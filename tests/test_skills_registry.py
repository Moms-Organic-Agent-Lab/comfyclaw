"""Tests for the new SkillsRegistry CRUD + multi-root behaviour (Phase 2)."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import pytest

from comfyclaw.skill_manager import SkillsRegistry

_VALID_SKILL = """\
---
name: my-imported
description: A test skill imported via various paths.
---

Body content.
"""


def _make_skill_dir(parent: Path, name: str, body: str = _VALID_SKILL) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    d = parent / name
    d.mkdir()
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


def _zip_of(skill_dir: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for root, _dirs, files in os.walk(skill_dir):
            for fn in files:
                full = Path(root) / fn
                rel = full.relative_to(skill_dir.parent)
                zf.write(full, rel.as_posix())
    return buf.getvalue()


@pytest.fixture()
def user_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect the user-skills root into the test's tmp_path."""
    root = tmp_path / "userskills"
    monkeypatch.setenv("COMFYCLAW_USER_SKILLS_DIR", str(root))
    return root


# ---------------------------------------------------------------------------
# Multi-root + state.json
# ---------------------------------------------------------------------------


class TestMultiRoot:
    def test_includes_user_root_after_create(self, user_root: Path) -> None:
        # Drop a skill into the user dir before construction.
        user_root.mkdir(parents=True)
        d = user_root / "alpha"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: alpha\ndescription: alpha test.\n---\n\nbody")
        reg = SkillsRegistry()
        assert "alpha" in reg.skill_names
        assert reg.get_source("alpha") == "user"

    def test_state_json_persists_enabled_flag(self, user_root: Path) -> None:
        user_root.mkdir(parents=True)
        d = user_root / "beta"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: beta\ndescription: b\n---\nbody")
        reg = SkillsRegistry()
        assert reg.is_enabled("beta") is True
        reg.set_enabled("beta", False)

        # Re-open: state survives.
        reg2 = SkillsRegistry()
        assert reg2.is_enabled("beta") is False
        assert "beta" not in reg2.enabled_skill_names

    def test_disabled_skills_excluded_from_xml(self, user_root: Path) -> None:
        user_root.mkdir(parents=True)
        d = user_root / "gamma"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: gamma\ndescription: g\n---\nbody")
        reg = SkillsRegistry()
        reg.set_enabled("gamma", False)
        assert "gamma" not in reg.build_available_skills_xml()


# ---------------------------------------------------------------------------
# Import: folder
# ---------------------------------------------------------------------------


class TestImportFromFolder:
    def test_copies_skill_into_user_root(self, tmp_path: Path, user_root: Path) -> None:
        src = _make_skill_dir(tmp_path / "src", "my-imported")
        reg = SkillsRegistry()
        name = reg.import_from_folder(src)
        assert name == "my-imported"
        assert (user_root / "my-imported" / "SKILL.md").exists()
        assert "my-imported" in reg.skill_names
        assert reg.get_source("my-imported") == "local"

    def test_rejects_invalid_source(self, tmp_path: Path, user_root: Path) -> None:
        bad = tmp_path / "no-skill"
        bad.mkdir()
        reg = SkillsRegistry()
        with pytest.raises(ValueError):
            reg.import_from_folder(bad)

    def test_rejects_duplicate(self, tmp_path: Path, user_root: Path) -> None:
        src = _make_skill_dir(tmp_path / "src", "my-imported")
        reg = SkillsRegistry()
        reg.import_from_folder(src)
        with pytest.raises(FileExistsError):
            reg.import_from_folder(src)


# ---------------------------------------------------------------------------
# Import: zip
# ---------------------------------------------------------------------------


class TestImportFromZip:
    def test_extracts_top_level_dir(self, tmp_path: Path, user_root: Path) -> None:
        src = _make_skill_dir(
            tmp_path / "src", "zipped-skill", _VALID_SKILL.replace("my-imported", "zipped-skill")
        )
        zip_bytes = _zip_of(src)
        reg = SkillsRegistry()
        name = reg.import_from_zip_bytes(zip_bytes)
        assert name == "zipped-skill"
        assert "zipped-skill" in reg.skill_names
        assert reg.get_source("zipped-skill") == "zip"

    def test_rejects_path_traversal(self, user_root: Path) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil/SKILL.md", "no")
        reg = SkillsRegistry()
        with pytest.raises(ValueError):
            reg.import_from_zip_bytes(buf.getvalue())

    def test_rejects_multiple_top_level_dirs(self, user_root: Path) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a/SKILL.md", "---\nname: a\ndescription: x\n---\n")
            zf.writestr("b/SKILL.md", "---\nname: b\ndescription: x\n---\n")
        reg = SkillsRegistry()
        with pytest.raises(ValueError):
            reg.import_from_zip_bytes(buf.getvalue())


# ---------------------------------------------------------------------------
# Manifest + delete
# ---------------------------------------------------------------------------


class TestManifestAndDelete:
    def test_manifest_includes_source_and_enabled(self, tmp_path: Path, user_root: Path) -> None:
        src = _make_skill_dir(
            tmp_path / "src", "delta", _VALID_SKILL.replace("my-imported", "delta")
        )
        reg = SkillsRegistry()
        reg.import_from_folder(src)
        rows = {r["name"]: r for r in reg.get_manifest()}
        assert "delta" in rows
        assert rows["delta"]["source"] == "local"
        assert rows["delta"]["enabled"] is True

    def test_delete_removes_user_skill(self, tmp_path: Path, user_root: Path) -> None:
        src = _make_skill_dir(
            tmp_path / "src", "epsilon", _VALID_SKILL.replace("my-imported", "epsilon")
        )
        reg = SkillsRegistry()
        reg.import_from_folder(src)
        assert "epsilon" in reg.skill_names
        assert (user_root / "epsilon").exists()

        reg.delete("epsilon")
        assert "epsilon" not in reg.skill_names
        assert not (user_root / "epsilon").exists()

    def test_cannot_delete_builtin(self, user_root: Path) -> None:
        reg = SkillsRegistry()
        any_builtin = next((n for n in reg.skill_names if reg.is_builtin(n)), None)
        if any_builtin is None:
            pytest.skip("No built-in skills available in this environment")
        with pytest.raises(PermissionError):
            reg.delete(any_builtin)
