---
name: hires-fix
description: >-
  Add a second-pass upscale-and-refine stage to recover fine detail lost when
  the base latent is small. Use when the verifier reports blurry output, soft
  texture, or lack of fine detail — especially when the base latent is 768×768
  or smaller. Also trigger when fix_strategy contains "add_hires_fix". This is
  the most reliable fix for global softness that isn't caused by a specific
  structural problem.
license: MIT
compatibility: ComfyClaw agent — requires KSampler, VAEDecode, and VAEEncode nodes.
allowed-tools: add_hires_fix set_param
metadata:
  author: davidliuk
  version: "0.2.0"
---

Hires fix works by decoding the base latent to pixels, upscaling, then
running a second diffusion pass at low denoising strength. The second pass
sharpens detail without changing the overall composition — the model is
essentially "filling in" the detail the small latent couldn't hold.

## Add with `add_hires_fix`

```
add_hires_fix(
  base_ksampler_node_id = "<original KSampler node ID>",
  vae_node_id           = "<VAEDecode or VAE node ID>",
  upscale_factor        = 1.5,    # see table below
  denoise_strength      = 0.5,    # see table below
)
```

## Choosing upscale factor and denoise

| Goal | upscale_factor | denoise_strength |
|---|---|---|
| Sharpen without changing content | 1.5 | 0.35–0.45 |
| Add fine detail with minor refinement | 1.5–2.0 | 0.45–0.60 |
| Re-generate detail more aggressively | 2.0 | 0.60–0.70 |

Start at `upscale_factor=1.5` and `denoise=0.5`. Go higher only if the
verifier still reports softness after the first hires pass.

## Gotchas

- Skip hires fix when the base resolution is already ≥ 1024×1024 — the
  VRAM cost is high and the quality gain is marginal.
- Set hires KSampler `steps` to at least 15; fewer steps produces an
  under-refined second pass that can look worse than the original.
- If VRAM is tight, keep `upscale_factor` at 1.5.
