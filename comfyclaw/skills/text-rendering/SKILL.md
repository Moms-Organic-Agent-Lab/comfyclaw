---
name: text-rendering
description: >-
  Restructure prompts for accurate in-image text rendering. Use when the user
  wants specific text, quotes, signs, labels, or logos to appear in the
  generated image — including phrases like "a sign saying...", "text on the
  shirt", "a logo with the word", or whenever the user places target text
  in quotation marks. Text in diffusion models fails silently without careful
  prompt structure; this skill prevents garbled or missing characters.
license: MIT
compatibility: ComfyClaw agent — pure prompt rewrite, no node changes.
metadata:
  author: davidliuk
  version: "0.2.0"
---

Diffusion models treat text as shapes, not language. Getting clean results
requires telling the model exactly where the text sits, what surface it's on,
what the typography looks like, and what the characters are — in that order.

## Required elements (always include all five)

**1. Position the text explicitly**

Describe where in the frame the text lives:
> "centered on the upper third of the image", "bottom-right corner", "wrapped around the circular border"

For multiple text elements, establish a clear hierarchy (headline vs. subtitle).

**2. Break long text into lines**

Don't let the model guess line breaks. State the structure explicitly:
> "a two-line inscription: first line reads 'OPEN', second line reads '24/7'"

**3. Name the surface and its texture**

What is the text written on, and how does it interact with that surface?
> "carved into weathered stone", "glowing neon on a dark storefront", "embroidered on silk fabric"

The surface texture directly affects how the letters look.

**4. Specify the typographic style**

Name font weight, style, and material:
> "bold condensed sans-serif", "elegant italic calligraphy", "3D chrome block letters", "distressed stencil"

Add colour and finish: "matte white", "glowing gold", "translucent frosted".

**5. Use directive verbs and quote the target text**

Use verbs like *rendered*, *inscribed*, *embossed*, *spelled out*, *stenciled*,
and always wrap the target text in double quotes:
> the sign is inscribed with the words "WELCOME HOME"

## Gotcha — keep text short

Models handle ≤ 8–10 characters reliably. Longer text is error-prone.
If the user needs a full sentence, warn them and suggest splitting it
across multiple visual elements (headline + subtitle, for example).
