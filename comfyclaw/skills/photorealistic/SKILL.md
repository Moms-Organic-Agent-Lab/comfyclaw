---
name: photorealistic
description: >-
  Tune prompts and sampler settings to produce camera-realistic images with
  natural lighting and DSLR-like quality. Use when the user describes a
  real-world scene or uses words like "photo", "photograph", "realistic",
  "DSLR", "cinematic", "RAW photo", or "shot on [camera]". Trigger this even
  when the user just says "make it look real" or "like a real picture" — this
  is one of the most frequent image-quality requests.
license: MIT
compatibility: ComfyClaw agent — requires KSampler and CLIPTextEncode nodes.
metadata:
  author: davidliuk
  version: "0.2.0"
---

Apply these five adjustments to push the model into its photorealistic mode.

**1. Append camera descriptors to the positive prompt**

```
, RAW photo, DSLR, 85mm lens, f/1.8, natural lighting, photorealistic, hyper detailed
```

Lens and aperture tokens strongly condition the model toward photographic
rendering. Add after the subject.

**2. Set the negative prompt to block artistic rendering modes**

```
cartoon, drawing, painting, anime, sketch, illustration, 3d render, cgi, watercolor
```

Without these, the model will often default to painterly or illustrative
styles even with photorealistic tokens in the positive prompt.

**3. Set KSampler `steps` to 30**

Photorealism requires finer diffusion than artistic styles. 30 steps
significantly reduces grain and softness versus the default 20.

**4. Set KSampler `cfg` to 7.5**

Slightly higher than the general-quality default (7.0) for tighter prompt
adherence — real photography has a very specific look that looser settings
won't capture.

**5. Set sampler to `dpmpp_2m` with `karras` scheduler**

This combination produces the smoothest photorealistic gradients and skin
tones. If `dpmpp_2m` is not available, `dpmpp_sde` is the next best choice.
