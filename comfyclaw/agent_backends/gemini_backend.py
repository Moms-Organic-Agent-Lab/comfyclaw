"""
GeminiCLIBackend — drive Google's Gemini CLI (``gemini``) as the agent.

The Gemini CLI's headless mode is::

    gemini -p "<prompt>" [-m MODEL]

It does not natively expose a tool-use protocol, so we use the
JSON-envelope strategy from :mod:`._stream_session`.

Authentication: the CLI uses ``gemini auth`` or ``GEMINI_API_KEY``.
"""

from __future__ import annotations

import os
import shutil

from . import _stream_session
from .base import DispatchFn, EventFn

_GEMINI_BIN_ENV = "COMFYCLAW_GEMINI_BIN"


def _gemini_bin() -> str:
    return os.environ.get(_GEMINI_BIN_ENV, "").strip() or "gemini"


class GeminiCLIBackend:
    """Run the agent through the Gemini CLI in headless prompt mode."""

    name = "gemini-cli"

    def __init__(self, model: str = "") -> None:
        self.model = model
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
        model = self.model

        def _invoke(prompt: str) -> str:
            argv: list[str] = [bin_path, "-p", prompt]
            if model:
                argv += ["-m", model]
            rc, out, err = _stream_session.run_cli_oneshot(argv, "", timeout=420)
            if rc != 0 and not out:
                raise RuntimeError(f"gemini rc={rc}: {err[:200]}")
            return out or err

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
