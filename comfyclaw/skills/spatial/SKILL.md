---
name: spatial
description: >-
  Rewrite prompts to explicitly encode spatial relationships and physical
  arrangements between objects. Use when the user describes scenes with
  multiple elements using words like "on the left/right", "above/below",
  "behind/in front of", "jumping over", "in a circle", "partially hidden",
  or when multiple characters or objects need to interact in a specific
  layout. Diffusion models struggle with implicit spatial language — making
  it explicit here dramatically reduces positioning errors.
license: MIT
compatibility: ComfyClaw agent — pure prompt rewrite, no node changes.
metadata:
  author: davidliuk
  version: "0.2.0"
---

Rewrite the prompt by covering the spatial dimensions relevant to the scene.
Not all ten dimensions apply to every scene — include only those that matter
for this particular layout.

## Spatial dimension reference

| Dimension | What to specify | Example |
|---|---|---|
| **S1 Object list** | Name every distinct object | "a red apple, a wooden table, a ceramic bowl" |
| **S2 Attribution** | Color, material, texture per object | "matte black, rough oak, translucent glass" |
| **S3 Position** | Absolute or relative placement | "centered top-third", "to the left of X" |
| **S4 Orientation** | Facing direction, rotation | "facing left", "upside down", "profile view" |
| **S5 Group layout** | Arrangement of multiple items | "arranged in a V-shape", "stacked", "in a row" |
| **S6 Relative size** | Comparisons between objects | "twice as tall as X", "smaller than Y" |
| **S7 Proximity** | Physical distance | "touching", "5 cm apart", "far in the background" |
| **S8 Occlusion** | 3D layering and depth | "X partially behind Y", "hidden below the table" |
| **S9 Motion state** | Dynamic or mid-action poses | "mid-jump", "pouring liquid", "arm mid-swing" |
| **S10 Causal link** | Cause → visible effect | "wind blowing → cloak billowing to the right" |

## Output format

Write the result as a **single fluent paragraph** — no bullet points. Replace
vague spatial words ("nearby", "next to") with precise descriptions
("standing 30 cm to the right of X, facing toward it").

## Consistency check

Before outputting, verify there are no circular contradictions
(e.g. "A is left of B" AND "B is left of A"). Fix any that exist.
