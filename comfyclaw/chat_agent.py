"""
ChatAgent — lightweight conversational LLM for the ComfyClaw chat panel.

Handles free-form user questions about the current workflow and general
ComfyUI assistance.  Streams response tokens back as an async generator.

Supports two backends:

* ``"litellm"`` (default) — direct API call via LiteLLM with an
  ``ANTHROPIC_API_KEY`` / equivalent env var.
* ``"claude-code"`` — drives the local ``claude`` CLI in non-interactive
  ``-p`` mode and streams JSON events back into tokens.  This avoids
  needing any API key for users who have already signed into Claude
  through the in-panel OAuth flow.

The dispatcher :func:`chat_stream` picks the backend based on the
``agent_backend`` argument so the WebSocket handler can route chat
requests through whatever the user selected in the picker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator

log = logging.getLogger(__name__)

_SYSTEM_BASE = """\
You are ComfyClaw, an expert ComfyUI workflow assistant embedded in the live
ComfyClaw plugin.  Your role is to answer questions about ComfyUI, explain
workflow topologies, suggest improvements, and help debug problems.

Guidelines:
- Be concise and actionable. Format node class names and parameters in `backticks`.
- When a user wants to modify the workflow (add LoRA, change sampler, etc.),
  describe what you would change, then remind them to click ▶ Generate in
  "Improve Current" mode so the agent applies it.
- If the user asks what is currently in the workflow, summarise it from the
  node list below.
"""


def _summarize_workflow(workflow: dict | None) -> str:
    if not workflow:
        return "\n\nCurrent workflow: (empty — no nodes yet)"
    lines: list[str] = []
    for nid, node in list(workflow.items())[:40]:
        ct = node.get("class_type", "?")
        scalar_inputs = {
            k: v
            for k, v in (node.get("inputs") or {}).items()
            if not isinstance(v, list) and len(str(v)) < 80
        }
        inp_str = ", ".join(f"{k}={v!r}" for k, v in list(scalar_inputs.items())[:3])
        lines.append(f"  [{nid}] {ct}" + (f"  ({inp_str})" if inp_str else ""))
    if len(workflow) > 40:
        lines.append(f"  … and {len(workflow) - 40} more nodes")
    return "\n\nCurrent workflow nodes:\n" + "\n".join(lines)


def _flatten_history(messages: list[dict]) -> tuple[str, str]:
    """Split chat history into (prior_transcript, latest_user_message).

    The Claude CLI's ``-p`` mode takes a single prompt string; we prepend
    earlier turns as a transcript so the model has the context.  The
    most recent ``user`` message becomes the actual prompt.
    """
    if not messages:
        return "", ""
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx < 0:
        return "", ""
    prior = messages[:last_user_idx]
    latest = str(messages[last_user_idx].get("content") or "")
    if not prior:
        return "", latest
    transcript_lines: list[str] = []
    for m in prior:
        role = m.get("role", "user")
        content = str(m.get("content") or "")
        label = {"user": "User", "assistant": "Assistant", "system": "System"}.get(role, role.title())
        transcript_lines.append(f"{label}: {content}")
    return "\n".join(transcript_lines), latest


# ─────────────────────────────────────────────────────────────────────────────
# Backend dispatch
# ─────────────────────────────────────────────────────────────────────────────


async def chat_stream(
    messages: list[dict],
    workflow: dict | None,
    model: str,
    api_key: str | None = None,
    api_base: str | None = None,
    agent_backend: str = "litellm",
) -> AsyncGenerator[str, None]:
    """Stream chat tokens from the selected backend.

    Parameters
    ----------
    messages, workflow:
        Conversation history and current workflow snapshot.
    model:
        Model id.  Used by the LiteLLM backend directly; the Claude
        backend translates it to a CLI alias if recognised.
    api_key, api_base:
        Only used by the LiteLLM backend.
    agent_backend:
        ``"litellm"`` (default) or ``"claude-code"``.  Anything else
        falls back to LiteLLM with a log warning.
    """
    backend = (agent_backend or "litellm").strip().lower().replace("_", "-")
    if backend in ("claude-code", "claude"):
        async for tok in _claude_chat_stream(messages, workflow, model):
            yield tok
        return
    if backend != "litellm":
        log.warning(
            "[chat] Unknown agent_backend %r — falling back to LiteLLM.",
            agent_backend,
        )
    async for tok in _litellm_chat_stream(messages, workflow, model, api_key, api_base):
        yield tok


# ─────────────────────────────────────────────────────────────────────────────
# LiteLLM backend (legacy)
# ─────────────────────────────────────────────────────────────────────────────


async def _litellm_chat_stream(
    messages: list[dict],
    workflow: dict | None,
    model: str,
    api_key: str | None,
    api_base: str | None,
) -> AsyncGenerator[str, None]:
    import litellm  # lazy import — not always needed

    system = _SYSTEM_BASE + _summarize_workflow(workflow)
    full_messages = [{"role": "system", "content": system}] + list(messages)

    kwargs: dict = {"model": model, "messages": full_messages, "stream": True}
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base

    response = await litellm.acompletion(**kwargs)
    async for chunk in response:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


# ─────────────────────────────────────────────────────────────────────────────
# Claude Code CLI backend
# ─────────────────────────────────────────────────────────────────────────────


def _claude_cli_model(model: str) -> str:
    """Translate a LiteLLM-style model string into a Claude CLI alias.

    Mirrors the heuristic in :mod:`.agent_backends.claude_code_backend`
    so chat uses the same model family as the agent does.
    """
    raw = (model or "").strip()
    if not raw:
        return ""
    if "/" in raw:
        _, _, suffix = raw.partition("/")
    else:
        suffix = raw
    suffix = suffix.lower()
    if suffix in {"default", "sonnet", "opus", "haiku"}:
        return suffix
    if suffix.startswith("claude-") and suffix[-8:].isdigit():
        return suffix
    if "sonnet" in suffix:
        return "sonnet"
    if "opus" in suffix:
        return "opus"
    if "haiku" in suffix:
        return "haiku"
    return ""


async def _claude_chat_stream(
    messages: list[dict],
    workflow: dict | None,
    model: str,
) -> AsyncGenerator[str, None]:
    """Drive ``claude -p`` for a single chat reply.

    Uses ``--output-format stream-json --include-partial-messages`` so we
    get incremental ``text_delta`` events from the CLI; falls back to
    final ``assistant`` content blocks if no partials arrive (e.g. older
    CLI builds that don't support partials).
    """
    from .agent_backends.base import _env_with_claude_path, _resolve_claude_bin

    binary = _resolve_claude_bin()
    if not binary:
        yield (
            "⚠️  Claude Code is not installed on the server. "
            "Open the backend picker and click **Install Claude Code**, "
            "then try again."
        )
        return

    system_prompt = _SYSTEM_BASE + _summarize_workflow(workflow)
    transcript, latest_user = _flatten_history(messages)
    if not latest_user:
        return
    if transcript:
        prompt = f"{transcript}\n\nUser: {latest_user}"
    else:
        prompt = latest_user

    argv = [
        binary,
        "-p",
        prompt,
        "--append-system-prompt",
        system_prompt,
        "--output-format",
        "stream-json",
        # stream-json requires --verbose on the CLI side; without it the
        # binary refuses to start with "requires --verbose".
        "--verbose",
        "--include-partial-messages",
        "--disable-slash-commands",
    ]
    cli_model = _claude_cli_model(model)
    if cli_model:
        argv += ["--model", cli_model]

    env = _env_with_claude_path(binary)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        yield f"⚠️  Could not launch claude: {exc}"
        return

    saw_partial = False
    final_text_blocks: list[str] = []
    try:
        assert proc.stdout is not None
        while True:
            raw_line = await proc.stdout.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            mtype = evt.get("type")
            if mtype == "partial_message":
                delta = evt.get("delta") or evt.get("event", {}).get("delta") or {}
                # Two shapes we've seen:
                #   {"type":"text_delta","text":"..."} — current SDK
                #   {"text":"..."}                       — older SDK
                dtype = delta.get("type") or "text_delta"
                txt = delta.get("text")
                if not txt and isinstance(delta.get("partial_json"), str):
                    # tool-use input streaming — irrelevant for chat
                    continue
                if dtype == "text_delta" and txt:
                    saw_partial = True
                    yield txt
            elif mtype == "assistant":
                msg = evt.get("message") or {}
                for blk in msg.get("content") or []:
                    if blk.get("type") == "text":
                        txt = blk.get("text") or ""
                        if txt:
                            final_text_blocks.append(txt)
            elif mtype == "result":
                # Final aggregate event.  When ``is_error`` is true it
                # carries a human-readable description (e.g. "Not logged
                # in · Please run /login") that the user needs to see.
                if evt.get("is_error"):
                    err = evt.get("result") or evt.get("error") or "unknown error"
                    if "not logged in" in str(err).lower():
                        yield (
                            "⚠️  Claude Code is not signed in. Open the "
                            "backend picker and click **Sign in to Claude**, "
                            "then try again."
                        )
                    else:
                        yield f"⚠️  Claude CLI error: {err}"
                    saw_partial = True  # suppress empty-output fallback
                elif not saw_partial and not final_text_blocks:
                    # Older builds without partials AND without assistant
                    # messages emit only this single ``result`` event.
                    txt = evt.get("result") or ""
                    if txt:
                        yield str(txt)
                        saw_partial = True
            elif mtype == "error":
                err = evt.get("error") or evt.get("message") or line
                yield f"\n\n⚠️  Claude CLI error: {err}"

        # Drain stderr if anything went wrong but no error event arrived.
        rc = await proc.wait()
        if not saw_partial and not final_text_blocks:
            err_bytes = await proc.stderr.read() if proc.stderr else b""
            err_text = err_bytes.decode("utf-8", errors="replace").strip()
            if rc != 0 or err_text:
                yield (
                    f"\n\n⚠️  Claude CLI exited with code {rc}."
                    + (f"\n\n```\n{err_text[-600:]}\n```" if err_text else "")
                )
        elif not saw_partial and final_text_blocks:
            # We only got the final assistant message (no partials).
            for blk in final_text_blocks:
                yield blk
    except asyncio.CancelledError:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        raise
