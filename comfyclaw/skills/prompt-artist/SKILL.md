---
name: prompt-artist
description: >-
  Rewrite a flat or sparse prompt into vivid, multi-dimensional artistic language.
  Use when the user asks for "creative", "aesthetic", "dreamy", "artistic",
  "masterpiece", "professional art", "award-winning", "concept art", "surreal",
  "futuristic", or "imaginative" images — or whenever the prompt feels too plain
  and the output would benefit from richer visual storytelling. Trigger this even
  when the user just says "make it look cooler" or "give it more artistic depth".
  Returns only the enhanced prompt text; pair it with high-quality or photorealistic
  to also tune the workflow parameters.
license: MIT
compatibility: ComfyClaw agent — pure prompt rewrite, returns enhanced text only.
metadata:
  author: davidliuk
  version: "0.2.0"
---

Choose the right mode based on what the user is asking for, then rewrite the
prompt accordingly. Return only the final enhanced prompt — no preamble, labels,
or explanation.

## Mode A — Creative spark (default)

Use for casual creative requests: "cool", "dreamy", "creative", "artistic", "surreal".
Apply these six principles to transform the base idea:

1. **Originality** — introduce something unexpected; mix unrelated concepts or a
   fresh perspective the base prompt doesn't suggest.
2. **Expressiveness** — weave in emotional cues (awe, curiosity, nostalgia, mystery)
   that give the image a clear mood.
3. **Aesthetic appeal** — describe composition, lighting, and colour balance that
   make the scene visually engaging, not just descriptive.
4. **Technical craft** — signal intentional skill: sharp focus, intentional depth of
   field, refined textures — whatever fits the medium.
5. **Surprising association** — blend two or more unrelated elements in a way that
   feels inventive rather than random.
6. **Interpretive depth** — allow more than one reading; hint at hidden meaning or
   relationships between elements.

## Mode B — Aesthetic masterpiece

Use for prestige requests: "masterpiece", "award-winning", "professional art",
"ArtiMuse-grade", or when maximum aesthetic scoring is the goal.
Integrate all eight dimensions:

1. **Composition** — establish visual hierarchy with a clear focal point; use
   leading lines, rule of thirds, or asymmetric balance.
2. **Color & light** — specify the palette and illumination precisely
   (e.g. "warm golden-hour side-lighting with cool blue shadows").
3. **Technical execution** — name the medium and its mastery
   (e.g. "precise ink crosshatching", "subsurface scattering on skin").
4. **Originality** — push beyond common tropes; suggest a crossover or
   reinterpretation that feels genuinely novel.
5. **Narrative** — embed a clear theme, symbolic object, or cultural reference
   that the viewer can decode.
6. **Emotional atmosphere** — use sensory language that evokes a specific feeling
   (e.g. "tranquil melancholy", "electric anticipation").
7. **Gestalt unity** — every element — style, palette, subject — should feel
   like parts of the same coherent whole.
8. **Layered depth** — add details that reward close inspection: background
   narrative, textural complexity, or subtle symbolic motifs.

## Gotcha

Do not add camera/lens specifications (85mm, f/1.8, RAW) here — those belong
in the `photorealistic` skill. This skill rewrites narrative and artistic
language; hardware specs belong to a different register.
