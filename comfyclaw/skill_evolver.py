"""Post-run skill evolution for ComfyClaw.

The evolver turns short-term run evidence into bounded skill-library changes.
It never writes to disk unless a caller explicitly applies a proposal.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .memory import ClawMemory
from .skill_manager import SkillsRegistry

ProposalAction = Literal["create", "refine"]


@dataclass
class SkillEvolutionProposal:
    action: ProposalAction
    name: str
    description: str
    body: str
    rationale: str
    evidence: list[str]
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "name": self.name,
            "description": self.description,
            "body": self.body,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SkillEvolutionProposal:
        action = str(data.get("action") or "create").strip().lower()
        if action not in {"create", "refine"}:
            action = "create"
        evidence = data.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        return cls(
            action=action,  # type: ignore[arg-type]
            name=_slugify(str(data.get("name") or "")),
            description=str(data.get("description") or "").strip(),
            body=str(data.get("body") or "").strip(),
            rationale=str(data.get("rationale") or "").strip(),
            evidence=[str(x).strip() for x in evidence if str(x).strip()],
            confidence=_clamp_float(data.get("confidence"), 0.0, 1.0),
        )

    def is_valid(self) -> bool:
        return bool(self.name and self.description and self.body and self.rationale)

    def format_for_human(self) -> str:
        evidence = "\n".join(f"- {e}" for e in self.evidence[:5]) or "- No evidence captured."
        return (
            f"Skill evolution proposal: {self.action} `{self.name}`\n"
            f"Confidence: {self.confidence:.2f}\n"
            f"Description: {self.description}\n\n"
            f"Rationale:\n{self.rationale}\n\n"
            f"Evidence:\n{evidence}\n\n"
            f"Draft SKILL.md body:\n{self.body}"
        )


@dataclass
class SkillEvolutionResult:
    proposal: SkillEvolutionProposal | None
    applied: bool = False
    message: str = ""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:80].strip("-")


def _clamp_float(value: object, lo: float, hi: float) -> float:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, val))


def _extract_json_object(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class SkillEvolver:
    """Analyze a completed run and propose a skill-library update."""

    def __init__(
        self,
        registry: SkillsRegistry,
        *,
        complete: Callable[[str, int], str] | None = None,
        min_attempts: int = 1,
        min_confidence: float = 0.55,
    ) -> None:
        self.registry = registry
        self.complete = complete
        self.min_attempts = min_attempts
        self.min_confidence = min_confidence

    def propose(
        self,
        *,
        prompt: str,
        memory: ClawMemory,
        evolution_log: str,
    ) -> SkillEvolutionProposal | None:
        if len(memory.attempts) < self.min_attempts:
            return None

        evidence = self._evidence_lines(memory)
        if not evidence:
            return None

        if self.complete is not None:
            proposal = self._llm_proposal(prompt, memory, evolution_log, evidence)
            if proposal and proposal.is_valid() and proposal.confidence >= self.min_confidence:
                return proposal
            return None
        return self._heuristic_proposal(prompt, memory, evidence)

    def apply(self, proposal: SkillEvolutionProposal) -> str:
        if not proposal.is_valid():
            raise ValueError("invalid skill evolution proposal")
        return self.registry.upsert_user_skill(
            proposal.name,
            proposal.description,
            proposal.body,
            origin="post-run self-evolution",
        )

    def maybe_evolve(
        self,
        *,
        prompt: str,
        memory: ClawMemory,
        evolution_log: str,
        confirm: Callable[[SkillEvolutionProposal], bool] | None = None,
    ) -> SkillEvolutionResult:
        proposal = self.propose(prompt=prompt, memory=memory, evolution_log=evolution_log)
        if proposal is None:
            return SkillEvolutionResult(None, False, "No skill evolution needed.")

        approved = confirm(proposal) if confirm else _terminal_confirm(proposal)
        if not approved:
            return SkillEvolutionResult(proposal, False, "Skill evolution skipped by human.")

        name = self.apply(proposal)
        return SkillEvolutionResult(proposal, True, f"Applied skill evolution: {name}")

    def _llm_proposal(
        self,
        prompt: str,
        memory: ClawMemory,
        evolution_log: str,
        evidence: list[str],
    ) -> SkillEvolutionProposal | None:
        if self.complete is None:
            return None

        skills = self.registry.get_manifest()
        skill_brief = "\n".join(
            f"- {s['name']}: {s.get('description', '')} ({s.get('source', '')})"
            for s in skills[:80]
        )
        msg = (
            "You are maintaining a ComfyClaw agent skill library after a completed task.\n"
            "Decide whether the run produced a reusable lesson that should become a new skill "
            "or refine an existing skill. Prefer NO proposal unless the evidence is reusable "
            "across future tasks. Never propose secrets, one-off prompt text, or a broad duplicate.\n\n"
            "Human feedback marks attempts as good cases or bad cases. For good cases, distill "
            "the reusable workflow/prompt tactics that should be repeated. For bad cases, distill "
            "the failure mode and the repair/avoidance protocol. If the proposal refines an existing "
            "skill, preserve its scope and add only the newly supported lesson.\n\n"
            "Return ONLY JSON with keys: action ('create' or 'refine'), name, description, "
            "body, rationale, evidence (array), confidence (0-1). If no update is needed, "
            'return {"action":"none","confidence":0,"rationale":"..."}.\n\n'
            f"User prompt:\n{prompt}\n\n"
            f"Existing skills:\n{skill_brief or '(none)'}\n\n"
            f"Attempt history:\n{memory.format_history_for_agent()}\n\n"
            f"Evolution log:\n{evolution_log}\n\n"
            f"Candidate evidence:\n" + "\n".join(f"- {line}" for line in evidence)
        )
        try:
            raw = self.complete(msg, 1200)
        except Exception as exc:
            print(f"[SkillEvolver] LLM proposal failed: {exc}", file=sys.stderr)
            return None
        data = _extract_json_object(raw)
        if not data or str(data.get("action", "")).lower() == "none":
            return None
        proposal = SkillEvolutionProposal.from_dict(data)
        if proposal.action == "refine" and proposal.name not in self.registry.skill_names:
            proposal.action = "create"
        return proposal

    def _heuristic_proposal(
        self,
        prompt: str,
        memory: ClawMemory,
        evidence: list[str],
    ) -> SkillEvolutionProposal | None:
        good_cases = [
            a for a in memory.attempts if a.feedback_case == "good case" and a.evolve_requested
        ]
        if good_cases:
            best = max(good_cases, key=lambda a: a.verifier_score)
            slug = _slugify("good-case-" + " ".join(re.findall(r"[a-zA-Z0-9]+", prompt)[:4]))
            comment_line = (
                f"\nHuman comment to preserve: {best.feedback_comment}"
                if best.feedback_comment
                else ""
            )
            return SkillEvolutionProposal(
                action="create",
                name=slug or "good-case-workflow-skill",
                description="Reuse workflow and prompt tactics from a human-approved generation.",
                body=(
                    "Use when a future task resembles the prompt or quality target from this "
                    "human-approved run.\n\n"
                    "1. Start from the workflow pattern that produced the approved image.\n"
                    "2. Preserve the prompt, sampler, model, and structural choices that aligned "
                    "with the user's positive feedback.\n"
                    "3. When adapting, change only the subject-specific details first; keep the "
                    "composition and quality controls stable until there is evidence they fail.\n"
                    "4. Treat the human comment as the quality target to maintain."
                    f"{comment_line}"
                ),
                rationale="The user explicitly marked a generated result as a good case for evolution.",
                evidence=evidence[:5],
                confidence=0.6,
            )

        failures = [f for a in memory.attempts for f in a.failed]
        if not failures:
            return None
        failed_text = " ".join(failures).lower()
        if "execution error" in failed_text or "workflow failed" in failed_text:
            name = "workflow-error-recovery"
            if name in self.registry.skill_names:
                action: ProposalAction = "refine"
            else:
                action = "create"
            body = (
                "Use when ComfyUI rejects a workflow or an execution-time node error repeats.\n\n"
                "1. Inspect the full workflow before adding new nodes.\n"
                "2. Validate graph references and slot indices.\n"
                "3. Delete or rewire the broken node before layering on new branches.\n"
                "4. Query available model/node options before changing filenames or classes.\n"
                "5. Re-submit only after validation passes."
            )
            return SkillEvolutionProposal(
                action=action,
                name=name,
                description="Recover from repeated ComfyUI workflow rejection or execution errors.",
                body=body,
                rationale="The run recorded workflow failures that are reusable as a repair protocol.",
                evidence=evidence[:5],
                confidence=0.6,
            )
        if len(memory.attempts) >= 2 and any(a.verifier_score < 0.65 for a in memory.attempts):
            slug = _slugify("task-pattern-" + " ".join(re.findall(r"[a-zA-Z0-9]+", prompt)[:4]))
            return SkillEvolutionProposal(
                action="create",
                name=slug or "task-pattern-skill",
                description="Reusable prompt and workflow tactics learned from a difficult task pattern.",
                body=(
                    "Use when a future task resembles the evidence from this run.\n\n"
                    "1. Start from the highest-scoring prior workflow pattern, not the earliest attempt.\n"
                    "2. Preserve changes that improved verifier-passed requirements.\n"
                    "3. Target the failed requirements with one structural change at a time.\n"
                    "4. Keep prompt edits specific to subject, composition, lighting, and quality defects."
                ),
                rationale="Multiple attempts had low scores, suggesting a reusable task-pattern lesson.",
                evidence=evidence[:5],
                confidence=0.55,
            )
        return None

    def _evidence_lines(self, memory: ClawMemory) -> list[str]:
        lines: list[str] = []
        for attempt in memory.attempts:
            if attempt.feedback_case:
                comment = (
                    f" Comment: {attempt.feedback_comment}" if attempt.feedback_comment else ""
                )
                lines.append(
                    f"Attempt {attempt.iteration} human-labeled {attempt.feedback_case} "
                    f"(rating={attempt.feedback_rating}, evolve={attempt.evolve_requested}).{comment}"
                )
            if attempt.failed:
                lines.append(f"Attempt {attempt.iteration} failed: {', '.join(attempt.failed[:3])}")
            if attempt.experience:
                lines.append(f"Attempt {attempt.iteration} lesson: {attempt.experience}")
        best = memory.best_attempt()
        if best:
            lines.append(f"Best attempt {best.iteration} scored {best.verifier_score:.2f}.")
        return lines[:12]


def _terminal_confirm(proposal: SkillEvolutionProposal) -> bool:
    print("\n[SkillEvolver] ── Proposed Skill Evolution ──")
    print(proposal.format_for_human())
    if not sys.stdin.isatty():
        print("[SkillEvolver] Non-interactive terminal; proposal not applied.")
        return False
    answer = input("\nApply this skill evolution? [y/N] ").strip().lower()
    return answer in {"y", "yes"}
