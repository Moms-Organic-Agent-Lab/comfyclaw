---
name: wan-video-2.2
description: >-
  Configuration guide for Wan2.2, Alibaba's open-source text-to-video and
  image-to-video model. Activate when the user wants video generation
  (modality=video) and the available models include any of "wan", "wan2.2",
  or "wan_video". Covers both ComfyUI native Wan nodes and the kijai
  WanVideoWrapper custom-node ecosystem — use the kijai path only if native
  nodes are missing.
license: Apache-2.0
metadata:
  author: davidliuk
  version: "1.0.0"
  base_arch: Wan2.2 DiT (T2V / I2V), 14B params with FP8 quantization
  defaults:
    frames: 16
    fps: 16
    width: 832
    height: 480
    steps: 30
    cfg: 5.0
    sampler: euler
    scheduler: simple
---

Wan2.2 is Alibaba's open video diffusion model. Two equivalent node ecosystems
exist in ComfyUI; pick **one** based on what `query_available_models` reports.

## Decision rule

1. Call `query_available_models` first.
2. If the model list contains `wan2.2_t2v_*.safetensors` (or similar) loaded
   through `UNETLoader`, use **Path A — native ComfyUI nodes** below.
3. If only `WanVideoModelLoader` / `WanVideoSampler` show up, use
   **Path B — kijai WanVideoWrapper**.
4. Do not mix nodes from the two paths in the same graph.

---

## Path A — native ComfyUI nodes (preferred)

Spine for text-to-video:

```
UNETLoader (wan2.2_t2v_*.safetensors)
    └→ MODEL ─────────────────────────────┐
CLIPLoader (umt5_xxl, type="wan")          │
    └→ CLIP → CLIPTextEncode (positive)    ↓
                                       KSampler
    └→ CLIP → CLIPTextEncode (negative) ↑    │
VAELoader (wan_2.2_vae.safetensors)             │
    └→ VAE                                      │
EmptyHunyuanLatentVideo (W=832, H=480, length=16)│
    └→ LATENT ─────────────────────────────────→┘
                                          │
                                          ↓
                                   VAEDecode (using VAE above)
                                          │
                                          ↓
                                   SaveAnimatedWEBP (fps=16)
                                   OR  VHS_VideoCombine (format=mp4, fps=16)
```

Key parameters:

| Node | Param | Value |
|---|---|---|
| `UNETLoader` | weight_dtype | `fp8_e4m3fn` |
| `EmptyHunyuanLatentVideo` | length | 16 (≈ 1 s @ 16 fps). Bump to 24-33 for ~2 s. |
| `EmptyHunyuanLatentVideo` | width × height | 832 × 480 (landscape) or 480 × 832 (portrait) |
| `KSampler` | steps | 30 (quality) · 20 (fast) |
| `KSampler` | cfg | 5.0 |
| `KSampler` | sampler_name | `euler` |
| `KSampler` | scheduler | `simple` |
| `SaveAnimatedWEBP` | fps | 16 |
| `SaveAnimatedWEBP` | quality | 90 |

Use `SaveAnimatedWEBP` when available — it ships with ComfyUI core and needs
no extras. `VHS_VideoCombine` produces mp4/webm but requires the
ComfyUI-VideoHelperSuite custom node and an ffmpeg binary on the server.

## Path B — kijai WanVideoWrapper

If `WanVideoModelLoader` is present, use this spine instead:

```
WanVideoModelLoader (model=wan2.2_t2v_14B_fp8)
    └→ WANMODEL ──────────────────────┐
WanVideoTextEncode (positive/negative) │
    └→ EMBEDS ─────────────────────┐  │
WanVideoEmptyLatent (W,H,frames)   │  │
    └→ LATENT ──→ WanVideoSampler ←┴──┘
                       │
                       ↓
                  WanVideoDecode
                       │
                       ↓
                  VHS_VideoCombine (format=mp4, fps=16)
```

Notable kijai-specific knobs:
- `WanVideoSampler.shift` defaults around `5.0` — leave at default.
- `WanVideoSampler.scheduler` should be `uni_pc` or `dpm++` (NOT `simple`).
- The text encoder is bundled inside `WanVideoModelLoader`; you do **not**
  need a separate `CLIPLoader`.

---

## Prompt engineering for Wan2.2

- Lead with camera and motion verbs: *"slow dolly forward, a fox walking
  through misty forest at dawn"*.
- One subject, one main action — Wan struggles with compound actions per clip.
- Include a *stylistic anchor* (e.g. "cinematic, 35 mm film, shallow DoF").
- Wan was trained on Chinese + English captions; either works.

### Default negative prompt (positive results)

> bright tones, overexposed, static, blurred details, subtitles,
> paintings, cartoons, still images, worst quality, low quality,
> jpeg artifacts, ugly, mutated, deformed, extra fingers, extra limbs,
> watermark, signature, text

Always paste this into the negative `CLIPTextEncode` node unless the user
asks for a stylised look that conflicts with it.

---

## Diagnostic checklist before `finalize_workflow`

1. `length` on the latent matches the saver's expected frame count.
2. `fps` is set on the saver (otherwise WEBP defaults to 25 and looks fast).
3. VAE node is wired to BOTH `UNETLoader.MODEL` consumers and the
   `VAEDecode` — Wan uses one VAE for both encode/decode.
4. Resolution is divisible by 16 in both dimensions.
5. CFG is **not** at the SDXL default of 7+ — Wan looks burnt above 6.0.

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Frozen / still frames | `length=1` or wrong latent class | Use `EmptyHunyuanLatentVideo`, set `length≥16` |
| Massive flicker | Wrong scheduler on KSampler | Use `simple` (native) or `uni_pc` (kijai) |
| Subject disappears mid-clip | CFG too high | Drop cfg to 4.0-5.0 |
| Hands explode | Wan's known weakness | Add negative prompt entries above; favour wider shots |
