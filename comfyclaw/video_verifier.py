"""
VideoVerifier — frame-sampling adapter on top of ClawVerifier.

Strategy (per the MVP plan):
  1. Extract N evenly-spaced frames from the candidate video.
  2. Send them to the VLM as a single multi-image message, annotated as
     temporal frames so the model can reason about motion + consistency.
  3. Augment the requirement checklist with a few video-specific yes/no
     questions (temporal coherence, motion plausibility, flicker).
  4. Return the same ``VerifierResult`` shape ClawHarness already consumes,
     so the harness loop and human/hybrid wrappers stay unchanged.

Frame extraction backends, in preference order:
  * ``PIL`` for animated WEBP / GIF / APNG (zero new required deps).
  * ``imageio`` (optional extras=video) for mp4 / webm.

If neither backend can decode the bytes, we degrade to single-frame
analysis using the raw blob — for animated WEBP / GIF the VLM can still
inspect the first frame thumbnail.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import litellm

from .verifier import (
    _DECOMPOSE_PROMPT,
    _DETAILED_ANALYSIS_PROMPT,
    ClawVerifier,
    RegionIssue,
    RequirementCheck,
    VerifierResult,
)

log = logging.getLogger(__name__)


_VIDEO_REQUIREMENTS = (
    "Is the motion across frames temporally coherent (no abrupt teleporting, flicker, or identity switches)?",
    "Do the main subjects keep a consistent identity, colour, and rough shape across the frames?",
    "Does the motion look physically plausible for the described scene?",
)


def _extract_frames(video_bytes: bytes, mime_type: str, n_frames: int) -> list[bytes]:
    """
    Return up to *n_frames* PNG-encoded frames sampled evenly through the clip.

    Tries PIL first (handles animated WEBP / GIF / APNG without extra deps),
    then ``imageio`` (handles mp4 / webm if the optional ``[video]`` extra is
    installed).  Returns an empty list if no backend can decode the bytes.
    """
    # ── PIL path: animated WEBP / GIF / APNG ───────────────────────────────
    try:
        from PIL import Image, ImageSequence

        img = Image.open(io.BytesIO(video_bytes))
        # ``n_frames`` attr is present on animated formats; fall back to count.
        try:
            total = img.n_frames  # type: ignore[attr-defined]
        except Exception:
            total = sum(1 for _ in ImageSequence.Iterator(img))
            img = Image.open(io.BytesIO(video_bytes))  # rewind iterator

        if total > 1:
            indices = _even_indices(total, n_frames)
            frames: list[bytes] = []
            for idx in indices:
                img.seek(idx)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="PNG")
                frames.append(buf.getvalue())
            return frames
    except Exception as exc:
        log.debug("PIL frame extraction failed: %s", exc)

    # ── imageio path: mp4 / webm ───────────────────────────────────────────
    try:
        import imageio.v3 as iio  # type: ignore[import-not-found]

        all_frames = iio.imread(video_bytes, index=None, plugin="pyav")
        total = len(all_frames)
        if total == 0:
            return []
        indices = _even_indices(total, n_frames)
        from PIL import Image

        out: list[bytes] = []
        for idx in indices:
            arr = all_frames[idx]
            buf = io.BytesIO()
            Image.fromarray(arr).convert("RGB").save(buf, format="PNG")
            out.append(buf.getvalue())
        return out
    except Exception as exc:
        log.debug("imageio frame extraction failed: %s", exc)

    return []


def _even_indices(total: int, n: int) -> list[int]:
    """Return *n* evenly-spaced integer indices in [0, total-1]."""
    if total <= 0 or n <= 0:
        return []
    if total <= n:
        return list(range(total))
    if n == 1:
        return [0]
    return [round(i * (total - 1) / (n - 1)) for i in range(n)]


class VideoVerifier:
    """
    Drop-in verifier for video outputs.

    Public API mirrors ``ClawVerifier``: ``verify(bytes, prompt, iteration)``
    returns a ``VerifierResult``, plus ``complete(prompt)`` for summarisation.

    Parameters
    ----------
    api_key, model, score_weights, max_workers
        Same as ``ClawVerifier``.
    media_type
        Hint for what kind of bytes will be passed to ``verify()``.  Frame
        extraction probes magic bytes regardless; this is just a default
        passed through to ``verify()`` callers that omit it.
    n_frames
        How many frames to sample per clip (default 6 — first, last, and 4
        evenly-spaced).
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "anthropic/claude-sonnet-4-5",
        score_weights: tuple[float, float] = (0.6, 0.4),
        max_workers: int = 6,
        n_frames: int = 6,
        media_type: str = "video/mp4",
    ) -> None:
        # Reuse ClawVerifier for the per-question yes/no path so we share
        # the request shape, parsing, and env-var handling.
        self._inner = ClawVerifier(
            api_key=api_key,
            model=model,
            score_weights=score_weights,
            max_workers=max_workers,
        )
        self.model = model
        self.score_weights = score_weights
        self.max_workers = max_workers
        self.n_frames = max(2, n_frames)
        self.media_type = media_type

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(
        self,
        video_bytes: bytes,
        prompt: str,
        iteration: int = 0,
        media_type: str | None = None,
    ) -> VerifierResult:
        mt = media_type or self.media_type
        frames = _extract_frames(video_bytes, mt, self.n_frames)
        if not frames:
            log.warning(
                "VideoVerifier: no frames extracted (mime=%s, %d bytes). "
                "Falling back to single-blob analysis — install comfyclaw[video] for mp4/webm.",
                mt,
                len(video_bytes),
            )
            # Best-effort fallback: treat the whole blob as a single image.
            return self._inner.verify(video_bytes, prompt, iteration=iteration)

        # Build the per-frame image blocks once (shared across requirement checks).
        frame_blocks = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{base64.standard_b64encode(f).decode()}"
                },
            }
            for f in frames
        ]
        header = (
            f"You are reviewing {len(frames)} frames sampled evenly in time "
            f"from a generated video (frame 1 = start, frame {len(frames)} = end). "
            "Treat them as a temporal sequence, not independent images."
        )

        questions = self._decompose_prompt(prompt) + list(_VIDEO_REQUIREMENTS)
        checks = self._check_requirements(frame_blocks, header, questions)
        detail = self._detailed_analysis(frame_blocks, header, prompt)

        passed = [c.question for c in checks if c.passed]
        failed = [c.question for c in checks if not c.passed]
        req_score = len(passed) / len(checks) if checks else 0.0

        detail_score = detail.get("score")
        w_req, w_det = self.score_weights
        if detail_score is not None:
            score = w_req * req_score + w_det * float(detail_score)
        else:
            score = req_score

        region_issues = [
            RegionIssue(
                region=ri.get("region", "unknown"),
                issue_type=ri.get("issue_type", "unknown"),
                description=ri.get("description", ""),
                severity=ri.get("severity", "medium"),
                fix_strategies=ri.get("fix_strategies", []),
            )
            for ri in detail.get("region_issues", [])
        ]

        return VerifierResult(
            score=round(score, 3),
            checks=checks,
            passed=passed,
            failed=failed,
            region_issues=region_issues,
            overall_assessment=detail.get("overall_assessment", ""),
            evolution_suggestions=detail.get("evolution_suggestions", []),
        )

    def complete(self, prompt: str, max_tokens: int = 200) -> str:
        return self._inner.complete(prompt, max_tokens=max_tokens)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _decompose_prompt(self, prompt: str) -> list[str]:
        resp = litellm.completion(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": _DECOMPOSE_PROMPT.format(prompt=prompt)}],
        )
        text = (resp.choices[0].message.content or "").strip()
        try:
            m = re.search(r"\[.*\]", text, re.DOTALL)
            return json.loads(m.group() if m else text)
        except Exception:
            return [ln.strip() for ln in text.splitlines() if "?" in ln]

    def _check_requirements(
        self,
        frame_blocks: list[dict],
        header: str,
        questions: list[str],
    ) -> list[RequirementCheck]:
        def check_one(q: str) -> RequirementCheck:
            try:
                content: list[dict] = list(frame_blocks)
                content.append(
                    {"type": "text", "text": f"{header}\nAnswer only 'yes' or 'no'. {q}"}
                )
                resp = litellm.completion(
                    model=self.model,
                    max_tokens=16,
                    messages=[{"role": "user", "content": content}],
                )
                ans = (resp.choices[0].message.content or "").strip().lower()
                return RequirementCheck(q, ans, "yes" in ans and "no" not in ans)
            except Exception as exc:
                return RequirementCheck(q, f"error: {exc}", False)

        n_workers = min(len(questions), self.max_workers)
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            return list(ex.map(check_one, questions))

    def _detailed_analysis(self, frame_blocks: list[dict], header: str, prompt: str) -> dict:
        text_block = {
            "type": "text",
            "text": (
                f"{header}\n\n" + _DETAILED_ANALYSIS_PROMPT.format(prompt=prompt)
                + "\n\nWhen describing region_issues, indicate WHICH frame(s) "
                "the issue appears in (e.g. 'frames 3-5: hand morphs'). Include "
                "temporal artefacts (flicker, identity drift, motion judder) "
                "in evolution_suggestions if present."
            ),
        }
        try:
            resp = litellm.completion(
                model=self.model,
                max_tokens=1800,
                messages=[{"role": "user", "content": [*frame_blocks, text_block]}],
            )
            text = (resp.choices[0].message.content or "").strip()
            m = re.search(r"\{.*\}", text, re.DOTALL)
            return json.loads(m.group() if m else text)
        except Exception as exc:
            return {
                "overall_assessment": f"Analysis error: {exc}",
                "score": None,
                "region_issues": [],
                "evolution_suggestions": [],
            }
