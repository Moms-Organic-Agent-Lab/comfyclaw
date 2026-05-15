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
import subprocess
import threading

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

        # Gemini's CLI tracks Google account capabilities — the
        # LiteLLM dropdown's model id (e.g. ``gemini/gemini-2.5-pro``)
        # doesn't necessarily match what the signed-in account is
        # entitled to.  Reuse the chat-side resolver so the UI's model
        # selection (or ``COMFYCLAW_GEMINI_MODEL``) is honoured when
        # set, and otherwise fall back to "no ``-m``" so the CLI picks
        # the plan default.  NB: ``-m`` must appear BEFORE ``-p``;
        # gemini's flag parser otherwise consumes the prompt as the
        # model value.
        from ..chat_agent import _gemini_pick_model

        gemini_model = _gemini_pick_model(self.model)
        if gemini_model:
            base_argv: list[str] = [bin_path, "-m", gemini_model, "-p"]
        else:
            base_argv = [bin_path, "-p"]

        # ``NO_COLOR`` keeps ANSI styling out of stdout so we don't have
        # to strip it before feeding the text to the envelope parser.
        env = {**os.environ, "NO_COLOR": "1"}

        def _invoke(prompt: str) -> str:
            argv = base_argv + [prompt]
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

            out_lines: list[str] = []
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

            # Stream stdout line by line, surfacing chunks as "thinking"
            # progress events so the agent log shows life while gemini
            # generates.  Gemini emits plain prose (no structured events).
            assert proc.stdout is not None
            try:
                for raw in iter(proc.stdout.readline, ""):
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    out_lines.append(line)
                    if on_event:
                        on_event(
                            "thinking",
                            line[:160] + ("…" if len(line) > 160 else ""),
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

            text = "\n".join(out_lines).strip()
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
