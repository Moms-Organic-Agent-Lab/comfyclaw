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
