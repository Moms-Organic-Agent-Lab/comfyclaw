"""
ChatAgent — lightweight conversational LLM for the ComfyClaw chat panel.

Handles free-form user questions about the current workflow and general
ComfyUI assistance.  Streams response tokens back as an async generator.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

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


async def chat_stream(
    messages: list[dict],
    workflow: dict | None,
    model: str,
    api_key: str | None = None,
    api_base: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream LLM response tokens for the chat panel.

    Parameters
    ----------
    messages:
        Conversation history in OpenAI format.  The last entry should be
        the user's new message.
    workflow:
        Current API-format workflow dict (used as context).
    model:
        LiteLLM model string (e.g. ``"anthropic/claude-sonnet-4-5"``).
    api_key:
        Optional API key override; falls back to environment variables.
    api_base:
        Optional base URL override for custom/self-hosted endpoints.
    """
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
