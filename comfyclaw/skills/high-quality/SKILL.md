---
name: high-quality
description: >-
  Raise output fidelity by tuning prompt tokens, sampler parameters, and
  resolution. Use when the user asks for "high quality", "detailed", "sharp",
  "crisp", "professional", or "8K" — or when the verifier reports soft detail,
  noise, or underwhelming fidelity. Layer this on top of photorealistic or
  creative; the parameter changes are complementary.
license: MIT
compatibility: ComfyClaw agent — requires KSampler and CLIPTextEncode nodes.
metadata:
  author: davidliuk
  version: "0.2.0"
---

Apply these five adjustments. Each targets a distinct failure mode.

**1. Append quality tokens to the positive prompt**

```
, masterpiece, best quality, highly detailed, 8k uhd, sharp focus, professional photography
```

These tokens shift the model's output distribution toward high-fidelity training
examples. Add them after the subject description, not at the start.

**2. Set the negative prompt**

```
blurry, low quality, bad anatomy, watermark, signature, jpeg artifacts, noise, overexposed
```

If no negative CLIPTextEncode node exists yet, create one and wire it to
the KSampler's `negative` input.

**3. Set KSampler `steps` to 25**

Below 20 steps fine detail is visibly under-resolved. 25 is the sweet spot
between quality and generation time; go to 30 only if the workflow already
runs fast.

**4. Set KSampler `cfg` to 7.0**

Below 6.5 detail gets soft; above 8.5 colour saturation and anatomy
deteriorate. 7.0 gives crisp adherence without artifacts.

**5. Set resolution to 1024×1024 in the latent image node**

`EmptyLatentImage` or `EmptySD3LatentImage` — use whichever is present.
768×768 is the floor; 1024 gives the model enough room for fine detail.
Avoid going above 1536 unless the model specifically supports it, as it
triggers tiling artifacts.
