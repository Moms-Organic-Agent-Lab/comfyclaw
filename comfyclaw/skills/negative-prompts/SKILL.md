---
name: negative-prompts
description: >-
  Construct effective negative prompts for diffusion models. Use when the
  workflow has an empty or weak negative encoder, when the verifier reports
  defects the positive prompt can't fix (extra limbs, watermarks, low
  quality, wrong style bleed), when the user complains about "too cartoon",
  "too anime", "blurry", "deformed", "ugly", or simply asks to "improve the
  negative prompt". Especially trigger when the second CLIPTextEncode is
  empty — every workflow that supports negatives should use them.
license: MIT
compatibility: ComfyClaw agent — requires a negative CLIPTextEncode node.
metadata:
  author: davidliuk
  version: "0.1.0"
---

# Effective negative prompts

A weak or empty negative prompt is the single most common reason a
workflow produces "almost right but obviously off" output — extra
fingers, watermarks, low contrast, wrong style. The right negative is
defect-targeted, **not** a kitchen-sink of every bad word.

## When to add or rewrite the negative

- Negative encoder is empty or only has `"bad quality"` / `"low res"`.
- The output has a recurring defect (anatomy, text, style bleed,
  noise/grain).
- The user says "make it less anime", "less cartoony", "less plastic",
  "no watermark", "remove the text" — those are all negative-prompt
  jobs first, sampler/CFG jobs second.
- Switching model family (e.g., anime → photoreal) — old negatives are
  usually wrong for the new family.

## Don't dump a kitchen sink

The internet's most-shared negatives stack 40–60 tokens. They mostly
cancel out and dilute the signal. Strong negatives are short and
targeted.

Bad (cargo-culted):

```
worst quality, low quality, normal quality, lowres, blurry, bad anatomy,
bad hands, missing fingers, extra digit, fewer digits, cropped, jpeg
artifacts, signature, watermark, username, ugly, deformed, disfigured,
mutated, mutation, error, monochrome, grayscale, ...
```

Good (defect-targeted, 8–15 tokens):

```
deformed hands, extra fingers, missing fingers, watermark, text, blurry
```

Quality goes up because each token actually steers the model. Adding
more tokens past ~15 has diminishing returns and can introduce its own
artifacts.

## Building blocks by category

Combine 1–2 from each relevant category. Skip irrelevant categories.

### Anatomy (only when humans are in the image)

```
deformed hands, extra fingers, missing fingers, fused fingers,
asymmetric eyes, crossed eyes
```

Use **at most 4 tokens**. Anatomy negatives only help when humans are
in the prompt — otherwise they're noise.

### Quality (image-level defects)

```
blurry, low contrast, jpeg artifacts, noise, oversaturated
```

Skip "low quality", "worst quality", "lowres" — they're widely used but
empirically weak. Concrete defects work better than abstract quality
words.

### Style suppression (the user wants photoreal, prompt drifts to art)

```
cartoon, drawing, painting, anime, sketch, illustration, 3d render, cgi
```

Use the full block when the positive is explicitly photorealistic. Pair
with the `photorealistic` skill.

### Style suppression (user wants art, prompt drifts to photo)

```
photograph, photorealistic, dslr, raw photo, real
```

### Surface defects (one-off cleanup)

```
watermark, signature, text, logo, copyright, label
```

Add this **whenever the user uploads or references stock imagery**, or
when a previous run produced these. Most checkpoints were trained on
data with these defects and they leak in.

### Composition defects

```
cropped, out of frame, cut off, tiling, repeated subject
```

Use when the verifier reports the subject is cut at the edge or there
are unwanted multiples.

### Background bleed

```
busy background, cluttered background, distracting background
```

Use when the user asked for a clean / studio / minimal scene and the
output has visual noise behind the subject.

## Family-specific gotchas

- **SDXL**: SDXL's CLIP-G encoder handles short prompts well; long
  kitchen-sink negatives sometimes degrade output more than help. Stay
  under 15 tokens.
- **Flux / DiT models (Qwen, HunyuanDiT, SD3)**: many of these accept
  natural-language negatives. `"no text, no watermark, no extra limbs"`
  outperforms tag lists.
- **LCM / Turbo / Lightning**: very low CFG (1–2) means negatives have
  weak influence. Keep them minimal (3–5 tokens) — they barely steer at
  these CFGs.
- **Anime checkpoints (Pony, NoobAI, Illustrious)**: use the canonical
  quality negatives that these models were trained on, e.g.
  `"score_4, score_3, source_furry, censored, monochrome"`. Generic
  negatives are weaker here. Confirm the exact tokens from the model
  card.

## CFG interaction

Negative prompts are scaled by CFG. If CFG is low (≤3), negatives are
almost ignored — don't bother stacking. If CFG is high (≥9), even mild
negatives become aggressive and can cause color casts; trim to
essentials.

Sane defaults:
- `cfg 5–7` → 8–15 negative tokens is healthy.
- `cfg 7–8` → cap at 10 tokens; verify no oversaturation.
- `cfg ≥ 9` → 5–8 tokens max; consider lowering CFG instead.

## Decision rules

1. Identify the **specific defect** the user is complaining about (or
   that the verifier reported). Negative prompts target defects, not
   abstract quality.
2. Pick **one category block** that matches the defect; copy 1–3 tokens.
3. Add **surface-defect tokens** (`watermark, text, signature`) unless
   the prompt explicitly calls for them.
4. Add **anatomy tokens** only if humans are in the prompt.
5. **Keep it under 15 tokens.** If you find yourself adding more,
   re-read the positive prompt — the fix is probably there, not here.
6. Verify CFG is in a range where negatives matter (5–7); raising CFG
   is not a substitute for a precise negative.
