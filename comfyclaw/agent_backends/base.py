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

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


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


@dataclass
class BackendStatus:
    name: str
    available: bool
    binary_path: str = ""
    detail: str = ""


def probe_all() -> list[BackendStatus]:
    """Return availability for every registered backend.

    The result is suitable for sending to the panel as a ``backend_status``
    WS message so the user can see which CLIs are installed.
    """
    out = [BackendStatus(name="litellm", available=True, detail="Always available")]
    for cli, key in (
        ("claude", "claude-code"),
        ("codex", "codex"),
        ("gemini", "gemini-cli"),
    ):
        path = shutil.which(cli) or ""
        out.append(
            BackendStatus(
                name=key,
                available=bool(path),
                binary_path=path,
                detail=("" if path else f"`{cli}` not on PATH"),
            )
        )
    return out
