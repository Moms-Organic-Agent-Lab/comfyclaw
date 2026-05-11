"""Tests for the AgentBackend abstraction (Phase 1)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from comfyclaw.agent_backends import (
    AgentBackend,
    BackendStatus,
    LiteLLMBackend,
    ToolCall,
    get_backend,
    probe_all,
)

# ---------------------------------------------------------------------------
# Helpers — replicate test_agent's litellm response builders
# ---------------------------------------------------------------------------


def _tool_call(name: str, args: dict, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _resp_tool(*calls: SimpleNamespace) -> MagicMock:
    msg = MagicMock()
    msg.content = None
    msg.tool_calls = list(calls)
    choice = MagicMock(message=msg, finish_reason="tool_calls")
    return MagicMock(choices=[choice])


def _resp_stop(text: str = "Done.") -> MagicMock:
    msg = MagicMock(content=text, tool_calls=None)
    choice = MagicMock(message=msg, finish_reason="stop")
    return MagicMock(choices=[choice])


# ---------------------------------------------------------------------------
# LiteLLMBackend regression: behaviour must match the previous in-place loop.
# ---------------------------------------------------------------------------


class TestLiteLLMBackend:
    def test_is_protocol_compliant(self) -> None:
        be = LiteLLMBackend()
        assert isinstance(be, AgentBackend)
        assert be.name == "litellm"
        assert be.is_available() is True

    def test_finalize_returns_rationale(self) -> None:
        be = LiteLLMBackend(model="anthropic/claude-test")

        responses = [
            _resp_tool(_tool_call("finalize_workflow", {"rationale": "Looks great."})),
        ]

        with patch("litellm.completion", side_effect=responses):
            tools_seen: list[str] = []

            def dispatch(call: ToolCall) -> tuple[str, bool]:
                tools_seen.append(call.name)
                if call.name == "finalize_workflow":
                    return "ok", True
                return "ok", False

            rationale = be.run_tool_loop(
                system="sys",
                user="user",
                tools=[],
                dispatch=dispatch,
            )

        assert "Looks great." in rationale
        assert tools_seen == ["finalize_workflow"]

    def test_stop_finish_reason_breaks_loop(self) -> None:
        be = LiteLLMBackend()
        with patch("litellm.completion", side_effect=[_resp_stop("All set.")]):
            rationale = be.run_tool_loop(
                system="sys",
                user="user",
                tools=[],
                dispatch=lambda c: ("ok", False),
            )
        # No finalize_workflow was called → falls back to default rationale.
        assert "no rationale" in rationale.lower() or rationale.strip() == "(no rationale provided)"

    def test_multi_round_dispatch(self) -> None:
        be = LiteLLMBackend()
        responses = [
            _resp_tool(_tool_call("set_param", {"node_id": "1"}, "c1")),
            _resp_tool(_tool_call("finalize_workflow", {"rationale": "done"}, "c2")),
        ]
        order: list[str] = []
        with patch("litellm.completion", side_effect=responses):

            def dispatch(call: ToolCall) -> tuple[str, bool]:
                order.append(call.name)
                return "ok", call.name == "finalize_workflow"

            rationale = be.run_tool_loop(system="sys", user="u", tools=[], dispatch=dispatch)
        assert order == ["set_param", "finalize_workflow"]
        assert rationale == "done"

    def test_emits_events(self) -> None:
        be = LiteLLMBackend()
        events: list[tuple[str, str]] = []
        with patch("litellm.completion", side_effect=[_resp_stop("hello")]):
            be.run_tool_loop(
                system="sys",
                user="u",
                tools=[],
                dispatch=lambda c: ("", False),
                on_event=lambda et, content, tool, args: events.append((et, content[:20])),
            )
        # Should emit at least an "info" start and a "thinking"/"info" end.
        types = {e[0] for e in events}
        assert "info" in types


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestGetBackend:
    def test_default_is_litellm(self) -> None:
        be = get_backend("")
        assert be.name == "litellm"

    def test_unknown_falls_back_to_litellm(self, capsys: pytest.CaptureFixture) -> None:
        be = get_backend("nonexistent-backend")
        assert be.name == "litellm"

    def test_litellm_passes_through_keys(self) -> None:
        be = get_backend("litellm", model="m1", api_key="sk-x", api_base="https://x")
        assert be.name == "litellm"
        assert be.model == "m1"
        assert be.api_key == "sk-x"
        assert be.api_base == "https://x"

    def test_aliases(self) -> None:
        # `claude` should request the claude-code backend (and fall back if missing)
        be = get_backend("claude")
        assert be.name in ("claude-code", "litellm")


# ---------------------------------------------------------------------------
# Availability probe
# ---------------------------------------------------------------------------


class TestProbe:
    def test_returns_all_known_backends(self) -> None:
        statuses = probe_all()
        names = {s.name for s in statuses}
        assert {"litellm", "claude-code", "codex", "gemini-cli"} <= names

    def test_litellm_always_available(self) -> None:
        for s in probe_all():
            if s.name == "litellm":
                assert s.available is True

    def test_each_status_has_required_fields(self) -> None:
        for s in probe_all():
            assert isinstance(s, BackendStatus)
            assert isinstance(s.name, str) and s.name
            assert isinstance(s.available, bool)


# ---------------------------------------------------------------------------
# Claude-code model name normalisation
# ---------------------------------------------------------------------------


class TestClaudeModelNormalisation:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("", ""),
            ("   ", ""),
            ("sonnet", "sonnet"),
            ("default", "default"),
            ("anthropic/claude-sonnet-4-5", "sonnet"),
            ("anthropic/claude-opus-4-1", "opus"),
            ("anthropic/claude-haiku", "haiku"),
            ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5-20250929"),
            ("openai/gpt-4o", ""),
            ("foo/bar", ""),
        ],
    )
    def test_normalise_model(self, raw: str, expected: str) -> None:
        from comfyclaw.agent_backends.claude_code_backend import (
            _normalise_claude_model,
        )

        assert _normalise_claude_model(raw) == expected
