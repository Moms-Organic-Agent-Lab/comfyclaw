"""
Unit tests for the video-modality pipeline.

Three surfaces are exercised:

1. ``ComfyClient.collect_videos`` — extracts ``videos`` / ``gifs`` / animated
   ``images`` entries from a mocked history response.
2. ``_extract_frames`` — animated WEBP/GIF round-trip via PIL.
3. ``VideoVerifier.verify`` — end-to-end with litellm mocked; checks that the
   multi-image temporal prompt is assembled and that video-specific
   requirement questions are added to the checklist.

No real network or ffmpeg required — every external call is monkey-patched.
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from comfyclaw.client import ComfyClient, _guess_video_mime
from comfyclaw.video_verifier import VideoVerifier, _even_indices, _extract_frames

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _litellm_text_response(text: str) -> MagicMock:
    msg = MagicMock()
    msg.content = text
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.fixture()
def animated_gif_bytes() -> bytes:
    """Build a 4-frame animated GIF in memory using PIL."""
    from PIL import Image

    frames = [Image.new("RGB", (8, 8), color=(i * 60 % 255, 50, 50)) for i in range(4)]
    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _guess_video_mime
# ---------------------------------------------------------------------------


class TestGuessVideoMime:
    def test_mp4_from_extension(self) -> None:
        assert _guess_video_mime("clip.mp4", None) == "video/mp4"

    def test_webm_from_extension(self) -> None:
        assert _guess_video_mime("clip.webm", None) == "video/webm"

    def test_gif_from_extension(self) -> None:
        assert _guess_video_mime("clip.gif", None) == "image/gif"

    def test_webp_from_extension(self) -> None:
        assert _guess_video_mime("clip.webp", None) == "image/webp"

    def test_format_hint_overrides_extension(self) -> None:
        # ComfyUI's VHS sometimes reports the bare format string.
        assert _guess_video_mime("output.bin", "mp4") == "video/mp4"
        assert _guess_video_mime("output.bin", "webm") == "video/webm"


# ---------------------------------------------------------------------------
# ComfyClient.collect_videos
# ---------------------------------------------------------------------------


class TestCollectVideos:
    def test_collects_videos_key(self) -> None:
        client = ComfyClient.__new__(ComfyClient)
        client.server_address = "x"
        client.client_id = "y"
        history = {
            "outputs": {
                "9": {
                    "videos": [
                        {
                            "filename": "clip.mp4",
                            "subfolder": "",
                            "type": "output",
                            "format": "mp4",
                        },
                    ]
                }
            }
        }
        with patch.object(client, "get_image", return_value=b"FAKEMP4") as gi:
            out = client.collect_videos(history)
        assert out == [(b"FAKEMP4", "video/mp4")]
        gi.assert_called_once()

    def test_collects_gifs_key(self) -> None:
        client = ComfyClient.__new__(ComfyClient)
        client.server_address = "x"
        client.client_id = "y"
        history = {
            "outputs": {"9": {"gifs": [{"filename": "a.gif", "subfolder": "", "type": "output"}]}}
        }
        with patch.object(client, "get_image", return_value=b"GIF89aXXX"):
            out = client.collect_videos(history)
        assert out == [(b"GIF89aXXX", "image/gif")]

    def test_animated_webp_under_images_key(self) -> None:
        # SaveAnimatedWEBP reports under "images" but with a .webp filename —
        # collect_videos should pick it up.
        client = ComfyClient.__new__(ComfyClient)
        client.server_address = "x"
        client.client_id = "y"
        history = {
            "outputs": {
                "9": {
                    "images": [
                        {"filename": "anim.webp", "subfolder": "", "type": "output"},
                    ]
                }
            }
        }
        with patch.object(client, "get_image", return_value=b"RIFFxxxxWEBPVP8L"):
            out = client.collect_videos(history)
        assert out == [(b"RIFFxxxxWEBPVP8L", "image/webp")]

    def test_ignores_plain_png_in_images(self) -> None:
        # Non-animated images must not show up in the video collector.
        client = ComfyClient.__new__(ComfyClient)
        client.server_address = "x"
        client.client_id = "y"
        history = {
            "outputs": {"9": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}}
        }
        with patch.object(client, "get_image", return_value=b"PNG") as gi:
            out = client.collect_videos(history)
        assert out == []
        gi.assert_not_called()


# ---------------------------------------------------------------------------
# ComfyClient.wait_for_completion error metadata
# ---------------------------------------------------------------------------


class TestWaitForCompletionErrors:
    def test_execution_error_includes_node_and_traceback(self) -> None:
        client = ComfyClient.__new__(ComfyClient)
        client.server_address = "x"
        client.client_id = "y"
        client.get_history = MagicMock(
            return_value={
                "pid": {
                    "status": {
                        "status_str": "error",
                        "messages": [
                            [
                                "execution_error",
                                {
                                    "node_id": "10",
                                    "node_type": "KSampler",
                                    "exception_message": "[Errno 5] Input/output error",
                                    "traceback": ["tqdm/std.py", "app/logger.py"],
                                },
                            ]
                        ],
                    }
                }
            }
        )

        out = client.wait_for_completion("pid", timeout=1, poll_interval=0)

        assert "node 10 KSampler" in out["error"]
        assert out["error_node_id"] == "10"
        assert out["error_node_type"] == "KSampler"
        assert out["error_traceback"] == ["tqdm/std.py", "app/logger.py"]


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------


class TestEvenIndices:
    def test_basic(self) -> None:
        assert _even_indices(10, 4) == [0, 3, 6, 9]

    def test_one_frame_request(self) -> None:
        # n=1 is degenerate but should not crash.
        assert _even_indices(10, 1) == [0]

    def test_total_smaller_than_n(self) -> None:
        assert _even_indices(3, 6) == [0, 1, 2]

    def test_empty(self) -> None:
        assert _even_indices(0, 6) == []


class TestExtractFrames:
    def test_animated_gif_round_trip(self, animated_gif_bytes: bytes) -> None:
        frames = _extract_frames(animated_gif_bytes, "image/gif", n_frames=3)
        assert len(frames) == 3
        # Each frame should be a valid PNG (start with PNG magic bytes).
        for f in frames:
            assert f[:8] == b"\x89PNG\r\n\x1a\n"

    def test_unknown_bytes_returns_empty(self) -> None:
        assert _extract_frames(b"not a video at all", "video/mp4", n_frames=4) == []


# ---------------------------------------------------------------------------
# VideoVerifier.verify
# ---------------------------------------------------------------------------


class TestVideoVerifier:
    def test_verify_packages_frames_into_temporal_prompt(self, animated_gif_bytes: bytes) -> None:
        verifier = VideoVerifier.__new__(VideoVerifier)
        from comfyclaw.verifier import ClawVerifier

        inner = ClawVerifier.__new__(ClawVerifier)
        inner.model = "test/m"
        inner.score_weights = (0.6, 0.4)
        inner.max_workers = 2
        verifier._inner = inner
        verifier.model = "test/m"
        verifier.score_weights = (0.6, 0.4)
        verifier.max_workers = 2
        verifier.n_frames = 4
        verifier.media_type = "image/gif"

        # Mock the three litellm hops:
        #   1) decompose → returns a 2-question JSON array.
        #   2) each per-question check → "yes" or "no".
        #   3) detailed analysis → a JSON object.
        call_log = []

        def _fake_completion(*args, **kwargs):  # type: ignore[no-untyped-def]
            messages = kwargs.get("messages") or []
            call_log.append(messages)
            content = messages[-1]["content"] if messages else ""
            if (
                isinstance(content, str)
                and "Analyze the following image generation prompt" in content
            ):
                return _litellm_text_response('["Is there a fox?", "Is it red?"]')
            if isinstance(content, list):
                text_block = next((c for c in content if c.get("type") == "text"), {})
                txt = text_block.get("text", "")
                if "Answer only 'yes' or 'no'" in txt:
                    return _litellm_text_response("yes")
                # detailed analysis
                return _litellm_text_response(
                    json.dumps(
                        {
                            "overall_assessment": "decent",
                            "score": 0.75,
                            "region_issues": [],
                            "evolution_suggestions": [],
                        }
                    )
                )
            return _litellm_text_response("yes")

        with patch("comfyclaw.video_verifier.litellm.completion", side_effect=_fake_completion):
            result = verifier.verify(animated_gif_bytes, "a red fox", iteration=1)

        # The result blends the requirement pass rate (1.0 since all "yes")
        # with the detail score (0.75) at weights (0.6, 0.4):
        #   0.6 * 1.0 + 0.4 * 0.75 = 0.9
        assert result.score == pytest.approx(0.9, abs=1e-3)

        # Video-specific requirements were appended to the checklist.
        questions = [c.question for c in result.checks]
        assert any("temporally coherent" in q for q in questions)
        assert any("consistent identity" in q for q in questions)

        # The per-question messages should contain multiple image blocks
        # (the temporal frame sequence), not just one.
        multi_image_calls = [
            m
            for m in call_log
            if isinstance(m[-1]["content"], list)
            and sum(1 for c in m[-1]["content"] if c.get("type") == "image_url") >= 2
        ]
        assert len(multi_image_calls) >= 1


# ---------------------------------------------------------------------------
# HarnessConfig wiring
# ---------------------------------------------------------------------------


class TestHarnessConfigModality:
    def test_default_is_image(self) -> None:
        from comfyclaw.harness import HarnessConfig

        cfg = HarnessConfig()
        assert cfg.modality == "image"
        assert cfg.video_frames == 6

    def test_video_mode_accepted(self) -> None:
        from comfyclaw.harness import HarnessConfig

        cfg = HarnessConfig(modality="video", video_frames=8)
        assert cfg.modality == "video"
        assert cfg.video_frames == 8
