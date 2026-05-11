---
name: qwen-image-2512
description: >-
  Configuration guide for Qwen-Image-2512, Alibaba's top-ranked open-source
  text-to-image model (Dec 2025). Detect this model when the workflow contains
  UNETLoader with "qwen_image" in the model name, or CLIPLoader with
  type "qwen_image". Uses standard ComfyUI nodes (UNETLoader, CLIPLoader,
  VAELoader, KSampler, EmptySD3LatentImage) with FP8 quantized weights.
license: Apache-2.0
metadata:
  author: davidliuk
  version: "2.0.0"
  base_arch: Qwen-Image (custom DiT + Qwen2.5-VL-7B text encoder, FP8 quantized)
  diffusion_model: qwen_image_2512_fp8_e4m3fn.safetensors  → ComfyUI/models/diffusion_models/
  text_encoder:    qwen_2.5_vl_7b_fp8_scaled.safetensors   → ComfyUI/models/text_encoders/
  vae:             qwen_image_vae.safetensors                → ComfyUI/models/vae/
  optional_lora:   Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors → ComfyUI/models/loras/
---

Qwen-Image-2512 is Alibaba's #1-ranked open-source T2I model on AI Arena (Dec 2025).
The official ComfyUI integration uses **standard native ComfyUI nodes** with FP8
quantized weights (~28 GB total), fitting comfortably in 45 GB VRAM.

## ⚠️ Architecture differences vs SD/SDXL

| Feature | SD 1.5 / SDXL | Qwen-Image-2512 |
|---|---|---|
| Model loader | `CheckpointLoaderSimple` | `UNETLoader` + `CLIPLoader` + `VAELoader` (separate) |
| Latent space | `EmptyLatentImage` | `EmptySD3LatentImage` |
| Model conditioning | — | `ModelSamplingAuraFlow` (required, `shift`≈3.1) |
| Typical steps | 20-30 | **4 (Lightning LoRA)** or 50 (no LoRA) |
| Typical CFG | 7.0 | **1.0 (Lightning)** or 4.0 (no LoRA) |
| Sampler | `euler_ancestral` | `euler` |
| Scheduler | `karras` | `simple` |
| Native resolution | 512 or 1024 | **1328 × 1328** (or see aspect ratios) |

---

## 1. Node graph structure

```
UNETLoader ("qwen_image_2512_fp8_e4m3fn.safetensors")
    └──► LoraLoaderModelOnly (optional Lightning LoRA, strength=1.0)
             └──► ModelSamplingAuraFlow (shift=3.1)
                      └──► KSampler ◄── CLIPTextEncode (positive) ← CLIPLoader
                                    ◄── CLIPTextEncode (negative) ← CLIPLoader
                                    ◄── EmptySD3LatentImage
                               └──► VAEDecode ◄── VAELoader
                                        └──► SaveImage
```

All these are **built-in ComfyUI nodes** — no custom extension required.

---

## 2. KSampler settings

### Mode A — Lightning LoRA (fast, 4 steps)

Use when the LoraLoaderModelOnly node is present and enabled.

| param | value |
|---|---|
| steps | 4 |
| cfg | 1.0 |
| sampler_name | `euler` |
| scheduler | `simple` |
| denoise | 1.0 |

```
set_param("223", "steps",          4)
set_param("223", "cfg",            1.0)
set_param("223", "sampler_name",   "euler")
set_param("223", "scheduler",      "simple")
```

### Mode B — Standard (50 steps, no LoRA)

Use when the LoRA is disabled or absent.

| param | value |
|---|---|
| steps | 50 |
| cfg | 4.0 |
| sampler_name | `euler` |
| scheduler | `simple` |
| denoise | 1.0 |

---

## 3. ModelSamplingAuraFlow shift

The `shift` parameter (default 3.1) controls noise schedule. Higher = more
structured composition; lower = finer details. Range: 1.0 – 5.0.
**Do not remove this node — Qwen-Image will produce noise without it.**

```
set_param("222", "shift", 3.1)
```

---

## 4. Resolutions (EmptySD3LatentImage)

Qwen-Image-2512 is trained on specific aspect-ratio buckets:

| aspect | width | height |
|---|---|---|
| 1:1 (default) | 1328 | 1328 |
| 16:9 | 1664 | 928 |
| 9:16 | 928 | 1664 |
| 4:3 | 1472 | 1104 |
| 3:4 | 1104 | 1472 |
| 3:2 | 1584 | 1056 |
| 2:3 | 1056 | 1584 |

```
set_param("232", "width",  1664)
set_param("232", "height", 928)
```

---

## 5. Prompt engineering

Qwen2.5-VL understands natural language extremely well. Use **detailed sentences**,
not keyword lists:

```
Good:
"Urban alleyway at dusk. Tall fashion model mid-stride, full body shot, cinematic.
 Rose-gold metallic trench coat over black turtleneck. Braided dark hair, medium
 complexion. Vibrant yellow handbag with geometric details. White architectural
 sneakers. High-contrast, tactile, extreme clarity, photorealistic."

Poor:  "fashion model, trench coat, alley, 8k, masterpiece"
```

Qwen does NOT need `masterpiece`, `8k uhd`, or other quality tokens.

**Negative prompt** (Chinese preferred):
```
低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，蜡像感，人脸无细节，过度光滑，画面具有AI感。构图混乱。文字模糊，扭曲
```

English fallback:
```
low resolution, low quality, deformed limbs, distorted fingers, oversaturated,
waxy skin, no facial detail, over-smoothed, AI-generated look, chaotic composition,
blurry text
```

---

## 6. Iteration strategy

| Verifier issue | Fix strategy |
|---|---|
| Dull / washed out | Add more detail to prompt; raise cfg slightly (1.5–2.0) |
| Oversaturated | Lower cfg toward 1.0 |
| Soft / blurry | Switch to Mode B (50 steps, cfg=4.0) without Lightning LoRA |
| Wrong composition | Add spatial language ("in the foreground", "far right", "top left") |
| AI-looking skin | Extend negative prompt with skin artefact terms |
| Wrong aspect ratio | Update `EmptySD3LatentImage` width/height to a supported bucket |
| LoRA too strong/weak | Tune `strength_model` on `LoraLoaderModelOnly` (0.5–1.0) |

---

## 7. Example: fast quality call sequence (Lightning mode)

```
inspect_workflow()

# Positive prompt (node 227)
set_param("227", "text", "Red fox in a misty ancient forest at dawn, soft golden
  volumetric light through cedar trees, photorealistic wildlife photography,
  shallow DOF, individual fur strands, morning dew on foliage.")

# Negative prompt (node 228)
set_param("228", "text", "低分辨率，低画质，肢体畸形，画面过饱和，画面具有AI感")

# Resolution — landscape 16:9
set_param("232", "width",  1664)
set_param("232", "height", 928)

# Lightning mode (4-step, cfg=1.0)
set_param("223", "steps", 4)
set_param("223", "cfg",   1.0)
set_param("223", "seed",  42)

finalize_workflow()
```
