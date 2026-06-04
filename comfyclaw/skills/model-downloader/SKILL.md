---
name: model-downloader
description: Detect missing ComfyUI model weights and request user-approved downloads into the correct models subdirectory.
---

# Model Downloader

Use this skill when `query_available_models` shows an empty list or the workflow needs a model file that is not installed.

Rules:

1. Never invent a model filename. Query the relevant model type first.
2. If a required file is missing, call `download_model_weights` with a trusted URL, destination subdirectory, and a short reason.
3. The tool asks the user for approval before downloading. If the user declines, choose another installed model or answer with the manual command.
4. After a successful download, tell the user that ComfyUI may need a restart before loader dropdowns refresh.

Common destinations:

- Checkpoints: `checkpoints`
- UNET / diffusion model files: `diffusion_models`
- Text encoders: `text_encoders`
- VAE files: `vae`
- LoRA adapters: `loras`
- ControlNet models: `controlnet`
- CLIP files: `clip`
- CLIP Vision files: `clip_vision`
- Upscalers: `upscale_models`

Known sample URLs:

- Qwen-Image-2512 diffusion model:
  `https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors`
- Qwen-Image text encoder:
  `https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors`
- Qwen-Image VAE:
  `https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors`
- Qwen-Image-2512 Lightning LoRA:
  `https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/resolve/main/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors`
- Wan2.2 T2V high-noise UNET:
  `https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors`
- Wan2.2 T2V low-noise UNET:
  `https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors`
- Wan2.2 / Wan2.1 VAE:
  `https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors`
- UMT5 text encoder for Wan workflows:
  `https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`

Example tool calls:

```json
{
  "url": "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors",
  "dest_subdir": "vae",
  "reason": "The Qwen-Image workflow requires this VAE, but ComfyUI does not list it."
}
```

```json
{
  "url": "https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/resolve/main/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors",
  "dest_subdir": "loras",
  "reason": "The requested fast Qwen-Image workflow needs the Lightning LoRA adapter."
}
```
