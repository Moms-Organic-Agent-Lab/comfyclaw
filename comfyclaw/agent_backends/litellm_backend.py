"""
LiteLLMBackend — wraps LiteLLM's OpenAI-compatible completion API.

This is the default backend.  It speaks every provider supported by
LiteLLM — Anthropic, OpenAI, Gemini, Groq, Ollama, OpenRouter, Mistral,
Together, etc. — using the unified ``litellm.completion(...)`` call.

The implementation is the original tool-use loop that previously lived
inside :class:`comfyclaw.agent.ClawAgent.plan_and_patch`, lifted here
so the harness can swap in a CLI-driven backend without touching the
dispatcher.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .base import DispatchFn, EventFn, ToolCall


def _abbreviate_tool_args(args: dict, max_val: int = 120) -> dict:
    out: dict = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > max_val:
            out[k] = v[:max_val] + "…"
        elif isinstance(v, dict) and len(json.dumps(v)) > max_val:
            out[k] = "{…}"
        else:
            out[k] = v
    return out


class LiteLLMBackend:
    """Default backend; uses ``litellm.completion`` with OpenAI tool format."""

    name = "litellm"

    def __init__(
        self,
        model: str = "anthropic/claude-sonnet-4-5",
        api_key: str = "",
        api_base: str | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self.model = model
        self.api_key = api_key or ""
        self.api_base = api_base or None
        self.max_tokens = max_tokens

    def is_available(self) -> bool:  # noqa: D401 — protocol method
        return True

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
        import litellm  # lazy

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        rationale = "(no rationale provided)"
        rounds = 0

        if on_event:
            on_event("info", f"Starting agent loop (model: {self.model})", "", None)

        while rounds < max_rounds:
            rounds += 1
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "tools": tools,
                "messages": messages,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base

            resp = litellm.completion(**kwargs)
            choice = resp.choices[0]
            finish_reason = choice.finish_reason
            assistant_msg = choice.message
            messages.append(assistant_msg)

            if assistant_msg.content and on_event:
                on_event("thinking", assistant_msg.content, "", None)

            if finish_reason in ("stop", "end_turn"):
                if on_event:
                    on_event("info", "Agent finished planning.", "", None)
                break
            if finish_reason != "tool_calls":
                print(
                    f"[LiteLLMBackend] Unexpected finish_reason: {finish_reason!r}",
                    file=sys.stderr,
                )
                break

            done = False
            for tc in assistant_msg.tool_calls or []:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                if on_event:
                    on_event(
                        "tool_call",
                        f"Calling {name}",
                        name,
                        _abbreviate_tool_args(args),
                    )

                call = ToolCall(name=name, args=args, call_id=tc.id)
                result_text, should_stop = dispatch(call)

                if on_event:
                    on_event("tool_result", result_text[:300], name, None)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    }
                )
                if should_stop:
                    rationale = args.get("rationale", rationale)
                    if on_event:
                        on_event("info", f"Finalized: {rationale[:200]}", "", None)
                    done = True

            if done:
                break

        return rationale
