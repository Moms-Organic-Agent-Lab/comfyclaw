from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from comfyclaw.cli import (
    _MODEL_BUNDLES,
    _bundle_status,
    _model_target,
    _probe_openai_compatible,
    _update_env_file,
)
from comfyclaw.model_bundles import (
    huggingface_model_file_from_url,
    model_url_target,
    safe_model_filename,
)


def test_update_env_file_preserves_comments_and_updates_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\nCOMFYCLAW_MODEL=old\nOTHER=1\n")

    _update_env_file(
        env,
        {
            "COMFYCLAW_MODEL": "openai/Qwen/Qwen3.6-27B",
            "COMFYCLAW_API_BASE": "http://127.0.0.1:18000/v1",
        },
    )

    text = env.read_text()
    assert "# comment" in text
    assert "OTHER=1" in text
    assert "COMFYCLAW_MODEL=openai/Qwen/Qwen3.6-27B" in text
    assert "COMFYCLAW_API_BASE=http://127.0.0.1:18000/v1" in text


def test_model_target_maps_bundle_file_to_comfyui_models_dir(tmp_path: Path) -> None:
    bundle = _MODEL_BUNDLES["wan22-t2v"]
    target = _model_target(tmp_path, bundle.files[0])
    assert target == (
        tmp_path
        / "models"
        / "diffusion_models"
        / "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
    )


def test_bundle_status_detects_present_files(tmp_path: Path) -> None:
    bundle = _MODEL_BUNDLES["qwen-image-2512"]
    present = _model_target(tmp_path, bundle.files[0])
    present.parent.mkdir(parents=True)
    present.write_bytes(b"x")

    statuses = _bundle_status(tmp_path, bundle)

    assert statuses[0][2] is True
    assert any(exists is False for _mf, _target, exists in statuses[1:])


def test_probe_openai_compatible_parses_model_ids() -> None:
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"data":[{"id":"Qwen/Qwen3.6-27B"}]}'

    with patch("urllib.request.urlopen", return_value=_Resp()):
        ok, detail, models = _probe_openai_compatible("http://127.0.0.1:18000/v1")

    assert ok is True
    assert models == ["Qwen/Qwen3.6-27B"]
    assert "Qwen/Qwen3.6-27B" in detail


def test_huggingface_model_file_from_url_parses_resolve_url() -> None:
    mf = huggingface_model_file_from_url(
        "https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/"
        "split_files/vae/qwen_image_vae.safetensors?download=true",
        "vae",
    )

    assert mf is not None
    assert mf.repo == "Comfy-Org/Qwen-Image_ComfyUI"
    assert mf.revision == "main"
    assert mf.path == "split_files/vae/qwen_image_vae.safetensors"
    assert mf.dest_subdir == "vae"
    assert mf.dest_name == "qwen_image_vae.safetensors"


def test_model_url_target_restricts_destination_subdirs(tmp_path: Path) -> None:
    target = model_url_target(tmp_path, "loras", "../adapter.safetensors")
    assert target == tmp_path / "models" / "loras" / "adapter.safetensors"

    try:
        model_url_target(tmp_path, "../custom_nodes", "x.safetensors")
    except ValueError as exc:
        assert "Unsupported model destination" in str(exc)
    else:
        raise AssertionError("expected unsupported destination to fail")


def test_safe_model_filename_removes_path_and_unsafe_chars() -> None:
    assert safe_model_filename("../my model?.safetensors") == "my model.safetensors"
