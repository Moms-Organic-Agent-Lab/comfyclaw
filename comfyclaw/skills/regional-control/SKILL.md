---
name: regional-control
description: >-
  Apply separate text prompts to distinct image regions to prevent style
  contamination between subject and background. Use when the verifier reports
  subject and background look mismatched, scene elements compete stylistically,
  background is too plain or generic relative to the subject, or fix_strategy
  contains "add_regional_prompt". Also useful when the user explicitly asks for
  "different styles in different parts" of the image.
license: MIT
compatibility: ComfyClaw agent — modifies CLIPTextEncode conditioning graph.
allowed-tools: add_regional_attention set_param
metadata:
  author: davidliuk
  version: "0.2.0"
---

Regional conditioning lets different areas of the image follow different
textual descriptions — the model stops treating the whole canvas as a single
prompt and instead handles each region semi-independently.

## Default approach — BREAK token

Start here. It requires no new nodes and works with any SD model:

Format the positive CLIPTextEncode text as:

```
[foreground prompt] BREAK [background prompt]
```

The model allocates roughly equal attention to each section. This is a soft
separation — good enough for style bleed and mismatched backgrounds.

**Example:**

```
cute tabby cat, detailed fur texture, soft rim lighting, sharp focus
BREAK
sunlit wooden windowsill, warm afternoon bokeh, shallow depth of field
```

## Power option — ConditioningCombine

Use when BREAK doesn't resolve the bleed, or when the foreground needs
significantly more weight than the background.

```
add_regional_attention(
  foreground_prompt = "…",
  background_prompt = "…",
  foreground_weight = 1.3,   # 1.2–1.5; higher = more subject emphasis
)
```

This creates two CLIPTextEncode nodes, a ConditioningAverage to weight the
foreground, and a ConditioningCombine that merges both. The output replaces
the existing positive conditioning input on KSampler.

## Writing regional prompts

- **Foreground**: subject + quality modifiers + subject-specific lighting
- **Background**: environment + mood — avoid repeating subject terms
- Keep each section concise; complex regional prompts create conflicting signals

## Gotchas

Repeating the same keywords in both regions causes blending rather than
separation — the model averages them. Keep the vocabulary distinct.
Do not put negative terms in regional prompts; they belong in the main
negative conditioning node.
