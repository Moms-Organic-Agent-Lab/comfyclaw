"""Tests for skill injection into the chat path (hybrid catalogue + /skill)."""

from __future__ import annotations

from pathlib import Path

import pytest

from comfyclaw import chat_agent as ca
from comfyclaw.skill_manager import SkillsRegistry


def _write_skill(root: Path, name: str, description: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SkillsRegistry:
    root = tmp_path / "userskills"
    root.mkdir(parents=True)
    _write_skill(root, "lighting-pro", "Improve lighting and shadows.", "LIGHTING BODY")
    _write_skill(root, "lora-stack", "Stack multiple LoRA models.", "LORA BODY")
    monkeypatch.setenv("COMFYCLAW_USER_SKILLS_DIR", str(root))
    # Isolate from the builtin catalogue so assertions stay deterministic.
    return SkillsRegistry(include_builtin_root=False, quiet=True)


class TestParseSkillCommands:
    def test_extracts_known_names_and_ignores_prose(self) -> None:
        valid = {"lighting-pro", "lora-stack"}
        got = ca._parse_skill_commands("/skill lighting-pro make it pop", valid)
        assert got == ["lighting-pro"]

    def test_multiple_names(self) -> None:
        valid = {"lighting-pro", "lora-stack"}
        got = ca._parse_skill_commands("/skills lighting-pro, lora-stack please", valid)
        assert got == ["lighting-pro", "lora-stack"]

    def test_no_command(self) -> None:
        assert ca._parse_skill_commands("just a normal question", {"lighting-pro"}) == []


class TestBuildSkillBlock:
    def test_catalogue_always_present(self, registry: SkillsRegistry) -> None:
        block = ca._build_skill_block(registry, [{"role": "user", "content": "hi"}])
        assert "<available_skills>" in block
        assert "lighting-pro" in block

    def test_forced_skill_body_inlined(self, registry: SkillsRegistry) -> None:
        msgs = [{"role": "user", "content": "/skill lora-stack add a lora"}]
        block = ca._build_skill_block(registry, msgs)
        assert "## Loaded skill instructions" in block
        assert '<skill name="lora-stack">' in block
        assert "LORA BODY" in block

    def test_relevant_skill_auto_inlined(self, registry: SkillsRegistry) -> None:
        # "lighting" overlaps the lighting-pro description keywords.
        msgs = [{"role": "user", "content": "how do I fix the lighting?"}]
        block = ca._build_skill_block(registry, msgs)
        assert "LIGHTING BODY" in block

    def test_none_registry_returns_empty(self) -> None:
        assert ca._build_skill_block(None, [{"role": "user", "content": "hi"}]) == ""

    def test_body_count_capped(
        self, monkeypatch: pytest.MonkeyPatch, registry: SkillsRegistry
    ) -> None:
        monkeypatch.setattr(ca, "_CHAT_SKILL_MAX_BODIES", 1)
        msgs = [{"role": "user", "content": "/skills lighting-pro lora-stack"}]
        block = ca._build_skill_block(registry, msgs)
        # Only the first (explicit-priority) body should be inlined.
        assert block.count("<skill name=") == 1
