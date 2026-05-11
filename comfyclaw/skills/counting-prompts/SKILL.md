---
name: counting-prompts
description: >-
  Strategy for prompts that contain exact object counts, multi-subject
  conjunctions, or unusual attribute bindings. Use when the prompt has a
  number word ("two", "seven", "exactly N"), joins subjects with "and" /
  "|" / "+", pairs a color or material with an unlikely noun
  ("green croissants", "metal toys", "wooden apples"), or when the
  verifier reports missing objects, wrong counts, or fused subjects.
  Defaults to a verification-first lightweight approach and escalates to
  regional-control only when the simple recipe fails.
license: MIT
compatibility: ComfyClaw agent — works on any text-to-image checkpoint.
metadata:
  author: davidliuk
  version: "0.1.0"
  provenance: >-
    Distilled from IntelligenceLab/ComfyAgent-Zongxia evolved_skills runs
    (phase1_run1_cold, claude-sonnet-4-5_batched_v1, _legacy_nonbatched).
---

# Counting + multi-subject prompts

Prompts that combine **counts**, **multiple subjects**, or **unusual
attribute bindings** are the most common failure mode of base diffusion
models. The strongest empirical pattern is *try the lightweight fix first,
verify, escalate only on failure*.

## Detection — when this skill fires

Trigger when the user prompt matches any of:

- **Counted objects**: a number word (`"two"`, `"seven"`, `"exactly N"`)
  attached to a countable noun.
- **Multi-subject conjunctions**: `"X and Y"`, `"X | Y"`, `"X + Y"`, or two
  distinct nouns separated by punctuation.
- **Unusual attribute–noun pairs**: a color, material, or quality that is
  improbable for the noun (`"green croissants"`, `"metal toys"`,
  `"wooden apple"`, `"bright green backpack"`).

If the verifier later reports *missing objects*, *wrong counts*, or
*fused subjects*, treat that as a delayed trigger for this skill too.

## Lightweight recipe — always try first

The base model handles these prompts well when the total object count is
**under ~8** and the conjunction is explicit. Skip regional control and
LoRAs on the first pass.

1. Build a standard workflow (single positive `CLIPTextEncode`).
2. Restate counts and attributes aggressively in the positive prompt:
   - Counts: `"exactly N <noun>"`, not just `"N <noun>"`.
   - Unusual attributes: repeat the binding close to the noun
     (`"green croissant, green pastry, all croissants are green"`).
3. Use targeted negatives:
   - `"no extra objects, no missing objects"`
   - `"more than N, less than N"` when count is critical
   - `"no people, no animals, no text"` when irrelevant subjects keep
     leaking in
4. Generate and verify each constraint **separately**:
   - `"Are there exactly N <object>?"`
   - `"Is each <object> clearly visible and distinct?"`
   - `"Does each <object> have the required attribute?"`

**Why this works**: explicit count words plus distinct object categories
give the base model enough signal. Regional control adds latency and can
hurt overall composition; only spend it when verification fails.

## Escalation — when the lightweight recipe fails

Add complexity in this order, one step at a time:

1. **Hires-fix** (read the `hires-fix` skill) when structure or fine
   detail is wrong but the count is approximately right.
2. **Unusual-attributes reinforcement** when the model keeps drifting to
   the canonical color/material (e.g., croissants going brown).
3. **Regional-control** (read the `regional-control` skill) only when:
   - Total object count ≥ 8, or
   - Two subjects are fusing into one, or
   - Style bleed between subject and background is visible, or
   - The lightweight recipe failed verification *twice* on the same
     constraint.
4. **LoRA discovery** — call `query_available_models("loras")` and use a
   LoRA only when it directly matches the subject or style. Start
   conservatively (`0.6–0.8` strength) and avoid stacking.

## Sampler defaults

Counted/multi-subject prompts are sensitive to high CFG. Keep:

- `steps`: 25–35
- `cfg`: 6–8
- `sampler`: whatever the workflow already uses (don't experiment here)
- Avoid CFG > 9 — it destabilizes composition and inflates count errors.

## Concrete recipes

### `a green backpack and a pig`

Two distinct subjects, mixed style risk.

```
positive: bright green backpack, clearly visible, distinct subject,
          pig, clearly visible, distinct subject, simple background
negative: no extra objects, no text, no watermark
```

If verification reports a fused subject, then add regional control.

### `seven green croissants`

Count + unusual attribute on the same noun.

```
positive: exactly seven croissants, all green, green croissants,
          arranged clearly, top-down view
negative: extra croissants, missing croissants, brown croissants,
          other colors, text, people
```

If structure is right but detail is soft, add hires-fix. Save LoRAs for
last.

### `two metal toys`

Count + material + risk of unrelated subject classes (people, animals).

```
positive: exactly two metallic toy objects, shiny metal, toy-like,
          isolated, neutral background
negative: people, animals, text, extra objects, more than two,
          less than two, plastic, fabric
```

If the model still produces three or one, restate as
`"exactly two — no more, no less"` in the positive prompt before
escalating to regional control.

## Decision summary

| Symptom from verifier             | Next action                                |
|-----------------------------------|--------------------------------------------|
| Wrong count, <8 objects           | Tighten prompt + negatives, regenerate     |
| Wrong count, ≥8 objects           | Add regional-control                       |
| Subjects fused into one           | Add regional-control                       |
| Right count, soft/blurry detail   | Add hires-fix                              |
| Attribute ignored (color/material)| Repeat attribute close to noun, re-prompt  |
| Failed twice on same constraint   | Add regional-control                       |
| Domain-specific LoRA available    | Use it at 0.6–0.8 strength, only one       |

## Priority order

1. Sharpen prompt wording first.
2. Strengthen negatives second.
3. Hires-fix for detail loss.
4. Regional control only after the first two fail.
5. Sampler tuning last.

This is the repeatable high-score path across counting and multi-subject
prompts.
