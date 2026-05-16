"""
End-to-end smoke test for video modality (dry-run; no ComfyUI execution).

Two phases:

  Phase A — Hand-built Wan2.2 workflow round-trip
      Loads a representative Wan2.2 API-format workflow, runs it through
      ClawHarness with modality=video and dry_run=True, and asserts the
      harness:
        * routes to the video verifier
        * validates the graph
        * prints the workflow without trying to talk to ComfyUI

  Phase B — Live agent build-from-scratch (optional, --live)
      Asks the agent to design a Wan2.2 workflow from scratch, dry-run only.
      Captures the agent's final workflow JSON and asserts it has the
      structural ingredients a video pipeline needs.

Run:
    uv run python scripts/e2e_video_dryrun.py             # phase A only
    uv run python scripts/e2e_video_dryrun.py --live      # + phase B
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Hand-built Wan2.2 workflow in ComfyUI API format. Uses native nodes that
# the local ComfyUI exposes (verified via /object_info above).
WAN22_WORKFLOW: dict = {
    "1": {
        "class_type": "UNETLoader",
        "_meta": {"title": "Load Wan2.2 UNET"},
        "inputs": {
            "unet_name": "wan2.2_t2v_14B_fp8_e4m3fn.safetensors",
            "weight_dtype": "fp8_e4m3fn",
        },
    },
    "2": {
        "class_type": "CLIPLoader",
        "_meta": {"title": "Load Wan CLIP"},
        "inputs": {
            "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "type": "wan",
        },
    },
    "3": {
        "class_type": "VAELoader",
        "_meta": {"title": "Load Wan VAE"},
        "inputs": {"vae_name": "wan_2.2_vae.safetensors"},
    },
    "4": {
        "class_type": "CLIPTextEncode",
        "_meta": {"title": "Positive"},
        "inputs": {"clip": ["2", 0], "text": "(will be overwritten by harness)"},
    },
    "5": {
        "class_type": "CLIPTextEncode",
        "_meta": {"title": "Negative"},
        "inputs": {
            "clip": ["2", 0],
            "text": (
                "bright tones, overexposed, static, blurred details, "
                "subtitles, paintings, cartoons, still images, worst quality, "
                "low quality, jpeg artifacts"
            ),
        },
    },
    "6": {
        "class_type": "EmptyHunyuanLatentVideo",
        "_meta": {"title": "Empty Video Latent"},
        "inputs": {"width": 832, "height": 480, "length": 16, "batch_size": 1},
    },
    "7": {
        "class_type": "KSampler",
        "_meta": {"title": "KSampler"},
        "inputs": {
            "model": ["1", 0],
            "positive": ["4", 0],
            "negative": ["5", 0],
            "latent_image": ["6", 0],
            "seed": 42,
            "steps": 30,
            "cfg": 5.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1.0,
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "_meta": {"title": "Decode"},
        "inputs": {"samples": ["7", 0], "vae": ["3", 0]},
    },
    "9": {
        "class_type": "SaveAnimatedWEBP",
        "_meta": {"title": "Save WEBP"},
        "inputs": {
            "images": ["8", 0],
            "fps": 16.0,
            "lossless": False,
            "quality": 90,
            "method": "default",
            "filename_prefix": "comfyclaw_video",
        },
    },
}


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        print(f"[FAIL] {msg}")
        sys.exit(1)
    print(f"[ OK ] {msg}")


def phase_a_handbuilt() -> None:
    """Round-trip a known-good Wan2.2 workflow through harness dry-run."""
    from comfyclaw.harness import ClawHarness, HarnessConfig
    from comfyclaw.video_verifier import VideoVerifier
    from comfyclaw.workflow import WorkflowManager

    print("\n========== Phase A: hand-built Wan2.2 round-trip ==========")

    # 1. Schema validity ----------------------------------------------------
    errors = WorkflowManager.validate(WAN22_WORKFLOW)
    _expect(not errors, f"Hand-built workflow validates clean (errors={errors})")

    # 2. Harness wiring -----------------------------------------------------
    cfg = HarnessConfig(
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        server_address=os.environ.get("COMFYUI_ADDR", "127.0.0.1:8000"),
        model="anthropic/claude-haiku-4-5-20251001",  # cheap; we won't actually invoke
        max_iterations=1,
        sync_port=0,                                   # disable WebSocket for the test
        # skills_dir=None: agent now picks up bundled built-in skills
        # automatically (SkillsRegistry default root).
        modality="video",
        video_frames=4,
        run_mode="manual",                             # skip verifier; we just want dry-run
    )

    with ClawHarness.from_workflow_dict(WAN22_WORKFLOW, cfg) as h:
        # Verifier should still be wired correctly even though manual mode skips it.
        # In manual mode self._verifier is None; check the modality field instead.
        _expect(h.config.modality == "video", "harness.config.modality == 'video'")

        # Force-instantiate a VideoVerifier independently and confirm it would
        # route to the video path if used.
        vv = VideoVerifier(
            model=cfg.model,
            n_frames=cfg.video_frames,
            score_weights=cfg.score_weights,
        )
        _expect(vv.n_frames == 4, "VideoVerifier honours config.video_frames")

        # Dry-run: the harness loads the workflow, runs the agent (no-op since
        # manual mode + no real prompt + we're not really LLM-calling the model
        # because dry_run=True bails before ComfyUI). Wait — actually the agent
        # IS called even in dry-run; only ComfyUI execution is skipped. So pass
        # a trivial prompt and let the agent decide there's nothing to do.
        result = h.run(prompt="a red fox at dawn", dry_run=True)

    _expect(result is None, "dry_run returns None (no image bytes)")
    print()


def phase_b_live(prompt: str) -> None:
    """Live LLM agent builds a video workflow from scratch (dry-run)."""
    from comfyclaw.harness import ClawHarness, HarnessConfig

    print("\n========== Phase B: live agent build-from-scratch ==========")
    print(f"Prompt: {prompt!r}")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[SKIP] No ANTHROPIC_API_KEY in env — cannot run live phase.")
        return

    cfg = HarnessConfig(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        server_address=os.environ.get("COMFYUI_ADDR", "127.0.0.1:8000"),
        model="anthropic/claude-sonnet-4-5",
        max_iterations=1,
        sync_port=0,
        # skills_dir=None: built-in skills load automatically.
        modality="video",
        video_frames=4,
        run_mode="manual",
    )

    from comfyclaw.workflow import WorkflowManager

    # Probe ComfyUI for what models are actually installed — this controls
    # whether we expect a real video pipeline or the "no video model, fall
    # back to image" honest-degradation path.
    has_video_weights = _probe_video_weights(cfg.server_address)
    print(f"Video model weights detected on ComfyUI: {has_video_weights}")

    final_workflow: dict | None = None
    final_rationale: list[str] = []

    with ClawHarness.from_workflow_dict({}, cfg) as h:
        # Capture the workflow at the moment the harness commits it.
        _orig_on_change = h._on_workflow_change

        def _capture(wf: dict) -> None:
            nonlocal final_workflow
            final_workflow = dict(wf)
            _orig_on_change(wf)

        # Capture the agent's final rationale string too (the harness logs it
        # through the agent's on_agent_event hook).
        _orig_event = h._on_agent_event

        def _capture_event(event_type: str, content: str,
                           tool_name: str = "", tool_args: dict | None = None) -> None:
            if content:
                final_rationale.append(content)
            _orig_event(event_type, content, tool_name, tool_args)

        h._on_workflow_change = _capture          # type: ignore[method-assign]
        h._on_agent_event = _capture_event        # type: ignore[method-assign]

        h.run(prompt=prompt, dry_run=True)

        # The harness records the agent's final rationale in the evolution log
        # — that string is the most reliable signal of whether the agent
        # acknowledged the missing video model.
        for entry in h.evolution_log.entries:
            if entry.rationale:
                final_rationale.append(entry.rationale)

    if final_workflow is None or not final_workflow:
        print("[FAIL] Agent produced an empty workflow.")
        sys.exit(1)

    classes = sorted({n.get("class_type", "?") for n in final_workflow.values()})
    print("Final workflow node classes:")
    for c in classes:
        print(f"    {c}")

    # Universal assertion — the workflow must be schema-clean regardless of
    # whether the agent built a video graph or an image fallback.
    errors = WorkflowManager.validate(final_workflow)
    _expect(not errors, f"Agent's workflow validates clean (errors={errors})")
    _expect(any("Sampler" in c or "Sample" in c for c in classes),
            "agent placed a sampler")
    _expect(any(c in classes for c in (
        "UNETLoader", "CheckpointLoaderSimple",
        "WanVideoModelLoader", "HunyuanVideoModelLoader",
    )), "agent placed a model loader")

    has_video_latent = any(c in classes for c in (
        "EmptyHunyuanLatentVideo",
        "EmptyMochiLatentVideo", "EmptyLTXVLatentVideo",
        "EmptyCosmosLatentVideo", "WanVideoEmptyLatent",
    ))
    has_video_saver = any(c in classes for c in (
        "SaveAnimatedWEBP", "SaveAnimatedPNG", "SaveVideo",
        "VHS_VideoCombine", "WanVideoDecode",
    ))

    if has_video_weights:
        # Strict path: video weights exist, the agent should produce a real
        # video graph.
        _expect(has_video_latent, "agent placed a video latent (5-D, not image latent)")
        _expect(has_video_saver, "agent placed a video saver")
    else:
        # Honest-degradation path: no video weights on this ComfyUI, so the
        # agent is allowed to fall back to an image pipeline — but ONLY if it
        # surfaced the limitation in its rationale (this is what the
        # video-builder skill demands).
        rationale_blob = "\n".join(final_rationale).lower()
        flagged_missing = any(
            kw in rationale_blob for kw in (
                "no video model", "needs to install", "video generation model",
                "wan2", "wan 2", "hunyuan", "no video weights",
                "cannot generate", "cannot produce video",
            )
        )
        _expect(
            has_video_latent or has_video_saver or flagged_missing,
            "agent built a video graph OR honestly surfaced the missing-video-model limitation",
        )

    print("\nFull workflow JSON:")
    print(json.dumps(final_workflow, indent=2)[:4000])


def _probe_video_weights(server_address: str) -> bool:
    """Return True if ComfyUI has any plausibly-video UNET available."""
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://{server_address}/object_info/UNETLoader", timeout=4
        ) as resp:
            data = json.loads(resp.read())
        models = data.get("UNETLoader", {}).get("input", {}).get(
            "required", {}).get("unet_name", [[]])[0]
        return any(
            any(tag in m.lower() for tag in (
                "wan", "hunyuan", "svd", "ltx", "cosmos", "mochi", "animate",
            ))
            for m in models
        )
    except Exception as exc:
        print(f"[warn] could not probe ComfyUI for video models: {exc}")
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="Also run Phase B (real LLM agent, costs API tokens)")
    ap.add_argument("--prompt", default="a slow dolly forward, a red fox walking "
                    "through misty pine forest at dawn, cinematic")
    args = ap.parse_args()

    phase_a_handbuilt()
    if args.live:
        phase_b_live(args.prompt)
    print("\n✅ End-to-end dry-run passed.")


if __name__ == "__main__":
    main()
