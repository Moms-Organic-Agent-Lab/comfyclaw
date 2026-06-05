"""
GeminiCLIBackend — drive Google's Gemini CLI (``gemini``) as the agent.

The Gemini CLI's headless mode is::

    gemini -p "<prompt>" [-m MODEL]

It does not natively expose a tool-use protocol, so we use the
JSON-envelope strategy from :mod:`._stream_session`.

Authentication: the CLI uses ``gemini auth`` or ``GEMINI_API_KEY``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading

from . import _stream_session
from .base import DispatchFn, EventFn

_GEMINI_BIN_ENV = "COMFYCLAW_GEMINI_BIN"
_GEMINI_SESSION_BY_KEY: dict[str, str] = {}
_GEMINI_SESSION_LOCK = threading.Lock()
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,255}$")


def _gemini_bin() -> str:
    return os.environ.get(_GEMINI_BIN_ENV, "").strip() or "gemini"


def _get_recorded_gemini_session(session_key: str) -> str:
    if not session_key:
        return ""
    with _GEMINI_SESSION_LOCK:
        return _GEMINI_SESSION_BY_KEY.get(session_key, "")


def _record_gemini_session(session_key: str, gemini_session_id: str) -> None:
    if not session_key or not gemini_session_id or not _SESSION_ID_RE.match(gemini_session_id):
        return
    with _GEMINI_SESSION_LOCK:
        _GEMINI_SESSION_BY_KEY[session_key] = gemini_session_id


def _extract_stream_json_text(line: str, session_key: str = "") -> str:
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return line
    if not isinstance(evt, dict):
        return line

    etype = str(evt.get("type") or "").lower()
    if not etype:
        return line
    if etype in {"init", "session"}:
        sid = evt.get("session_id") or evt.get("id")
        if isinstance(sid, str):
            _record_gemini_session(session_key, sid)
        return ""

    if etype == "message":
        if str(evt.get("role") or "").lower() == "user":
            return ""
        content = evt.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                part
                if isinstance(part, str)
                else str(part.get("text") or "") if isinstance(part, dict) else ""
                for part in content
            )
        return ""

    if etype in {"content", "chunk"}:
        if str(evt.get("role") or "").lower() == "user":
            return ""
        text = evt.get("text") or evt.get("content") or evt.get("delta")
        return text if isinstance(text, str) else ""

    if etype == "error":
        message = evt.get("message") or evt.get("error")
        return f"Gemini error: {message}" if message else ""

    return ""


class GeminiCLIBackend:
    """Run the agent through the Gemini CLI in headless prompt mode."""

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
        def _argv_for_prompt(prompt: str) -> list[str]:
            argv: list[str] = [bin_path]
            sid = _get_recorded_gemini_session(self.session_key)
            if sid:
                argv += ["--resume", sid]
            if gemini_model:
                argv += ["-m", gemini_model]
            return argv + ["--prompt", prompt, "--output-format", "stream-json"]

        # ``NO_COLOR`` keeps ANSI styling out of stdout so we don't have
        # to strip it before feeding the text to the envelope parser.
        env = {**os.environ, "NO_COLOR": "1"}

        def _invoke(prompt: str) -> str:
            argv = _argv_for_prompt(prompt)
            try:
                proc = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(f"gemini not on PATH: {exc}") from exc

            out_parts: list[str] = []
            stderr_lines: list[str] = []
            stderr_done = threading.Event()

            def _drain_stderr() -> None:
                assert proc.stderr is not None
                try:
                    for raw in iter(proc.stderr.readline, ""):
                        if raw.strip():
                            stderr_lines.append(raw.rstrip("\n"))
                finally:
                    stderr_done.set()

            stderr_thread = threading.Thread(
                target=_drain_stderr, daemon=True, name="gemini-stderr-drain"
            )
            stderr_thread.start()

            # Gemini often pretty-prints the JSON envelope one line at a time.
            # Do not surface raw stdout as thinking events: it creates noisy
            # one-character blocks like "}" in the UI. The shared envelope loop
            # emits clean rationale / tool events after parsing.
            if on_event:
                on_event("info", "Gemini is preparing a tool plan…", "", None)
            assert proc.stdout is not None
            try:
                for raw in iter(proc.stdout.readline, ""):
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    text = _extract_stream_json_text(line, self.session_key)
                    if text:
                        out_parts.append(text)
            finally:
                try:
                    rc = proc.wait(timeout=420)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = -9
                stderr_done.wait(timeout=2.0)

            text = "".join(out_parts).strip()
            if rc != 0 and not text:
                tail = "\n".join(stderr_lines[-12:]).strip() or "no stderr"
                raise RuntimeError(f"gemini rc={rc}: {tail[:300]}")
            return text

        return _stream_session.run_envelope_loop(
            backend_name="gemini-cli",
            invoke=_invoke,
            system=system,
            user=user,
            tools=tools,
            dispatch=dispatch,
            on_event=on_event,
            max_rounds=max_rounds,
        )
