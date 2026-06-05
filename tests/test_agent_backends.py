"""Tests for the AgentBackend abstraction (Phase 1)."""

from __future__ import annotations

import json
from io import StringIO
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
            ("openai/gpt-5.4", ""),
            ("foo/bar", ""),
        ],
    )
    def test_normalise_model(self, raw: str, expected: str) -> None:
        from comfyclaw.agent_backends.claude_code_backend import (
            _normalise_claude_model,
        )

        assert _normalise_claude_model(raw) == expected


class TestClaudeEnvironment:
    def test_claude_env_scrubs_external_api_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from comfyclaw.agent_backends.base import _env_with_claude_path

        monkeypatch.setenv("ANTHROPIC_API_KEY", "bad-key")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "bad-token")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://bad.example")
        monkeypatch.setenv("PATH", "/usr/bin")

        env = _env_with_claude_path("/tmp/claude-bin/claude")

        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert env["PATH"].split(":")[0] == "/tmp/claude-bin"


class TestGeminiCLIBackend:
    def test_does_not_emit_pretty_json_lines_as_thinking(self) -> None:
        from comfyclaw.agent_backends.gemini_backend import GeminiCLIBackend

        pretty_json = (
            '{\n'
            '  "tool_calls": [\n'
            '    {"name": "finalize_workflow", "arguments": {"rationale": "done"}}\n'
            "  ],\n"
            '  "rationale": "done",\n'
            '  "done": false\n'
            "}\n"
        )

        class _Proc:
            stdout = StringIO(pretty_json)
            stderr = StringIO("")

            def wait(self, timeout=None):
                return 0

        events: list[tuple[str, str]] = []

        with patch("subprocess.Popen", return_value=_Proc()):
            be = GeminiCLIBackend(model="")
            rationale = be.run_tool_loop(
                system="sys",
                user="user",
                tools=[],
                dispatch=lambda call: ("ok", call.name == "finalize_workflow"),
                on_event=lambda et, content, tool, args: events.append((et, content)),
            )

        assert rationale == "done"
        assert not any(et == "thinking" and content.strip() in {"{", "}", "],"} for et, content in events)
        assert any(et == "tool_call" for et, _content in events)


class TestClaudeEnvelope:
    def test_fallback_does_not_append_empty_tool_protocol_to_prompt(self) -> None:
        from comfyclaw.agent_backends.claude_code_backend import _run_envelope

        captured: dict[str, object] = {}

        def fake_run(argv, stdin_text, **kwargs):
            captured["argv"] = argv
            captured["stdin"] = stdin_text
            return 0, '{"tool_calls":[],"rationale":"done","done":true}', ""

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "finalize_workflow",
                    "description": "Finish.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        with patch("comfyclaw.agent_backends._stream_session.run_cli_oneshot", side_effect=fake_run):
            rationale = _run_envelope(
                bin_path="claude",
                model="sonnet",
                system="sys",
                user="user",
                tools=tools,
                dispatch=lambda call: ("ok", False),
                on_event=None,
                max_rounds=1,
            )

        assert rationale == "done"
        stdin_text = str(captured["stdin"])
        assert "Available tools:" not in stdin_text
        argv = captured["argv"]
        assert isinstance(argv, list)
        system_prompt = argv[argv.index("--system-prompt") + 1]
        assert "finalize_workflow" in system_prompt

    def test_claude_env_can_preserve_external_api_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from comfyclaw.agent_backends.base import _env_with_claude_path

        monkeypatch.setenv("ANTHROPIC_API_KEY", "explicit-key")

        env = _env_with_claude_path("/tmp/claude", scrub_external_api_keys=False)

        assert env["ANTHROPIC_API_KEY"] == "explicit-key"


# ---------------------------------------------------------------------------
# Codex model name normalisation
# ---------------------------------------------------------------------------


class TestCodexModelNormalisation:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("", "gpt-5.5"),
            ("   ", "gpt-5.5"),
            ("gpt-5.5", "gpt-5.5"),
            ("openai/gpt-5.5", "gpt-5.5"),
            ("gpt-5.4", "gpt-5.4"),
            ("gpt-5.4-mini", "gpt-5.4-mini"),
            ("gpt-5.3-codex", "gpt-5.3-codex"),
            ("gpt-5", "gpt-5.5"),
            ("gpt-5-codex", "gpt-5.5"),
            ("gpt-5.6", "gpt-5.6"),
            ("o3-mini", "o3-mini"),
            ("o3-pro", "o3"),
            ("o4", "o4-mini"),
            ("foo/bar", "gpt-5.5"),
        ],
    )
    def test_pick_model(self, raw: str, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
        from comfyclaw.chat_agent import _codex_pick_model

        monkeypatch.delenv("COMFYCLAW_CODEX_MODEL", raising=False)
        assert _codex_pick_model(raw) == expected

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from comfyclaw.chat_agent import _codex_pick_model

        monkeypatch.setenv("COMFYCLAW_CODEX_MODEL", "custom-codex-model")
        assert _codex_pick_model("gpt-5.5") == "custom-codex-model"


class TestCodexSessionReuse:
    def test_extracts_codex_session_id_from_events(self) -> None:
        from comfyclaw.agent_backends.codex_backend import _extract_codex_session_id

        sid = "123e4567-e89b-12d3-a456-426614174000"
        assert _extract_codex_session_id(f'{{"type":"session.created","session_id":"{sid}"}}') == sid
        assert _extract_codex_session_id(f'{{"thread":{{"id":"{sid}"}}}}') == sid

    def test_records_codex_session_per_comfyclaw_session(self) -> None:
        from comfyclaw.agent_backends.codex_backend import (
            _get_recorded_codex_session,
            _record_codex_session,
        )

        sid = "123e4567-e89b-12d3-a456-426614174000"
        _record_codex_session("comfyclaw-session-a", sid)

        assert _get_recorded_codex_session("comfyclaw-session-a") == sid
        assert _get_recorded_codex_session("comfyclaw-session-b") == ""
