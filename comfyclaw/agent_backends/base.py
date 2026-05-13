"""
AgentBackend — abstract interface for the agent's tool-use loop.

Each backend takes a system prompt, a user message, a tool schema, and a
``dispatch`` callable that maps tool name + args to a result string.  The
backend is responsible for running the multi-round conversation until the
LLM either signals "done" (e.g. ``finalize_workflow``) or hits the round
budget.

Backends emit lightweight events through ``on_event`` so the UI can show
thinking / tool-call / tool-result entries in real time.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class ToolCall:
    """One tool invocation issued by the LLM.

    Attributes
    ----------
    name     : Tool name as declared in the tool schema.
    args     : Parsed JSON arguments (always a dict, possibly empty).
    call_id  : Opaque round-trip id the backend uses to match the reply.
               Different providers use different id formats; the harness
               never inspects this — it just round-trips it back.
    """

    name: str
    args: dict
    call_id: str = ""


# Type aliases for callback signatures
DispatchFn = Callable[[ToolCall], "tuple[str, bool]"]
"""Dispatcher: ``(call) -> (result_text, should_stop)``.

``should_stop`` is ``True`` when the call corresponds to a terminal tool
such as ``finalize_workflow`` — the backend should exit the loop after
delivering the result back to the model.
"""

EventFn = Callable[[str, str, str, "dict | None"], None]
"""Event emitter: ``(event_type, content, tool_name, tool_args_or_None)``."""


@runtime_checkable
class AgentBackend(Protocol):
    """Pluggable driver for the tool-use loop."""

    #: Stable lowercase identifier used in HarnessConfig.agent_backend.
    name: str

    def is_available(self) -> bool:
        """Return True if this backend can run in the current environment.

        For LiteLLM this is always True (any provider with an API key
        works).  For CLI backends this checks that the required binary
        is on ``$PATH``.
        """
        ...

    def run_tool_loop(
        self,
        system: str,
        user: str,
        tools: list[dict],
        dispatch: DispatchFn,
        on_event: EventFn | None = None,
        max_rounds: int = 40,
    ) -> str:
        """Run the LLM tool-use conversation to completion.

        Parameters
        ----------
        system     : System prompt string.
        user       : User message string.
        tools      : Tool schema in OpenAI function-calling format.
        dispatch   : Callable that executes a tool call and returns
                     ``(result_text, should_stop)``.
        on_event   : Optional listener for thinking / tool-call /
                     tool-result events.
        max_rounds : Hard cap on tool-call rounds.

        Returns
        -------
        Final rationale string emitted by the model (typically the
        ``rationale`` argument of ``finalize_workflow``, or any trailing
        assistant text).
        """
        ...


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_backend(
    name: str,
    *,
    model: str = "",
    api_key: str = "",
    api_base: str | None = None,
    extra: dict[str, Any] | None = None,
) -> AgentBackend:
    """Return a backend instance by name.

    Falls back to LiteLLM with a printed warning if the requested CLI
    backend's binary is missing.
    """
    name = (name or "litellm").strip().lower().replace("_", "-")
    extra = extra or {}

    if name == "litellm":
        from .litellm_backend import LiteLLMBackend

        return LiteLLMBackend(model=model, api_key=api_key, api_base=api_base)

    if name in ("claude-code", "claude"):
        from .claude_code_backend import ClaudeCodeBackend

        be = ClaudeCodeBackend(model=model or extra.get("model", ""))
        if be.is_available():
            return be
        print(f"[agent_backends] '{name}' CLI not found on PATH — falling back to litellm.")
    elif name in ("codex", "openai-codex"):
        from .codex_backend import CodexBackend

        be = CodexBackend(model=model or extra.get("model", ""))
        if be.is_available():
            return be
        print(f"[agent_backends] '{name}' CLI not found on PATH — falling back to litellm.")
    elif name in ("gemini-cli", "gemini"):
        from .gemini_backend import GeminiCLIBackend

        be = GeminiCLIBackend(model=model or extra.get("model", ""))
        if be.is_available():
            return be
        print(f"[agent_backends] '{name}' CLI not found on PATH — falling back to litellm.")
    else:
        print(f"[agent_backends] Unknown backend {name!r} — falling back to litellm.")

    from .litellm_backend import LiteLLMBackend

    return LiteLLMBackend(model=model, api_key=api_key, api_base=api_base)


# ---------------------------------------------------------------------------
# Availability probes (used by sync_server to populate the panel UI)
# ---------------------------------------------------------------------------


BackendState = Literal["ok", "needs_install", "needs_auth", "error", "unsupported"]


@dataclass
class BackendStatus:
    name: str
    # ``available`` is kept for backward compatibility with older panels.
    # New panels read ``state`` and treat anything other than ``"ok"`` as
    # requiring user action.
    available: bool
    state: BackendState = "ok"
    binary_path: str = ""
    auth_method: str = ""  # e.g. "claudeai" | "console" | ""
    detail: str = ""
    # When True, the panel may surface an "Install" affordance for this
    # backend (currently only claude-code).
    can_install: bool = False


# Search list for the `claude` binary when it isn't on $PATH. Order matters —
# we prefer locations under the user's home so we don't accidentally pick a
# system-wide install we can't refresh later.
_CLAUDE_PATH_HINTS: tuple[str, ...] = (
    "~/.local/share/fnm/node-versions/*/installation/bin/claude",
    "~/.local/bin/claude",
    "~/.npm-global/bin/claude",
    "~/.nvm/versions/node/*/bin/claude",
    "~/.volta/bin/claude",
    "/opt/local/bin/claude",
    "/usr/local/bin/claude",
)


def _resolve_claude_bin() -> str:
    """Find a usable `claude` binary even if it isn't on $PATH.

    Resolution order:
      1. ``$COMFYCLAW_CLAUDE_BIN`` if set and executable.
      2. ``shutil.which("claude")``.
      3. Common install locations (fnm, nvm, ~/.local/bin, /usr/local/bin, …).
    """
    override = os.environ.get("COMFYCLAW_CLAUDE_BIN", "").strip()
    if override and os.access(override, os.X_OK):
        return override

    via_path = shutil.which("claude")
    if via_path:
        return via_path

    for pattern in _CLAUDE_PATH_HINTS:
        for candidate in glob.glob(os.path.expanduser(pattern)):
            if os.access(candidate, os.X_OK):
                return candidate
    return ""


def _env_with_claude_path(binary: str) -> dict[str, str]:
    """Return an env dict where the claude binary's directory is on PATH.

    Necessary because the Claude Code CLI is a node script (``#!/usr/bin/env
    node``); if ``node`` itself isn't on PATH the kernel returns exit 127
    with ``/usr/bin/env: 'node': No such file or directory`` *before*
    the script can even start.  We co-locate node + claude in the same
    bin dir under fnm/nvm/npm-global, so prepending that dir to PATH is
    enough to make both reachable to the kernel and to the script.
    """
    env = {**os.environ}
    binary_dir = os.path.dirname(binary) if binary else ""
    if binary_dir:
        existing = env.get("PATH", "")
        if binary_dir not in existing.split(os.pathsep):
            env["PATH"] = (
                binary_dir + (os.pathsep + existing if existing else "")
            )
    return env


def _probe_claude_auth(binary: str) -> tuple[BackendState, str, str]:
    """Run ``claude auth status --json`` and parse the result.

    Returns ``(state, auth_method, detail)`` where ``state`` is one of
    ``"ok"`` / ``"needs_auth"`` / ``"error"``.
    """
    try:
        proc = subprocess.run(
            [binary, "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            env=_env_with_claude_path(binary),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return "error", "", f"auth probe failed: {exc}"
    except Exception as exc:  # noqa: BLE001
        return "error", "", f"auth probe failed: {exc}"

    raw = (proc.stdout or "").strip() or (proc.stderr or "").strip()

    # Exit 127 with no/garbled stdout usually means the shebang interpreter
    # (node) isn't on PATH.  Surface that as a real error rather than
    # silently "needs_auth" so the panel can show a useful message.
    if proc.returncode == 127:
        combined = ((proc.stderr or "") + " " + (proc.stdout or "")).lower()
        if "node" in combined and ("no such file" in combined or "not found" in combined):
            return (
                "error",
                "",
                "Claude Code is installed but its `node` runtime can't be "
                "found on PATH. Add the binary's directory to PATH.",
            )

    if not raw:
        # Non-zero with no output: treat as unauthenticated rather than error
        # so the panel still surfaces a Sign-in affordance.
        if proc.returncode != 0:
            return "needs_auth", "", "Not signed in"
        return "error", "", "auth probe returned no output"

    # Strip ANSI codes some CLI builds emit even with --json.
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        if "logged in" in raw.lower() or "loggedin" in raw.lower():
            return "ok", "", ""
        return "needs_auth", "", "Not signed in"

    logged_in = bool(payload.get("loggedIn"))
    auth_method = str(payload.get("authMethod") or "")
    if logged_in:
        detail = f"Signed in via {auth_method}" if auth_method and auth_method != "none" else "Signed in"
        return "ok", auth_method, detail
    return "needs_auth", auth_method, "Not signed in"


def probe_all() -> list[BackendStatus]:
    """Return availability for every registered backend.

    Suitable for sending to the panel as the ``agent_backends`` WS message
    so the user can see which CLIs are installed and authenticated.
    """
    out: list[BackendStatus] = [
        BackendStatus(
            name="litellm",
            available=True,
            state="ok",
            detail="Always available",
        )
    ]

    # ── claude-code ──────────────────────────────────────────────────────────
    claude_bin = _resolve_claude_bin()
    if not claude_bin:
        out.append(
            BackendStatus(
                name="claude-code",
                available=False,
                state="needs_install",
                detail="Claude Code is not installed",
                can_install=True,
            )
        )
    else:
        state, auth_method, detail = _probe_claude_auth(claude_bin)
        out.append(
            BackendStatus(
                name="claude-code",
                available=(state == "ok"),
                state=state,
                binary_path=claude_bin,
                auth_method=auth_method,
                detail=detail or f"`claude` at {claude_bin}",
                can_install=True,
            )
        )

    # ── codex / gemini-cli — binary-presence only ────────────────────────────
    for cli, key in (("codex", "codex"), ("gemini", "gemini-cli")):
        path = shutil.which(cli) or ""
        out.append(
            BackendStatus(
                name=key,
                available=bool(path),
                state="ok" if path else "unsupported",
                binary_path=path,
                detail=("" if path else f"`{cli}` not on PATH"),
                can_install=False,
            )
        )
    return out
