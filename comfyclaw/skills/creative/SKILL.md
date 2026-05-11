---
name: creative
description: >-
  Set sampler parameters and prompt tags for vivid, concept-art-style outputs.
  Use when the user asks for "creative", "artistic", "fantasy", "concept art",
  "surreal", "stylized", or any clearly fictional scene where photorealism
  would be wrong. Pair with prompt-artist to also enrich the prompt text —
  this skill handles only the workflow settings.
license: MIT
compatibility: ComfyClaw agent — requires KSampler and CLIPTextEncode nodes.
metadata:
  author: davidliuk
  version: "0.2.0"
---

Apply these adjustments to unlock the model's artistic rendering mode.

**1. Append concept-art tokens to the positive prompt**

```
, concept art, vivid colors, dynamic composition, highly detailed digital painting, trending on artstation
```

**2. Extend the negative prompt**

```
bland, boring, plain, flat colors, low detail, photorealistic, photograph
```

Adding "photorealistic" and "photograph" to the negative is important — without
them the model often gravitates toward realism even with art tokens present.

**3. Lower KSampler `cfg` to 6.5**

Tighter CFG (7+) constrains the model toward literal prompt interpretation.
6.5 gives it room to make creative associations and produce unexpected details.

**4. Set sampler to `euler_ancestral`**

The stochastic nature of euler_a produces organic variation and "happy
accidents" that deterministic samplers (dpmpp_2m) smooth away.

**5. Change the seed**

Pick any new integer. Staying on the same seed after parameter changes often
locks the output into the same compositional attractor; a fresh seed explores
a wider region of the model's output space.
