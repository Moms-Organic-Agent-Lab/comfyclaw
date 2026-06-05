"""
CodexBackend — drive OpenAI's Codex CLI (``codex``) as the agent.

Codex's headless mode is::

    codex exec --json --skip-git-repo-check --sandbox read-only [-m MODEL] "<prompt>"

It does NOT speak the Anthropic ``tool_use`` block protocol.  We
therefore use the JSON-envelope protocol from
:mod:`._stream_session` — the model is told to emit a strict JSON
envelope describing the tool calls it wants, and we echo the results
back on the next turn.

The event taxonomy we parse from ``codex exec --json`` is identical to
the one the official ``@openai/codex-sdk`` Node SDK exposes (see
``reference/openai-codex.js``): each line is a JSON object with a
``type`` field (``item.started`` / ``item.updated`` / ``item.completed``
/ ``turn.failed`` / ``error`` / etc.) and an ``item`` payload whose
``type`` is one of ``agent_message`` / ``reasoning`` / ``command_execution``
/ ``file_change`` / ``mcp_tool_call`` / ``web_search`` / ``todo_list``.
The model's actual text reply lives at ``item.completed`` events whose
``item.type == "agent_message"``; everything else is internal noise we
filter out before feeding text to the envelope parser.

Authentication is the CLI's responsibility (``codex login`` or
``OPENAI_API_KEY`` in the environment).
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

_CODEX_BIN_ENV = "COMFYCLAW_CODEX_BIN"
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,255}$")
_CODEX_SESSION_BY_KEY: dict[str, str] = {}
_CODEX_SESSION_LOCK = threading.Lock()


def _codex_bin() -> str:
    return os.environ.get(_CODEX_BIN_ENV, "").strip() or "codex"


def _extract_agent_message_text(line: str) -> str:
    """Pull the agent's text reply out of one ``codex exec --json`` event line.

    Returns the message text for ``item.completed`` events with
    ``item.type == "agent_message"``; an empty string otherwise.  Older
    codex builds that flattened the event into ``{"agent_message": …}``
    are still picked up via the legacy fallback so the same module
    works across CLI versions.
    """
    line = line.strip()
    if not line or not line.startswith("{"):
        return ""
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return ""

    etype = (evt.get("type") or "").lower()
    if etype == "item.completed":
        item = evt.get("item") or {}
        if (item.get("type") or "").lower() == "agent_message":
            text = item.get("text") or ""
            return text if isinstance(text, str) else ""
        return ""

    # Legacy flat-shape codex builds (pre-SDK schema).
    payload = evt.get("agent_message") or evt.get("text") or evt.get("delta") or evt.get("content")
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        nested = payload.get("text") or payload.get("content")
        if isinstance(nested, str):
            return nested
    return ""


def _is_turn_failure(line: str) -> tuple[bool, str]:
    """Detect ``turn.failed``/``error`` events and return their message."""
    line = line.strip()
    if not line.startswith("{"):
        return False, ""
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return False, ""
    etype = (evt.get("type") or "").lower()
    if etype not in {"turn.failed", "error"}:
        return False, ""
    err = evt.get("error") or evt.get("message") or ""
    if isinstance(err, dict):
        err = err.get("message") or err.get("error") or json.dumps(err)
    return True, str(err)


def _extract_codex_session_id(line: str) -> str:
    """Best-effort extraction of Codex's persisted conversation id."""
    line = line.strip()
    if not line.startswith("{"):
        return ""
    try:
        evt = json.loads(line)
    except json.JSONDecodeError:
        return ""

    def _valid_session_id(value: str) -> str:
        value = value.strip()
        m = _UUID_RE.search(value)
        if m:
            return m.group(0)
        return value if _SESSION_ID_RE.match(value) else ""

    direct_keys = (
        "session_id",
        "conversation_id",
        "thread_id",
        "rollout_id",
    )
    stack = [evt]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key in direct_keys:
                val = cur.get(key)
                if isinstance(val, str):
                    sid = _valid_session_id(val)
                    if sid:
                        return sid
            for parent_key in ("session", "conversation", "thread"):
                nested = cur.get(parent_key)
                if isinstance(nested, dict):
                    val = nested.get("id")
                    if isinstance(val, str):
                        sid = _valid_session_id(val)
                        if sid:
                            return sid
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return ""


def _get_recorded_codex_session(session_key: str) -> str:
    if not session_key:
        return ""
    with _CODEX_SESSION_LOCK:
        return _CODEX_SESSION_BY_KEY.get(session_key, "")


def _record_codex_session(session_key: str, codex_session_id: str) -> None:
    if not session_key or not codex_session_id:
        return
    with _CODEX_SESSION_LOCK:
        _CODEX_SESSION_BY_KEY[session_key] = codex_session_id


class CodexBackend:
    """Run the agent through the Codex CLI's headless ``exec`` mode."""

    name = "codex"

    def __init__(self, model: str = "", session_key: str = "") -> None:
        self.model = model
        self.session_key = session_key
        self._bin = _codex_bin()

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

        # Mirror the ``threadOptions`` the @openai/codex-sdk reference passes
        # (see ``reference/openai-codex.js`` ~L395):
        #   skipGitRepoCheck: true  → --skip-git-repo-check  (without this,
        #                             codex blocks on a "not a git repo,
        #                             continue?" prompt when launched outside
        #                             a git repo and stdin is non-interactive)
        #   sandboxMode             → --sandbox read-only    (the envelope
        #                             protocol means *we* dispatch the tools,
        #                             codex only needs to emit text)
        # ``codex exec`` already implies a non-interactive ``approvalPolicy:
        # never``, so no explicit flag for that.
        exec_argv: list[str] = [
            bin_path,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
        ]
        # Codex's allowed-model list is bound to the user's ChatGPT
        # subscription.  It accepts CLI ids such as ``gpt-5.5`` and
        # ``gpt-5.4``, while plain ``gpt-5`` is rejected.  We share the
        # resolver used by the chat path so old saved UI selections are
        # translated to a supported Codex CLI id.  ``-c model=…`` beats
        # any value in the user's ``~/.codex/config.toml`` for *this*
        # invocation only.
        from comfyclaw.chat_agent import _codex_pick_model

        codex_model = _codex_pick_model(self.model)
        exec_argv += ["-c", f'model="{codex_model}"']

        def _argv_for_prompt(prompt: str) -> list[str]:
            codex_session_id = _get_recorded_codex_session(self.session_key)
            if codex_session_id:
                return [
                    bin_path,
                    "exec",
                    "resume",
                    "--json",
                    "--skip-git-repo-check",
                    "-c",
                    f'model="{codex_model}"',
                    codex_session_id,
                    prompt,
                ]
            return exec_argv + [prompt]

        # Mute codex's internal log surfaces — these otherwise leak into
        # the user's agent log as scary "ERROR codex_core::session:
        # failed to record rollout items" lines that have nothing to do
        # with the request.
        env = {
            **os.environ,
            "RUST_LOG": "off",
            "CODEX_LOG_LEVEL": "error",
            "NO_COLOR": "1",
        }

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
                raise RuntimeError(f"codex not on PATH: {exc}") from exc

            agent_text_parts: list[str] = []
            turn_error: str = ""

            # Drain stderr concurrently so codex can't stall on a full
            # pipe.  We only keep its tail to enrich error messages.
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
                target=_drain_stderr, daemon=True, name="codex-stderr-drain"
            )
            stderr_thread.start()

            # Stream stdout line-by-line.  Forward "thinking" updates to
            # ``on_event`` so the agent log shows progress instead of a
            # frozen "Starting codex session…" placeholder while codex
            # generates its reply.
            assert proc.stdout is not None
            try:
                for raw in iter(proc.stdout.readline, ""):
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    failed, err = _is_turn_failure(line)
                    if failed:
                        turn_error = err
                        continue
                    codex_session_id = _extract_codex_session_id(line)
                    if codex_session_id:
                        _record_codex_session(self.session_key, codex_session_id)
                    text = _extract_agent_message_text(line)
                    if not text:
                        continue
                    agent_text_parts.append(text)
                    if on_event:
                        preview = text.strip().splitlines()[0] if text.strip() else ""
                        on_event(
                            "thinking",
                            preview[:160] + ("…" if len(preview) > 160 else ""),
                            "",
                            None,
                        )
            finally:
                try:
                    rc = proc.wait(timeout=420)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = -9
                stderr_done.wait(timeout=2.0)

            if turn_error:
                raise RuntimeError(f"codex turn failed: {turn_error}")

            if rc != 0 and not agent_text_parts:
                tail = "\n".join(stderr_lines[-12:]).strip() or "no stderr"
                raise RuntimeError(f"codex rc={rc}: {tail[:300]}")

            # The LAST ``agent_message`` is the model's actual reply —
            # earlier ones are intermediate thoughts that codex emits
            # while reasoning through its own internal turn loop, and
            # joining them would shove non-envelope text in front of the
            # JSON envelope and break the parser.
            return agent_text_parts[-1].strip() if agent_text_parts else ""

        return _stream_session.run_envelope_loop(
            backend_name="codex",
            invoke=_invoke,
            system=system,
            user=user,
            tools=tools,
            dispatch=dispatch,
            on_event=on_event,
            max_rounds=max_rounds,
        )
