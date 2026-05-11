---
name: inpainting
description: >-
  Edit a specific region of an existing image while leaving the rest untouched.
  Use when the user wants to "inpaint", "mask", "edit just the X", "remove
  the Y", "replace the background", "change only the face/hands/sky", or
  asks to fix a localized defect (extra fingers, weird eyes, dirty patch)
  without regenerating the whole image. Also trigger when the user uploads
  both an image AND a mask, or asks for "img2img on this region only".
license: MIT
compatibility: ComfyClaw agent — requires LoadImage, VAEEncode/VAEEncodeForInpaint, VAEDecode, KSampler, SetLatentNoiseMask nodes.
allowed-tools: query_available_models add_inpaint_branch set_param
metadata:
  author: davidliuk
  version: "0.1.0"
---

# Region-only editing with inpainting

Inpainting modifies a masked area while preserving every pixel outside
the mask. It's the right tool whenever the user wants a *localized* fix
and explicitly wrong for global style/quality changes (use the
`high-quality`, `creative`, or `photorealistic` skills for those).

## Detection — when this skill fires

- The user uploads an image **and** a mask.
- The prompt contains "inpaint", "mask", "just the X", "only the Y",
  "fix the hands/face/eyes", "remove the watermark/object", "replace the
  background", "change the sky".
- A previous generation succeeded except for a localized defect (extra
  limbs, garbled text, wrong color on one element).
- The verifier reports a defect that affects a small fraction of the
  image only.

## Two workflow recipes

ComfyUI exposes two inpaint encoders. Use them deliberately.

### Recipe A — `VAEEncodeForInpaint` (preserve outside, regenerate inside)

The default. The encoded latent is conditioned to keep unmasked pixels
faithful while the masked region is denoised freely.

```
LoadImage(image)  [0] IMAGE → VAEEncodeForInpaint.pixels
LoadImage(mask)   [1] MASK  → VAEEncodeForInpaint.mask
CheckpointLoader  [2] VAE   → VAEEncodeForInpaint.vae

VAEEncodeForInpaint [0] LATENT → KSampler.latent_image
KSampler [0] LATENT → VAEDecode.samples
VAEDecode [0] IMAGE → SaveImage.images
```

Use **`denoise = 1.0`** here — the encoder takes care of preserving the
non-masked region; the sampler should regenerate the masked area fully.

### Recipe B — `VAEEncode` + `SetLatentNoiseMask` (preserve inside, controlled blend)

Manual noise scheduling. Use when you need to *partially* edit the
masked region (e.g., enhance detail rather than replace).

```
LoadImage(image) [0] IMAGE → VAEEncode.pixels
CheckpointLoader [2] VAE   → VAEEncode.vae
VAEEncode [0] LATENT → SetLatentNoiseMask.samples
LoadImage(mask)  [1] MASK  → SetLatentNoiseMask.mask
SetLatentNoiseMask [0] LATENT → KSampler.latent_image
```

Here **`denoise` is the editing strength**:
- `0.30–0.45`: subtle refinement (sharpen detail, fix small glitches).
- `0.55–0.70`: moderate redraw (change color, tweak shape).
- `0.80–1.00`: full replacement (effectively recipe A).

Start low and step up if the change is too timid.

## Mask preparation

The single biggest cause of bad inpaint output is a hard-edged mask.
Apply `GrowMask` + `FeatherMask` (or `MaskBlur`) before encoding.

- **Hands/face fix**: grow by 4 px, feather by 8 px.
- **Background replacement**: grow by 8 px, feather by 16 px.
- **Small object removal**: grow by 12 px (cover the shadow too),
  feather by 8 px.

If the mask comes from segmentation (`SAM`, `SegFormer`), feathering is
critical — the model edges are pixel-perfect and produce visible seams.

## Prompt strategy

**Positive prompt** describes only what should appear *inside* the mask,
not the whole image. Example for "change the sky to sunset":

```
positive: dramatic sunset sky, orange and purple clouds,
          warm golden hour light
```

Do **not** include the parts of the image that are outside the mask
(the foreground, subject, etc.) — they're already preserved and adding
them risks the model regenerating the boundary visibly.

**Negative prompt** should target the *defect you're fixing*:

- Fixing hands: `extra fingers, missing fingers, fused fingers, deformed hands`
- Fixing eyes: `crossed eyes, asymmetric eyes, deformed pupils`
- Removing watermark: `watermark, signature, text, logo`

## Sampler + steps

Inpainting tolerates fewer steps than full generation — you're editing a
smaller area.

- `steps`: 18–25 (no need for 30+ unless detail-critical).
- `cfg`: 5–7 (keep modest; high CFG creates seam contrast).
- `sampler`: same as your base workflow. `dpmpp_2m` / `euler_ancestral`
  both work; don't switch unless the base workflow's choice is broken.

## Common failure modes

| Symptom                                  | Cause                              | Fix                                      |
|------------------------------------------|------------------------------------|------------------------------------------|
| Hard seam at the mask edge               | Mask not feathered                 | Add `FeatherMask` with 8–16 px           |
| The whole image changed                  | Wrong encoder, or used VAEEncode without SetLatentNoiseMask | Switch to `VAEEncodeForInpaint`          |
| Masked region looks unchanged            | `denoise` too low (recipe B)       | Raise to 0.6–0.8 and retry               |
| Color cast on the edited area            | CFG too high, or sampler mismatch  | Drop `cfg` to 5–6                        |
| Inpaint regenerates the wrong subject    | Positive prompt described the whole image | Re-prompt for the masked region only |
| Missing fingers / extra fingers          | Mask too tight on the hand         | Grow mask 4–8 px, then re-feather        |

## Decision summary

1. Identify the *outside-mask* content the user wants preserved.
2. Pick recipe A (full replace) or B (controlled blend) by how much
   should change.
3. Feather the mask in proportion to the edit's scale.
4. Write the positive prompt for **inside the mask only**.
5. Direct the negative prompt at the specific defect being fixed.
6. Use modest CFG (5–7) and standard steps (18–25).
7. If a seam appears, the mask wasn't feathered enough — re-do step 3.
