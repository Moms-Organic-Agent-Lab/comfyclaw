---
name: dreamshaper8-lcm
description: >-
  Configuration guide for DreamShaper 8 LCM (Latency Consistency Model).
  MUST be read before tuning any sampler parameter when the active checkpoint
  contains "dreamshaper" and "lcm" in its filename. LCM models use a
  completely different sampler, step count, and CFG range than standard
  diffusion models — applying standard settings (steps=30, cfg=7.5, dpmpp_2m)
  will produce over-saturated, artifact-ridden images or fail outright.
license: MIT
compatibility: ComfyClaw agent — SD 1.5 base, CheckpointLoaderSimple node.
metadata:
  author: davidliuk
  version: "1.0.0"
  base_arch: SD 1.5
  model_file: dreamshaper8_lcm.safetensors
---

DreamShaper 8 LCM is a Latency Consistency Model fine-tuned on top of
DreamShaper 8. It excels at photorealistic portraits, fantasy art, and
cinematic scenes in **4–8 steps** instead of the usual 20–30.

## ⚠️ Critical: LCM requires different settings than standard SD

Standard diffusion skills (photorealistic, high-quality) set steps=30 and
cfg=7.5. **These values will break LCM generation.** Always apply this skill's
settings instead, then layer prompt improvements on top.

---

## 1. Sampler — use `lcm` with `sgm_uniform`

```
set_param(node_id=<KSampler_id>, param_name="sampler_name", value="lcm")
set_param(node_id=<KSampler_id>, param_name="scheduler",    value="sgm_uniform")
```

`lcm` is the only sampler that engages the LCM diffusion path.
`sgm_uniform` gives the most stable step spacing for LCM.
Never use `dpmpp_2m`, `euler`, or `ddim` — they bypass LCM and produce
muddy results requiring 30+ steps to look acceptable.

---

## 2. Steps — keep between 4 and 8

| Quality goal | steps |
|---|---|
| Fast draft / test | 4 |
| Standard quality | 6 |
| Best quality | 8 |

More than 8 steps does **not** improve quality and wastes GPU time. Set to 6
as a reliable default.

```
set_param(node_id=<KSampler_id>, param_name="steps", value=6)
```

---

## 3. CFG — keep between 1.5 and 2.5

| Prompt adherence | cfg |
|---|---|
| Loose / creative | 1.5 |
| Balanced (default) | 2.0 |
| Tight prompt follow | 2.5 |

LCM encodes guidance directly into the distillation weights. A CFG above 3.0
causes oversaturation, blown highlights, and colour banding. Never exceed 3.0.

```
set_param(node_id=<KSampler_id>, param_name="cfg", value=2.0)
```

---

## 4. Resolution — 512×512 native, 768×768 max without hires-fix

DreamShaper 8 is an SD 1.5 model trained at 512×512. It can handle 768×768
without hires-fix but shows composition issues above that.

```
set_param(node_id=<EmptyLatentImage_id>, param_name="width",  value=512)
set_param(node_id=<EmptyLatentImage_id>, param_name="height", value=512)
```

Use 768×768 for portrait/landscape shots that benefit from aspect ratio.
**For higher resolution, always apply hires-fix (read_skill("hires-fix"))
after the base pass.** The hires-fix KSampler should also use lcm + sgm_uniform.

---

## 5. Prompt style — DreamShaper 8 responds well to

**Positive prompt structure:**
```
<subject>, <style keywords>, <lighting>, <quality tokens>
```

Effective quality tokens for this model:
```
, masterpiece, best quality, ultra detailed, sharp focus
```

DreamShaper 8 LCM does NOT need camera lens tokens (85mm, f/1.8) to look
photorealistic — the model is fine-tuned for this aesthetic already.
Skip them to avoid over-conditioning.

**Negative prompt:**
```
ugly, deformed, noisy, blurry, low quality, watermark, text, (worst quality:1.4)
```

Parenthesised weights `(token:weight)` work with this model's CLIP. Use
`(worst quality:1.4)` rather than just `worst quality` for stronger rejection.

---

## 6. Hires-fix for LCM — adapt the second KSampler

When adding hires-fix on top of an LCM workflow, the second KSampler **must
also use the LCM sampler**:

```
set_param(node_id=<HiresKSampler_id>, param_name="sampler_name", value="lcm")
set_param(node_id=<HiresKSampler_id>, param_name="scheduler",    value="sgm_uniform")
set_param(node_id=<HiresKSampler_id>, param_name="steps",        value=4)
set_param(node_id=<HiresKSampler_id>, param_name="denoise",      value=0.45)
```

Denoise 0.45 adds detail without changing the composition. Use 0.55–0.65 only
if the verifier reports soft texture.

---

## Summary checklist

- [ ] `sampler_name` = `lcm`
- [ ] `scheduler` = `sgm_uniform`
- [ ] `steps` = 6 (range 4–8)
- [ ] `cfg` = 2.0 (range 1.5–2.5, never > 3.0)
- [ ] Resolution ≤ 768×768 without hires-fix
- [ ] Hires-fix KSampler also uses lcm + sgm_uniform
