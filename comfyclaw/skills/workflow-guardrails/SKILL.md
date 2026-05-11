---
name: workflow-guardrails
description: >-
  Preflight wiring, enum, and parameter checks that prevent the most common
  ComfyUI validation failures. Consult before connecting CheckpointLoaderSimple
  outputs, wiring KSampler / VAE / LoraLoader, editing dropdown enum values
  (sampler_name, scheduler, upscale_method), setting strength / cfg / denoise
  parameters, or whenever you see errors like prompt_outputs_failed_validation,
  exception_during_inner_validation, value_not_in_list, or string-index errors.
  Read this before any structural edit; it is faster to validate than to debug.
license: MIT
compatibility: ComfyClaw agent — applies to all ComfyUI versions and node sets.
metadata:
  author: davidliuk
  version: "0.1.0"
  provenance: >-
    Distilled from IntelligenceLab/ComfyAgent-Zongxia evolved_skills runs
    (phase1_run1_cold, claude-sonnet-4-5_batched_v1, _legacy_nonbatched).
---

# ComfyUI workflow guardrails

ComfyUI validates connections by **type** and dropdowns by **exact string
match**. Most "exception_during_inner_validation" /
"prompt_outputs_failed_validation" failures fall into a handful of
patterns. Run through this checklist before sending a graph for execution.

## 1. Output-slot reference (must be exact, 0-indexed)

| Node                       | Slot 0 | Slot 1 | Slot 2 |
|----------------------------|--------|--------|--------|
| `CheckpointLoaderSimple`   | MODEL  | CLIP   | VAE    |
| `LoraLoader`               | MODEL  | CLIP   | —      |
| `VAELoader`                | VAE    | —      | —      |
| `CLIPTextEncode`           | CONDITIONING | — | —    |
| `KSampler`                 | LATENT | —      | —      |
| `VAEDecode`                | IMAGE  | —      | —      |
| `EmptyLatentImage`         | LATENT | —      | —      |
| `LoadImage`                | IMAGE  | MASK   | —      |

Count slots from **0**, not 1. The single most frequent wiring bug is
feeding `CheckpointLoaderSimple[1]` (CLIP) into `KSampler.model` — silently
wrong, fails validation only at run.

## 2. Type-based wiring rules

Connections are matched on type, not on name proximity.

**Never**

- `MODEL` ↔ `CLIP` ↔ `VAE` — never cross these wires.
- `CONDITIONING` → `VAE` input.
- `LATENT` → `SaveImage` (must decode first via `VAEDecode`).
- `IMAGE` → `KSampler.latent_image` (must encode first via `VAEEncode`).
- `IMAGE` → anything expecting `MASK`.

**Always**

- `MODEL` → `KSampler.model` (and `LoraLoader.model`).
- `CLIP` → `CLIPTextEncode.clip` (both positive and negative encoders).
- `VAE` → `VAEDecode.vae` and `VAEEncode.vae`.
- `LATENT` → `VAEDecode.samples` before any image output.

## 3. The safe text-to-image skeleton

When in doubt, fall back to this topology:

```
CheckpointLoaderSimple
  [0] MODEL → KSampler.model
  [1] CLIP  → CLIPTextEncode(positive).clip
  [1] CLIP  → CLIPTextEncode(negative).clip
  [2] VAE   → VAEDecode.vae

EmptyLatentImage [0] LATENT → KSampler.latent_image
CLIPTextEncode(positive) [0] CONDITIONING → KSampler.positive
CLIPTextEncode(negative) [0] CONDITIONING → KSampler.negative

KSampler [0] LATENT → VAEDecode.samples
VAEDecode [0] IMAGE → SaveImage.images
```

For img2img, replace `EmptyLatentImage` with `LoadImage → VAEEncode` using
the **same** `VAE` from slot 2.

When a `LoraLoader` is inserted, route downstream from the loader's
outputs, not the checkpoint's:

```
CheckpointLoaderSimple[0] MODEL → LoraLoader.model
CheckpointLoaderSimple[1] CLIP  → LoraLoader.clip
LoraLoader[0] MODEL → KSampler.model       # not the checkpoint's MODEL
LoraLoader[1] CLIP  → CLIPTextEncode.clip  # not the checkpoint's CLIP
```

## 4. Enum fields — copy exact strings

Dropdown inputs reject anything not in the node's allowed list. Symptoms:

- `prompt_outputs_failed_validation`
- `value_not_in_list`
- `upscale_method: 'C' not in [...]`
- `string index out of range`

Common enums and a sample of legal values (always confirm against the
node, not from memory):

- `upscale_method`: `nearest-exact`, `bilinear`, `area`, `bicubic`, `lanczos`
- `sampler_name`: `euler`, `euler_ancestral`, `dpmpp_2m`, `dpmpp_sde`,
  `dpmpp_2m_sde`, `uni_pc`, etc.
- `scheduler`: `normal`, `karras`, `exponential`, `sgm_uniform`,
  `simple`, `ddim_uniform`

**Rules**

- Never abbreviate (no `"C"`, no `"A"`, no `"linear"` if not in the list).
- Preserve case, hyphens, and underscores literally.
- If you need a value, read it from the node, do not guess.

## 5. Numeric parameter ranges

| Parameter                    | Valid range          | Notes                            |
|------------------------------|----------------------|----------------------------------|
| `seed`                       | integer ≥ 0          | `-1` is *not* random on all nodes|
| `steps`                      | integer, typ. 8–60   | LCM models want 4–8              |
| `cfg`                        | float, typ. 1–20     | LCM models want 1–2              |
| `denoise`                    | 0.0 – 1.0            | Strictly enforced                |
| `conditioning_*_strength`    | 0.0 – 1.0            | Frequently violated — clamp it   |
| `width` / `height`           | integer, multiple of 8 | Always; some nodes require 64 |

When uncertain, use a conservative default (e.g. `1.0` for a strength,
`30` for steps, `7.5` for cfg). Out-of-range values fail validation
loudly; pushing the boundary rarely improves quality.

## 6. Don't leave stale wires after a swap

The most insidious runtime failures come from replacing a node but
keeping incompatible upstream/downstream links — every edge needs to be
re-checked when you swap any of:

- `CheckpointLoaderSimple` (re-wire MODEL/CLIP/VAE downstream)
- `KSampler` (re-wire positive/negative/latent/MODEL/LATENT)
- `VAEDecode` / `VAEEncode` (re-wire VAE and the IMAGE/LATENT pair)
- `LoraLoader` insertion or removal (downstream consumers need the new
  MODEL/CLIP, not the checkpoint's)

After any structural swap, re-verify every edge that touched the changed
node. A graph with stale wires often passes the type check on individual
nodes but fails inner validation at run.

## 7. Debugging "exception_during_inner_validation"

1. Find the `node_errors` payload in the response — it names the failing
   node ID.
2. Look at that node's inputs: type mismatch or wrong slot index?
3. Trace each upstream edge — every input must be connected when the node
   declares it required.
4. Check parameter types — strings vs. integers vs. floats matter, even
   when both look numeric.
5. Confirm any dropdown values against the node's enum list.

## Quick preflight checklist

Before sending a workflow:

- [ ] Every `MODEL` consumer is connected from a `MODEL` output (slot 0
      on a checkpoint, slot 0 on a LoraLoader).
- [ ] Every `CLIP` consumer is connected from a `CLIP` output.
- [ ] Every `VAE` consumer is connected from a `VAE` output.
- [ ] No `LATENT` goes anywhere except into a sampler or `VAEDecode`.
- [ ] All enum fields hold strings copied verbatim from the node.
- [ ] Strengths and `denoise` are in `[0, 1]`; `cfg` in `[1, 20]`.
- [ ] After any node swap, every touching edge has been re-checked.

This list catches the majority of validation failures in one pass.
