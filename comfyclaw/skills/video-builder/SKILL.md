---
name: video-builder
description: >-
  Architecture recipes for building ComfyUI video-generation workflows from
  scratch. Activate whenever modality=video and the workflow is empty or
  near-empty. Pairs with model-specific skills like wan-video-2.2 for
  parameter values; this skill owns the *graph topology*.
license: MIT
metadata:
  author: davidliuk
  version: "1.0.0"
---

This skill is the video-modality counterpart to `workflow-builder`. The agent
should read it before placing the first node in a video workflow.

## Universal video spine

Every text-to-video workflow needs these six logical stages, in order:

1. **Model load** — `UNETLoader` (or model-family-specific loader).
2. **Text encode** — `CLIPLoader` + two `CLIPTextEncode` nodes (positive +
   negative). Skip if the model loader includes a built-in encoder.
3. **VAE load** — `VAELoader`. Some video models share the image VAE; some
   ship their own — check the chosen model's SKILL.md.
4. **Empty latent video** — the key difference from image workflows.
   Image workflows use `EmptyLatentImage` / `EmptySD3LatentImage` (4-D
   tensor `[B,C,H,W]`); video workflows use `EmptyHunyuanLatentVideo` (or a
   model-family equivalent) which is a 5-D tensor `[B,C,T,H,W]` with a
   `length` widget for frame count.
5. **Sampler** — `KSampler` is the safe default. Some video custom nodes
   ship a `KSamplerAdvanced`-style sampler; only use it if the model
   demands it.
6. **Decode + save** — `VAEDecode` then **a video saver**:
   - `SaveAnimatedWEBP` — built into ComfyUI core, no extras, animated WEBP.
   - `SaveAnimatedPNG` — same, lossless but huge files.
   - `VHS_VideoCombine` — requires ComfyUI-VideoHelperSuite + ffmpeg, but
     outputs real mp4/webm.

## Slot reference for the unusual nodes

| Node | Outputs | Inputs |
|---|---|---|
| `EmptyHunyuanLatentVideo` | 0:LATENT | `width`, `height`, `length`, `batch_size` |
| `SaveAnimatedWEBP` | — | 0:images, `fps`, `lossless`, `quality`, `filename_prefix`, `method` |
| `VHS_VideoCombine` | — | 0:images, `frame_rate`, `format`, `crf`, `pix_fmt`, `filename_prefix` |

## Frame count → clip duration

```
duration_seconds  =  length  /  fps
```

Stick to **16-33 frames at 16 fps** (1-2 s) for the first attempt unless
the user explicitly asks for a longer clip. Long clips burn GPU time and
amplify temporal artefacts that Wan, Hunyuan, and SVD all struggle with.

## When the user asks for "video" without a model

Read available models (`query_available_models`), then pick in this
preference order:
1. **Wan2.2** if `wan2.2_*` is present → read `wan-video-2.2` skill.
2. **Hunyuan-Video** if `hunyuan_video_*` is present.
3. **AnimateDiff** if a motion module is present alongside an SD1.5
   checkpoint.
4. **SVD** (`svd_xt.safetensors`) — image-to-video only; require an input
   image first.

If none of these are installed, surface the error to the user via
`finalize_workflow` rather than guessing at a backbone that doesn't exist.

## Things that break video graphs (vs image graphs)

- Mixing image-VAE and video-VAE outputs without a re-encode step.
- Feeding `EmptyLatentImage` (4-D) to a video sampler.
- Forgetting `length` on the latent — silently produces a single still frame.
- Putting `SaveImage` after `VAEDecode` instead of an animation saver —
  produces N PNGs but no video file, so the harness's `collect_videos()`
  returns empty.
