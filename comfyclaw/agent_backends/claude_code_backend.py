"""
ClaudeCodeBackend — drive Anthropic's Claude Code CLI as the agent.

Strategy
--------
Claude Code supports a streaming JSON I/O mode via::

    claude -p --output-format stream-json --input-format stream-json

In that mode it reads JSON messages from stdin and writes events to
stdout, one JSON object per line.  Critically, it natively understands
the Anthropic ``tool_use`` / ``tool_result`` block format — so we can
publish our existing tool schema directly and let the CLI do the
tool-call routing for us.

We start one subprocess per ``run_tool_loop`` call, send an initial
``user`` message that contains the system prompt plus the request,
then for every ``tool_use`` block we receive we dispatch it locally
and stream the result back as a ``tool_result`` block.

Authentication, model selection, and rate limiting are entirely the
CLI's responsibility — the user must have ``claude`` set up
(``claude /login``) before running this backend.

Fallback path
-------------
If ``--output-format stream-json`` isn't supported by the installed
Claude Code version (older builds), the backend falls back to the
JSON-envelope protocol shared with Codex / Gemini via
:mod:`._stream_session`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading

from . import _stream_session
from .base import DispatchFn, EventFn, ToolCall

_CLAUDE_BIN_ENV = "COMFYCLAW_CLAUDE_BIN"


def _claude_bin() -> str:
    return os.environ.get(_CLAUDE_BIN_ENV, "").strip() or "claude"


# CLI aliases the `claude` binary accepts directly.
_CLAUDE_CLI_ALIASES = {"default", "sonnet", "opus", "haiku"}


def _normalise_claude_model(model: str) -> str:
    """
    Translate a LiteLLM-style model string (``anthropic/claude-sonnet-4-5``) into
    something the ``claude`` CLI accepts.

    Strategy:
        * Empty / falsy → empty string (caller will omit ``--model``).
        * Already a CLI alias (``sonnet``/``opus``/``haiku``/``default``) → kept.
        * Already a fully-qualified Anthropic model id (no ``/`` and starts with
          ``claude-`` or contains an 8-digit date) → kept verbatim.
        * Starts with ``anthropic/`` (or any provider prefix) → strip the prefix
          and try to map to a CLI alias by family keyword.
        * Otherwise → empty string so the CLI falls back to the user's default.
    """
    raw = (model or "").strip()
    if not raw:
        return ""
    if raw in _CLAUDE_CLI_ALIASES:
        return raw

    if "/" in raw:
        _, _, suffix = raw.partition("/")
    else:
        suffix = raw
    suffix = suffix.strip().lower()

    if not suffix:
        return ""
    if suffix in _CLAUDE_CLI_ALIASES:
        return suffix
    # Fully-qualified API model id (e.g. claude-sonnet-4-5-20250929) → keep as-is.
    if suffix.startswith("claude-") and suffix[-8:].isdigit():
        return suffix
    if "sonnet" in suffix:
        return "sonnet"
    if "opus" in suffix:
        return "opus"
    if "haiku" in suffix:
        return "haiku"
    return ""


class ClaudeCodeBackend:
    """Run the agent through the Claude Code CLI's streaming JSON mode."""

    name = "claude-code"

    def __init__(self, model: str = "") -> None:
        self.model = model
        self._cli_model = _normalise_claude_model(model)
        self._bin = _claude_bin()

    def is_available(self) -> bool:
        return shutil.which(self._bin) is not None

    # ------------------------------------------------------------------

    def run_tool_loop(
        self,
        system: str,
        user: str,
        tools: list[dict],
        dispatch: DispatchFn,
        on_event: EventFn | None = None,
        max_rounds: int = 40,
    ) -> str:
        if on_event:
            tag = self._cli_model or "(CLI default)"
            on_event(
                "info",
                f"Starting Claude Code session ({self._bin}) model={tag}",
                "",
                None,
            )
            if self.model and not self._cli_model:
                on_event(
                    "info",
                    f"Could not map model {self.model!r} to a Claude CLI alias; "
                    "falling back to the CLI's default model.",
                    "",
                    None,
                )

        # Claude Code's stream-json input format does not accept arbitrary
        # custom tool schemas — it only exposes its built-in ecosystem
        # (Bash/Edit/Read/etc.).  We therefore drive `claude` as a strict
        # JSON-envelope LLM via `_run_envelope`, which uses
        # `--bare --tools "" --system-prompt …` to bypass anti-injection
        # heuristics and force the model to speak our protocol exclusively.
        return _run_envelope(
            bin_path=self._bin,
            model=self._cli_model,
            system=system,
            user=user,
            tools=tools,
            dispatch=dispatch,
            on_event=on_event,
            max_rounds=max_rounds,
        )


# ---------------------------------------------------------------------------
# Native stream-json path
# ---------------------------------------------------------------------------


class _NativeUnsupported(RuntimeError):
    pass


def _run_native(
    *,
    bin_path: str,
    model: str,
    system: str,
    user: str,
    tools: list[dict],
    dispatch: DispatchFn,
    on_event: EventFn | None,
    max_rounds: int,
) -> str:
    argv: list[str] = [
        bin_path,
        "-p",
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--include-partial-messages",
    ]
    if model:
        argv += ["--model", model]

    # The `claude` script is `#!/usr/bin/env node` so co-locate its bin dir
    # on PATH; otherwise the kernel returns 127 before the script can start.
    from .base import _env_with_claude_path

    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=_env_with_claude_path(bin_path),
        )
    except FileNotFoundError as exc:
        raise _NativeUnsupported(f"binary not found: {exc}") from exc

    # Reader threads
    rationale = "(no rationale provided)"
    rounds = 0
    rationale_box = {"value": rationale, "rounds": 0}
    stop = threading.Event()

    # Pending tool calls indexed by tool_use_id
    pending: dict[str, dict] = {}

    # Initial system+user payload — Claude Code expects the system prompt
    # baked into the first user turn since `--system-prompt` isn't always
    # available.  Provide tools via the `tools` field if supported.
    init = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": f"{system}\n\n---\n\n{user}"}],
        },
        "tools": tools,
    }

    try:
        proc.stdin.write(json.dumps(init) + "\n")
        proc.stdin.flush()
    except Exception as exc:  # noqa: BLE001
        proc.kill()
        raise _NativeUnsupported(f"stdin write failed: {exc}") from exc

    def _drain_stderr():
        for line in iter(proc.stderr.readline, ""):
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            # First non-empty stderr line that mentions an unknown flag means
            # this Claude Code version doesn't support stream-json -> bail.
            if "unknown" in line.lower() and (
                "--output-format" in line or "--input-format" in line
            ):
                raise _NativeUnsupported(line)
            if line and on_event:
                on_event("info", f"[claude stderr] {line[:160]}", "", None)

    err_thread = threading.Thread(target=_drain_stderr, daemon=True)
    err_thread.start()

    def _send_tool_result(tool_use_id: str, content: str, is_error: bool = False) -> None:
        msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": content,
                        "is_error": is_error,
                    }
                ],
            },
        }
        try:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            print(f"[ClaudeCodeBackend] send_tool_result failed: {exc}", file=sys.stderr)

    saw_any = False
    try:
        for raw_line in iter(proc.stdout.readline, ""):
            if stop.is_set():
                break
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                msg = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            saw_any = True

            mtype = msg.get("type")
            if mtype == "assistant":
                msg_data = msg.get("message", {})
                for blk in msg_data.get("content", []) or []:
                    btype = blk.get("type")
                    if btype == "text":
                        txt = blk.get("text") or ""
                        if txt and on_event:
                            on_event("thinking", txt, "", None)
                    elif btype == "tool_use":
                        nm = blk.get("name", "")
                        tu_id = blk.get("id", "")
                        args = blk.get("input") or {}
                        if not isinstance(args, dict):
                            args = {}
                        pending[tu_id] = {"name": nm, "args": args}
                        if on_event:
                            on_event("tool_call", f"Calling {nm}", nm, args)

                        try:
                            result_text, should_stop = dispatch(
                                ToolCall(name=nm, args=args, call_id=tu_id)
                            )
                        except Exception as exc:  # noqa: BLE001
                            result_text = f"❌ Tool error: {exc}"
                            should_stop = False
                        if on_event:
                            on_event("tool_result", result_text[:300], nm, None)

                        _send_tool_result(tu_id, result_text)
                        if should_stop:
                            rationale_box["value"] = args.get("rationale", rationale_box["value"])
                            stop.set()
                            try:
                                proc.stdin.close()
                            except Exception:
                                pass
                            break
                rounds += 1
                rationale_box["rounds"] = rounds
                if rounds >= max_rounds:
                    stop.set()
                    break
            elif mtype == "result":
                # Final result event — Claude Code writes one of these
                # when the conversation ends.
                rationale_box["value"] = msg.get("result") or rationale_box["value"]
                stop.set()
                break
            elif mtype == "system" and msg.get("subtype") == "init":
                # Session started; nothing to do.
                pass
    finally:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not saw_any:
        raise _NativeUnsupported("no JSON output received from claude CLI")

    return rationale_box["value"]


# ---------------------------------------------------------------------------
# Envelope-protocol fallback
# ---------------------------------------------------------------------------


def _run_envelope(
    *,
    bin_path: str,
    model: str,
    system: str,
    user: str,
    tools: list[dict],
    dispatch: DispatchFn,
    on_event: EventFn | None,
    max_rounds: int,
) -> str:
    """Drive `claude` as a generic JSON-envelope LLM.

    Why we don't use Claude Code's native tool-use mode here:

    * Claude Code has its own built-in tool ecosystem (Bash/Edit/Read/etc.)
      and silently ignores arbitrary user-provided tool schemas.
    * Without ``--bare``/``--tools ""`` and an explicit ``--system-prompt``,
      Claude Code's anti-injection guard rejects pure JSON-envelope prompts.
    * ``--bare`` skips hooks/LSP/plugin discovery (huge speedup) and
      ``--tools ""`` disables every built-in tool so the model can only
      respond via the envelope we control.
    """

    full_system = system + _stream_session.envelope_protocol_instructions(tools)

    def _invoke(prompt: str) -> str:
        from .base import _env_with_claude_path

        argv = [
            bin_path,
            "-p",
            "--bare",
            "--tools",
            "",
            "--system-prompt",
            full_system,
        ]
        if model:
            argv += ["--model", model]
        rc, out, err = _stream_session.run_cli_oneshot(
            argv,
            prompt,
            timeout=300,
            env=_env_with_claude_path(bin_path),
        )
        if rc != 0 and not out:
            raise RuntimeError(f"claude rc={rc}: {err[:200]}")
        return out or err

    # We've already baked the system prompt into --system-prompt, so pass an
    # empty system to the envelope loop to avoid duplicating the protocol
    # instructions inside the user prompt.
    return _stream_session.run_envelope_loop(
        backend_name="claude-code",
        invoke=_invoke,
        system="",
        user=user,
        tools=[],  # tools list already encoded in full_system above
        dispatch=dispatch,
        on_event=on_event,
        max_rounds=max_rounds,
        protocol_in_system=True,
    )
