"""
CodexBackend — drive OpenAI's Codex CLI (``codex``) as the agent.

Codex's headless mode is::

    codex exec [-m MODEL] [--json] "<prompt>"

It does NOT speak the Anthropic ``tool_use`` block protocol.  We
therefore use the JSON-envelope protocol from
:mod:`._stream_session` — the model is told to emit a strict JSON
envelope describing the tool calls it wants, and we echo the results
back on the next turn.

Authentication is the CLI's responsibility (``codex login`` or
``OPENAI_API_KEY`` in the environment).
"""

from __future__ import annotations

import os
import shutil

from . import _stream_session
from .base import DispatchFn, EventFn

_CODEX_BIN_ENV = "COMFYCLAW_CODEX_BIN"


def _codex_bin() -> str:
    return os.environ.get(_CODEX_BIN_ENV, "").strip() or "codex"


class CodexBackend:
    """Run the agent through the Codex CLI's headless ``exec`` mode."""

    name = "codex"

    def __init__(self, model: str = "") -> None:
        self.model = model
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
        model = self.model

        def _invoke(prompt: str) -> str:
            argv: list[str] = [bin_path, "exec", "--json"]
            if model:
                argv += ["-m", model]
            argv.append(prompt)
            rc, out, err = _stream_session.run_cli_oneshot(argv, "", timeout=420)
            if rc != 0 and not out:
                # codex sometimes prints prose to stderr on auth issues
                raise RuntimeError(f"codex rc={rc}: {err[:200]}")
            text = out or err
            # Codex emits structured JSON event lines. Concatenate any "agent"
            # message text (heuristic — accepts both flat and nested shapes).
            joined: list[str] = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("{"):
                    try:
                        import json as _j

                        evt = _j.loads(line)
                    except Exception:
                        continue
                    payload = (
                        evt.get("agent_message")
                        or evt.get("text")
                        or evt.get("delta")
                        or evt.get("content")
                    )
                    if isinstance(payload, str):
                        joined.append(payload)
                    elif isinstance(payload, dict):
                        nested = payload.get("text") or payload.get("content")
                        if isinstance(nested, str):
                            joined.append(nested)
                else:
                    joined.append(line)
            return "\n".join(joined) if joined else text

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
