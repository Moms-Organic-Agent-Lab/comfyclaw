from __future__ import annotations

import json
from pathlib import Path

from comfyclaw.memory import ClawMemory
from comfyclaw.skill_evolver import SkillEvolutionProposal, SkillEvolver
from comfyclaw.skill_manager import SkillsRegistry


def _registry(tmp_path: Path, monkeypatch) -> SkillsRegistry:
    user_root = tmp_path / "user-skills"
    monkeypatch.setenv("COMFYCLAW_USER_SKILLS_DIR", str(user_root))
    return SkillsRegistry(
        include_builtin_root=False,
        include_user_root=True,
        quiet=True,
    )


def _memory() -> ClawMemory:
    mem = ClawMemory(max_images=0)
    mem.record(
        iteration=1,
        workflow_snapshot={},
        verifier_score=0.42,
        passed=[],
        failed=["Execution error: bad node slot"],
        experience="The workflow failed until the broken slot was rewired.",
    )
    return mem


def _good_case_memory() -> ClawMemory:
    mem = ClawMemory(max_images=0)
    mem.record(
        iteration=1,
        workflow_snapshot={},
        verifier_score=0.92,
        passed=["matches prompt"],
        failed=[],
        experience="The workflow produced a human-approved composition.",
        feedback_rating="up",
        feedback_comment="great lighting and composition",
        feedback_case="good case",
        evolve_requested=True,
    )
    return mem


def test_llm_json_proposal_is_parsed(tmp_path: Path, monkeypatch) -> None:
    reg = _registry(tmp_path, monkeypatch)

    def complete(_prompt: str, _max_tokens: int) -> str:
        return json.dumps(
            {
                "action": "create",
                "name": "slot-repair",
                "description": "Repair invalid ComfyUI slot wiring.",
                "body": "1. Validate slots.\n2. Rewire bad links.",
                "rationale": "The run exposed a reusable repair pattern.",
                "evidence": ["bad slot"],
                "confidence": 0.8,
            }
        )

    proposal = SkillEvolver(reg, complete=complete).propose(
        prompt="test",
        memory=_memory(),
        evolution_log="log",
    )
    assert proposal is not None
    assert proposal.name == "slot-repair"
    assert proposal.confidence == 0.8


def test_human_reject_does_not_write(tmp_path: Path, monkeypatch) -> None:
    reg = _registry(tmp_path, monkeypatch)
    evolver = SkillEvolver(reg)
    result = evolver.maybe_evolve(
        prompt="test",
        memory=_memory(),
        evolution_log="log",
        confirm=lambda _proposal: False,
    )
    assert result.proposal is not None
    assert not result.applied
    assert "workflow-error-recovery" not in reg.skill_names


def test_llm_none_is_respected(tmp_path: Path, monkeypatch) -> None:
    reg = _registry(tmp_path, monkeypatch)
    evolver = SkillEvolver(reg, complete=lambda _p, _m: '{"action":"none"}')
    result = evolver.maybe_evolve(
        prompt="test",
        memory=_memory(),
        evolution_log="log",
        confirm=lambda _proposal: True,
    )
    assert result.proposal is None
    assert "workflow-error-recovery" not in reg.skill_names


def test_human_approve_writes_user_skill(tmp_path: Path, monkeypatch) -> None:
    reg = _registry(tmp_path, monkeypatch)
    proposal = SkillEvolutionProposal(
        action="create",
        name="slot-repair",
        description="Repair invalid ComfyUI slot wiring.",
        body="1. Validate slots.\n2. Rewire bad links.",
        rationale="Reusable repair pattern.",
        evidence=["bad slot"],
        confidence=0.9,
    )
    evolver = SkillEvolver(reg)
    name = evolver.apply(proposal)
    assert name == "slot-repair"
    assert "slot-repair" in reg.skill_names
    assert "Validate slots" in reg.get_body("slot-repair")


def test_good_case_feedback_can_create_heuristic_skill(tmp_path: Path, monkeypatch) -> None:
    reg = _registry(tmp_path, monkeypatch)
    proposal = SkillEvolver(reg).propose(
        prompt="cinematic portrait with rim lighting",
        memory=_good_case_memory(),
        evolution_log="log",
    )
    assert proposal is not None
    assert proposal.name.startswith("good-case")
    assert "human-approved" in proposal.description
    assert "great lighting" in proposal.body
