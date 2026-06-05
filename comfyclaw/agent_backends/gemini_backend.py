"""
GeminiCLIBackend — drive Google's Gemini CLI (``gemini``) as the agent.

Modern Gemini CLI builds expose ACP (Agent Client Protocol) over stdio::

    gemini --acp

ACP is a long-lived, bidirectional NDJSON/JSON-RPC channel.  We keep one
process alive and map each ComfyClaw session to an ACP session, so follow-up
messages continue the same Gemini chat without paying CLI startup cost every
turn.  Gemini still does not consume ComfyClaw's local tool schema natively, so
we keep using the JSON-envelope strategy from :mod:`._stream_session` for
workflow tool calls.

Authentication: the CLI uses ``gemini auth`` or ``GEMINI_API_KEY``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from queue import Queue

from . import _stream_session
from .base import DispatchFn, EventFn

_GEMINI_BIN_ENV = "COMFYCLAW_GEMINI_BIN"
_ACP_CLIENTS: dict[tuple[str, str], _GeminiAcpClient] = {}
_ACP_CLIENTS_LOCK = threading.Lock()


def _gemini_bin() -> str:
    return os.environ.get(_GEMINI_BIN_ENV, "").strip() or "gemini"


@dataclass
class _AcpSessionState:
    acp_session_id: str
    model_applied: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)
    text_chunks: list[str] = field(default_factory=list)


class _GeminiAcpClient:
    """Small client-side ACP transport for ``gemini --acp``.

    The Gemini CLI bundle uses newline-delimited JSON, not LSP-style
    ``Content-Length`` framing.  Reader and writer stay alive for the whole
    Python process; individual prompt calls only wait on their request id.
    """

    def __init__(self, bin_path: str, cwd: str) -> None:
        self.bin_path = bin_path
        self.cwd = cwd
        self._proc: subprocess.Popen | None = None
        self._stderr_lines: list[str] = []
        self._request_id = 0
        self._write_lock = threading.Lock()
        self._pending: dict[int, Queue] = {}
        self._sessions_by_key: dict[str, _AcpSessionState] = {}
        self._sessions_by_acp_id: dict[str, _AcpSessionState] = {}
        self._session_updates: dict[str, list[dict]] = defaultdict(list)
        self._initialized = False
        self._auth_methods: list[dict] = []
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def has_session(self, session_key: str) -> bool:
        return bool(session_key and session_key in self._sessions_by_key and self.is_alive())

    def prompt(
        self,
        *,
        session_key: str,
        prompt: str,
        model: str,
        on_event: EventFn | None = None,
        timeout: float = 600.0,
    ) -> str:
        self._ensure_started()
        state = self._ensure_session(session_key or "__default__", model)
        with state.lock:
            start_idx = len(state.text_chunks)
            self._send_request(
                "session/prompt",
                {
                    "sessionId": state.acp_session_id,
                    "prompt": [{"type": "text", "text": prompt}],
                },
                timeout=timeout,
            )
            text = "".join(state.text_chunks[start_idx:]).strip()
        if not text and on_event:
            on_event("info", "Gemini ACP turn ended without assistant text.", "", None)
        return text

    def _ensure_started(self) -> None:
        if self.is_alive() and self._initialized:
            return
        self._start_process()
        init = self._send_request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientInfo": {
                    "name": "comfyclaw",
                    "title": "ComfyClaw",
                    "version": "0.1.0",
                },
                "clientCapabilities": {
                    "auth": {"terminal": False},
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
            },
            timeout=30.0,
        )
        self._auth_methods = list(init.get("authMethods") or []) if isinstance(init, dict) else []
        self._initialized = True

    def _start_process(self) -> None:
        self._stop_process()
        env = {**os.environ, "NO_COLOR": "1"}
        try:
            self._proc = subprocess.Popen(
                [self.bin_path, "--acp"],
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"gemini not on PATH: {exc}") from exc
        self._pending.clear()
        self._request_id = 0
        self._initialized = False
        self._stderr_lines.clear()
        self._reader_thread = threading.Thread(
            target=self._read_stdout, daemon=True, name="gemini-acp-stdout"
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, daemon=True, name="gemini-acp-stderr"
        )
        self._reader_thread.start()
        self._stderr_thread.start()

    def _stop_process(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except Exception:
                self._proc.kill()
        self._proc = None
        self._sessions_by_key.clear()
        self._sessions_by_acp_id.clear()
        self._session_updates.clear()

    def _ensure_session(self, session_key: str, model: str) -> _AcpSessionState:
        state = self._sessions_by_key.get(session_key)
        if state:
            if model and state.model_applied != model:
                self._send_request(
                    "session/set_model",
                    {"sessionId": state.acp_session_id, "modelId": model},
                    timeout=30.0,
                )
                state.model_applied = model
            return state

        try:
            result = self._send_request(
                "session/new",
                {"cwd": self.cwd, "mcpServers": []},
                timeout=90.0,
            )
        except RuntimeError as exc:
            if "Authentication required" not in str(exc) and "auth" not in str(exc).lower():
                raise
            self._authenticate_best_effort()
            result = self._send_request(
                "session/new",
                {"cwd": self.cwd, "mcpServers": []},
                timeout=90.0,
            )

        sid = str((result or {}).get("sessionId") or "")
        if not sid:
            raise RuntimeError(f"gemini ACP did not return a session id: {result!r}")
        state = _AcpSessionState(acp_session_id=sid)
        self._sessions_by_key[session_key] = state
        self._sessions_by_acp_id[sid] = state
        if sid in self._session_updates:
            for params in self._session_updates.pop(sid):
                self._handle_session_update(params)
        if model:
            self._send_request(
                "session/set_model",
                {"sessionId": sid, "modelId": model},
                timeout=30.0,
            )
            state.model_applied = model
        return state

    def _authenticate_best_effort(self) -> None:
        method = self._pick_auth_method()
        if not method:
            raise RuntimeError("Gemini ACP authentication required, but no auth method is available.")
        method_id = str(method.get("id") or "")
        params: dict = {"methodId": method_id}
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if api_key and "api" in method_id.lower():
            params["_meta"] = {"api-key": api_key}
        self._send_request("authenticate", params, timeout=90.0)

    def _pick_auth_method(self) -> dict | None:
        if not self._auth_methods:
            return None
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if api_key:
            for method in self._auth_methods:
                mid = str(method.get("id") or "").lower()
                name = str(method.get("name") or "").lower()
                if "api" in mid or "api" in name:
                    return method
        for method in self._auth_methods:
            mid = str(method.get("id") or "").lower()
            name = str(method.get("name") or "").lower()
            if "oauth" in mid or "google" in mid or "google" in name or "login" in name:
                return method
        return self._auth_methods[0]

    def _send_request(self, method: str, params: dict | None, timeout: float) -> dict:
        proc = self._proc
        if not proc or proc.poll() is not None or proc.stdin is None:
            raise RuntimeError("gemini ACP process is not running")
        with self._write_lock:
            request_id = self._request_id
            self._request_id += 1
            q: Queue = Queue(maxsize=1)
            self._pending[request_id] = q
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": params or {},
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            proc.stdin.flush()
        try:
            response = q.get(timeout=timeout)
        except Exception as exc:
            self._pending.pop(request_id, None)
            tail = "\n".join(self._stderr_lines[-8:]).strip()
            detail = f"; stderr: {tail[:400]}" if tail else ""
            raise RuntimeError(f"gemini ACP request {method!r} timed out{detail}") from exc
        if not isinstance(response, dict):
            raise RuntimeError(f"gemini ACP returned invalid response for {method}: {response!r}")
        if "error" in response:
            err = response.get("error") or {}
            message = err.get("message") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"gemini ACP {method} failed: {message}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def _send_response(self, request_id: int | str | None, result: dict | None = None, error: dict | None = None) -> None:
        proc = self._proc
        if not proc or proc.poll() is not None or proc.stdin is None or request_id is None:
            return
        payload: dict = {"jsonrpc": "2.0", "id": request_id}
        if error:
            payload["error"] = error
        else:
            payload["result"] = result or {}
        with self._write_lock:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for raw in iter(self._proc.stdout.readline, ""):
            line = raw.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._handle_message(message)

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for raw in iter(self._proc.stderr.readline, ""):
            line = raw.rstrip("\n")
            if line:
                self._stderr_lines.append(line)

    def _handle_message(self, message: dict) -> None:
        if "id" in message and "method" not in message:
            pending = self._pending.pop(message.get("id"), None)
            if pending:
                pending.put(message)
            return
        method = str(message.get("method") or "")
        if method == "session/update":
            params = message.get("params") or {}
            if isinstance(params, dict):
                self._handle_session_update(params)
            return
        if "id" in message and method:
            self._handle_client_request(message)

    def _handle_client_request(self, message: dict) -> None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        if method == "session/request_permission":
            self._send_response(
                request_id,
                {
                    "outcome": {
                        "outcome": "cancelled",
                    }
                },
            )
            return
        self._send_response(
            request_id,
            error={"code": -32601, "message": f"ComfyClaw ACP client does not implement {method}"},
        )

    def _handle_session_update(self, params: dict) -> None:
        sid = str(params.get("sessionId") or "")
        update = params.get("update") or {}
        if not sid or not isinstance(update, dict):
            return
        state = self._sessions_by_acp_id.get(sid)
        if not state:
            self._session_updates[sid].append(params)
            return
        kind = str(update.get("sessionUpdate") or "")
        if kind != "agent_message_chunk":
            return
        content = update.get("content") or {}
        if isinstance(content, dict):
            text = content.get("text")
            if isinstance(text, str):
                state.text_chunks.append(text)


def _get_acp_client(bin_path: str, cwd: str) -> _GeminiAcpClient:
    key = (bin_path, cwd)
    with _ACP_CLIENTS_LOCK:
        client = _ACP_CLIENTS.get(key)
        if not client or not client.is_alive():
            client = _GeminiAcpClient(bin_path, cwd)
            _ACP_CLIENTS[key] = client
        return client


class GeminiCLIBackend:
    """Run the agent through a long-lived Gemini CLI ACP session."""

    name = "gemini-cli"

    def __init__(self, model: str = "", session_key: str = "") -> None:
        self.model = model
        self.session_key = session_key
        self._bin = _gemini_bin()

    def is_available(self) -> bool:
        return shutil.which(self._bin) is not None

    def run_tool_loop(
        self,
        system: str,
        user: str,
        tools: list[dict],
        dispatch: DispatchFn,
        on_event: EventFn | None = None,
        max_rounds: int = 40,
    ) -> str:
        bin_path = self._bin

        # Gemini's CLI tracks Google account capabilities — the
        # LiteLLM dropdown's model id (e.g. ``gemini/gemini-2.5-pro``)
        # doesn't necessarily match what the signed-in account is
        # entitled to.  Reuse the chat-side resolver so the UI's model
        # selection (or ``COMFYCLAW_GEMINI_MODEL``) is honoured when
        # set, and otherwise fall back to "no ``-m``" so the CLI picks
        # the plan default.  NB: ``-m`` must appear BEFORE ``-p``;
        # gemini's flag parser otherwise consumes the prompt as the
        # model value.
        from comfyclaw.chat_agent import _gemini_pick_model

        gemini_model = _gemini_pick_model(self.model)
        cwd = os.getcwd()
        client = _get_acp_client(bin_path, cwd)

        def _invoke(prompt: str) -> str:
            return client.prompt(
                session_key=self.session_key or "__default__",
                prompt=prompt,
                model=gemini_model,
                on_event=on_event,
            )

        has_native_session = client.has_session(self.session_key or "__default__")
        return _stream_session.run_envelope_loop(
            backend_name="gemini-cli",
            invoke=_invoke,
            system=system,
            user=user,
            tools=tools,
            dispatch=dispatch,
            on_event=on_event,
            max_rounds=max_rounds,
            start_message=(
                "Continuing Gemini session"
                if has_native_session
                else "Starting Gemini ACP session"
            ),
        )
