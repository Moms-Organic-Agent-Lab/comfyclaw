"""Shared model bundle helpers for CLI and ComfyUI panel setup flows."""

from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelFile:
    repo: str
    path: str
    dest_subdir: str
    filename: str | None = None
    optional: bool = False

    @property
    def dest_name(self) -> str:
        return self.filename or Path(self.path).name


@dataclass(frozen=True)
class ModelBundle:
    name: str
    description: str
    files: tuple[ModelFile, ...]
    notes: tuple[str, ...] = ()


MODEL_BUNDLES: dict[str, ModelBundle] = {
    "wan22-t2v": ModelBundle(
        name="wan22-t2v",
        description="Wan2.2 14B text-to-video, native ComfyUI dual-UNET FP8 workflow.",
        files=(
            ModelFile(
                "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                "split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
                "diffusion_models",
            ),
            ModelFile(
                "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                "split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
                "diffusion_models",
            ),
            ModelFile(
                "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "text_encoders",
            ),
            ModelFile(
                "Comfy-Org/Wan_2.2_ComfyUI_Repackaged",
                "split_files/vae/wan_2.1_vae.safetensors",
                "vae",
            ),
        ),
        notes=(
            "Requires substantial VRAM. If a local LLM shares the GPU, run vLLM with a smaller --max-model-len / --gpu-memory-utilization.",
            "Restart ComfyUI after downloading so loader dropdowns refresh.",
        ),
    ),
    "qwen-image-2512": ModelBundle(
        name="qwen-image-2512",
        description="Qwen-Image-2512 text-to-image FP8 workflow with optional Lightning LoRA.",
        files=(
            ModelFile(
                "Comfy-Org/Qwen-Image_ComfyUI",
                "split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors",
                "diffusion_models",
            ),
            ModelFile(
                "Comfy-Org/Qwen-Image_ComfyUI",
                "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
                "text_encoders",
            ),
            ModelFile(
                "Comfy-Org/Qwen-Image_ComfyUI",
                "split_files/vae/qwen_image_vae.safetensors",
                "vae",
            ),
            ModelFile(
                "lightx2v/Qwen-Image-2512-Lightning",
                "Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors",
                "loras",
                optional=True,
            ),
        ),
        notes=(
            "The Lightning LoRA is optional but recommended for fast 4-step generation.",
            "Use examples/workflows/qwen_image_2512.json as a known-good starting workflow.",
        ),
    ),
}


def probe_openai_compatible(api_base: str, timeout: int = 5) -> tuple[bool, str, list[str]]:
    url = api_base.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} from {url}", []
    except Exception as exc:
        return False, f"{exc}", []
    models = [str(item.get("id")) for item in data.get("data", []) if item.get("id")]
    detail = ", ".join(models[:5]) if models else "reachable but no models listed"
    return True, detail, models


def model_target(comfyui_dir: Path, mf: ModelFile) -> Path:
    return comfyui_dir / "models" / mf.dest_subdir / mf.dest_name


def bundle_status(comfyui_dir: Path, bundle: ModelBundle) -> list[tuple[ModelFile, Path, bool]]:
    return [
        (mf, model_target(comfyui_dir, mf), model_target(comfyui_dir, mf).exists())
        for mf in bundle.files
    ]


def download_model_file(mf: ModelFile, target: Path) -> None:
    from huggingface_hub import hf_hub_download

    target.parent.mkdir(parents=True, exist_ok=True)
    src = Path(hf_hub_download(repo_id=mf.repo, filename=mf.path))
    tmp = target.with_suffix(target.suffix + ".tmp")
    shutil.copy2(src, tmp)
    tmp.replace(target)
