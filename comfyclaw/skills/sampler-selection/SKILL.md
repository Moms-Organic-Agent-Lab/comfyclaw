---
name: sampler-selection
description: >-
  Choose the right KSampler sampler_name + scheduler pair for the active
  checkpoint. Consult before changing sampler / scheduler / step count, when
  the verifier reports overcooked detail, grainy noise, color cast, or
  posterized output, or when a workflow defaults to dpmpp_2m + karras and
  the user wants faster / sharper / more creative results. Also use when
  the checkpoint family is unusual (LCM, Lightning, Turbo, Hyper-SD, DMD2)
  because those require non-standard sampler settings.
license: MIT
compatibility: ComfyClaw agent — requires KSampler node.
metadata:
  author: davidliuk
  version: "0.1.0"
---

# Sampler + scheduler selection

The sampler/scheduler pair sets the *character* of the diffusion run.
Wrong combinations either burn steps for no quality gain or produce
visibly worse images than the defaults. This skill picks safe defaults
plus when to deviate.

## The default that almost always works

For standard checkpoints (SD 1.5, SDXL, Flux, DiT models like Qwen,
HunyuanDiT, SD3), the workhorse pair is:

```
sampler_name: dpmpp_2m
scheduler:    karras
steps:        25–30
cfg:          6.5–7.5
```

`dpmpp_2m + karras` converges reliably across resolutions and prompt
styles, with good detail at 25 steps. Use this unless you have a
specific reason to switch.

## Detect the model family first

Step-and-CFG requirements vary by an order of magnitude across families.
Read the checkpoint name **before** touching sampler params.

| Family marker in name              | steps | cfg     | preferred sampler         | scheduler  |
|------------------------------------|-------|---------|---------------------------|------------|
| `lcm`                              | 4–8   | 1.0–2.0 | `lcm`                     | `sgm_uniform` |
| `lightning` (SDXL Lightning)       | 4–8   | 1.5–2.5 | `dpmpp_sde` / `euler`     | `sgm_uniform` |
| `turbo` (SDXL Turbo)               | 1–4   | 1.0–1.5 | `euler_ancestral`         | `simple`   |
| `hyper`, `hyper-sd`                | 4–8   | 1.5–3.0 | `dpmpp_2m_sde`            | `sgm_uniform` |
| `dmd2`                             | 4–8   | 1.0–2.0 | `lcm`                     | `sgm_uniform` |
| anything else (standard diffusion) | 25–35 | 6–8     | `dpmpp_2m`                | `karras`    |

Applying standard settings (steps=30, cfg=7) to LCM/Lightning/Turbo
**will** produce overcooked or saturated garbage. The companion skills
`dreamshaper8-lcm` and the checkpoint-specific guides override this one
when triggered.

## Picking by symptom

When the default works but the output has a specific defect:

| Symptom                                | Try                                              |
|----------------------------------------|--------------------------------------------------|
| Output looks plasticky / over-smooth   | Switch to `dpmpp_2m_sde` + `karras`              |
| Output is noisy / grainy at 25 steps   | Switch to `dpmpp_2m` + `karras` (you're already there?) — raise steps to 35 |
| Output is too tame / boring            | Switch to `euler_ancestral` + `normal`           |
| Output is too chaotic / overly variable| Switch to `dpmpp_2m` + `karras` (lock to a deterministic sampler) |
| Color cast (e.g., everything green-ish)| Drop `cfg` from 8+ to 6–7                        |
| Posterization / banding in gradients   | Switch `scheduler` to `karras` if currently `normal` |
| Hands/feet badly deformed              | Sampler change won't fix this — see `hires-fix` / `inpainting` |

## Sampler taxonomy (one-line each)

Use this to translate user requests into sampler choices.

- **`euler`** — classic deterministic; fast; sometimes flat.
- **`euler_ancestral`** — adds noise per step; more creative,
  less reproducible across seeds. Pair with `normal`.
- **`heun`** — slower euler variant; rarely worth the cost.
- **`dpmpp_2m`** — workhorse deterministic 2nd-order. Pair with `karras`.
- **`dpmpp_2m_sde`** — stochastic dpmpp_2m. Better surface texture
  (skin, fabric) at the cost of reproducibility.
- **`dpmpp_sde`** — stochastic 1st-order. Used for Lightning/Hyper.
- **`uni_pc`** / **`uni_pc_bh2`** — efficient at low steps (10–15).
  Pair with `karras` or `normal`.
- **`lcm`** — only for LCM-family models. Pair with `sgm_uniform`.
- **`ddim`** — classical, well-behaved. Pair with `ddim_uniform`.

## Scheduler taxonomy

The scheduler controls the noise schedule, not the algorithm.

- **`karras`** — most resilient. Use with `dpmpp_*`.
- **`normal`** — uniform-ish; pairs with `euler` and `euler_ancestral`.
- **`exponential`** — shifts detail to later steps. Useful for hires
  passes (low denoise + later detail).
- **`sgm_uniform`** — required for LCM, Lightning, DMD2.
- **`simple`** — for Turbo (1–4 step regime).
- **`ddim_uniform`** — only with `ddim`.

A wrong scheduler often manifests as the same image with worse details,
not as an error — so check this when output is "off" but you can't say
exactly how.

## Speed-vs-quality dial

When the user says "faster" or "make it quicker":

1. First, check the checkpoint — is there an LCM/Lightning/Turbo
   variant available? Switching model is 5–10× faster than tuning steps.
2. If the model is fixed:
   - 35 steps → 25 steps (rarely noticeable degradation).
   - 25 → 20 with `uni_pc` + `karras` if quality holds.
   - 20 → 15 only with verified outputs; below this, switch model family.

When the user says "sharper" or "more detail":

1. Raise steps from 25 → 35 (not beyond — diminishing returns).
2. Switch `dpmpp_2m` → `dpmpp_2m_sde` for surface texture.
3. If still soft, that's a `hires-fix` problem, not a sampler problem.

## Don't fight the workflow

If the loaded workflow uses a non-default sampler and the output is
already good, **leave it alone**. Sampler choice is a search-space
question; touching it can re-open issues the original author solved.
Only deviate when:

- A specific defect points to a known sampler issue (see symptom table).
- The model family was detected wrong (Turbo loaded with steps=30 etc.).
- The user explicitly asks for a different speed/quality trade-off.
