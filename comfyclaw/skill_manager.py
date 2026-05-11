"""
SkillsRegistry — multi-root, CRUD-capable skill loader.

Spec
----
The Anthropic Agent-Skills spec (https://agentskills.dev/specification)
governs the on-disk format: each skill is a directory whose name matches
the skill's ``name`` frontmatter field, with a ``SKILL.md`` file
containing YAML frontmatter (required: ``name`` + ``description``) and
a Markdown body.

Beyond the spec, ComfyClaw adds:

  • Multiple roots: built-in (read-only) + user (CRUD) + extras from env.
  • A ``state.json`` file inside the user root tracking the
    ``{enabled, source, imported_at}`` flag for every skill — both
    bundled and imported.
  • Import paths for **local folder**, **uploaded zip**, and **git URL**.
  • Live deletion of user-imported skills (built-ins can be disabled
    but never deleted).

Backwards compatibility
-----------------------
The old ``SkillManager`` API (``build_available_skills_xml``,
``get_body``, ``detect_relevant_skills``, ``skill_names``) is preserved
on this class so existing callers in :mod:`comfyclaw.agent` keep
working.  ``SkillManager`` is re-exported as an alias.
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, NamedTuple

import yaml

# ---------------------------------------------------------------------------
# Built-in skills directory
# ---------------------------------------------------------------------------

_BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _user_skills_root() -> Path:
    """Return the user's writable skills directory (``~/.comfyclaw/skills``)."""
    override = os.environ.get("COMFYCLAW_USER_SKILLS_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".comfyclaw" / "skills").resolve()


def _state_path(user_root: Path) -> Path:
    return user_root.parent / "skills_state.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class SkillProperties(NamedTuple):
    """Parsed SKILL.md frontmatter."""

    name: str
    description: str
    location: Path
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    metadata: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_skill_md(skill_dir: Path) -> tuple[SkillProperties, str]:
    """Parse a ``SKILL.md`` file and return ``(SkillProperties, body_text)``."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        skill_md = skill_dir / "skill.md"
    if not skill_md.exists():
        raise ValueError(f"SKILL.md not found in {skill_dir}")

    content = skill_md.read_text(encoding="utf-8")

    if not content.startswith("---"):
        raise ValueError(f"{skill_md}: SKILL.md must start with YAML frontmatter (---).")

    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{skill_md}: frontmatter not closed with ---")

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"{skill_md}: invalid YAML frontmatter: {exc}") from exc

    if not isinstance(fm, dict):
        raise ValueError(f"{skill_md}: frontmatter must be a YAML mapping")

    for field in ("name", "description"):
        if field not in fm or not str(fm[field]).strip():
            raise ValueError(f"{skill_md}: missing required field: {field!r}")

    declared_name = str(fm["name"]).strip()
    if declared_name != skill_dir.name:
        raise ValueError(
            f"{skill_md}: frontmatter 'name' ({declared_name!r}) does not match "
            f"directory name ({skill_dir.name!r})"
        )

    meta = fm.get("metadata")
    if isinstance(meta, dict):
        meta = {str(k): str(v) for k, v in meta.items()}
    else:
        meta = None

    props = SkillProperties(
        name=declared_name,
        description=str(fm["description"]).strip(),
        location=skill_md.resolve(),
        license=fm.get("license"),
        compatibility=fm.get("compatibility"),
        allowed_tools=fm.get("allowed-tools"),
        metadata=meta,
    )
    return props, parts[2].strip()


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def _is_valid_skill_name(name: str) -> bool:
    return bool(_NAME_RE.fullmatch(name)) and len(name) <= 80


def _safe_unzip_into(zip_bytes: bytes, dest_root: Path) -> Path:
    """Extract a SKILL.md zip into ``dest_root/<skill-name>``.

    The zip must contain exactly one top-level directory whose
    ``SKILL.md`` declares the same name.  Returns the extracted
    directory.
    """
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise ValueError("zip archive is empty")

        # Determine the top-level dir prefix (must be unique).
        tops = {n.split("/", 1)[0] for n in names}
        if len(tops) != 1:
            raise ValueError(
                f"zip must contain exactly one top-level directory (found: {sorted(tops)})"
            )
        top = tops.pop()
        if not _is_valid_skill_name(top):
            raise ValueError(f"top-level directory {top!r} is not a valid skill name")

        # Reject path traversal & absolute paths.
        for n in names:
            if n.startswith("/") or ".." in Path(n).parts:
                raise ValueError(f"unsafe path in zip: {n}")

        target = dest_root / top
        if target.exists():
            raise FileExistsError(f"skill {top!r} already exists at {target} (delete it first)")

        dest_root.mkdir(parents=True, exist_ok=True)
        zf.extractall(dest_root)

        # Re-normalize: if zip had nested wrapper, ensure SKILL.md exists.
        if not (target / "SKILL.md").exists() and not (target / "skill.md").exists():
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError(f"zip's top-level directory {top!r} has no SKILL.md")
        return target


def _git_clone_into(url: str, ref: str | None, dest: Path) -> None:
    """Shallow-clone *url* into *dest* (must not exist yet)."""
    if dest.exists():
        raise FileExistsError(f"{dest} already exists")
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {proc.stderr.strip() or proc.stdout.strip()}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SkillsRegistry:
    """Manages skills across one or more roots with enable/disable + import."""

    def __init__(
        self,
        skills_dir: str | Path | None = None,
        *,
        include_user_root: bool = True,
        extra_roots: list[str | Path] | None = None,
        quiet: bool = False,
    ) -> None:
        # Roots are searched in order; later roots override earlier names.
        roots: list[tuple[Path, str]] = []  # (path, source_label)
        roots.append((_BUILTIN_SKILLS_DIR.resolve(), "builtin"))
        if include_user_root:
            roots.append((_user_skills_root(), "user"))
        if skills_dir:
            roots.append((Path(skills_dir).expanduser().resolve(), "extra"))
        if extra_roots:
            for r in extra_roots:
                roots.append((Path(r).expanduser().resolve(), "extra"))

        self._roots: list[tuple[Path, str]] = roots
        self._user_root: Path = _user_skills_root()
        self._cache: dict[
            str, tuple[SkillProperties, str, str]
        ] = {}  # name -> (props, body, source)
        self._state: dict[str, dict[str, Any]] = self._load_state()
        self._load_all()
        # Make the resolved search paths discoverable. Without this users
        # who see "skills empty" in the UI have no way to know which dirs
        # were actually scanned. flush=True so the line shows up immediately
        # even when stdout is a daemonized pipe (no TTY).
        if not quiet:
            try:
                paths = ", ".join(f"{label}={path}" for path, label in self._roots)
                print(
                    f"[SkillsRegistry] loaded {len(self._cache)} skills from: {paths}",
                    flush=True,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[str, dict[str, Any]]:
        sp = _state_path(self._user_root)
        if not sp.exists():
            return {}
        try:
            return json.loads(sp.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_state(self) -> None:
        sp = _state_path(self._user_root)
        sp.parent.mkdir(parents=True, exist_ok=True)
        try:
            tmp = sp.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
            tmp.replace(sp)
        except OSError as exc:
            print(f"[SkillsRegistry] could not persist state: {exc}")

    def _load_all(self) -> None:
        self._cache.clear()
        for root, source in self._roots:
            if not root.is_dir():
                continue
            for d in sorted(root.iterdir()):
                if not d.is_dir():
                    continue
                try:
                    props, body = _parse_skill_md(d)
                except ValueError as exc:
                    import warnings

                    warnings.warn(
                        f"[SkillsRegistry] Skipping skill {d.name}: {exc}",
                        stacklevel=2,
                    )
                    continue
                # Later roots override earlier.
                self._cache[props.name] = (props, body, source)
                # Initialize state row if missing
                if props.name not in self._state:
                    self._state[props.name] = {
                        "enabled": True,
                        "source": source,
                        "imported_at": None,
                    }
        self._save_state()

    def reload(self) -> None:
        """Re-scan all roots from disk."""
        self._state = self._load_state()
        self._load_all()

    # ------------------------------------------------------------------
    # Public read API (back-compat with SkillManager)
    # ------------------------------------------------------------------

    @property
    def skill_names(self) -> list[str]:
        """All loaded skill names (enabled + disabled)."""
        return sorted(self._cache)

    @property
    def enabled_skill_names(self) -> list[str]:
        return sorted(n for n in self._cache if self._state.get(n, {}).get("enabled", True))

    def get_properties(self, name: str) -> SkillProperties:
        return self._cache[name][0]

    def get_body(self, name: str) -> str:
        return self._cache[name][1]

    def get_source(self, name: str) -> str:
        # Prefer the import-time source (``local``/``zip``/``git``) recorded
        # in state.json — that's what the user sees in the panel.  Fall back
        # to the root label (``builtin``/``user``) when no row exists yet.
        row = self._state.get(name) or {}
        return row.get("source") or self._cache[name][2]

    def is_enabled(self, name: str) -> bool:
        return bool(self._state.get(name, {}).get("enabled", True))

    def is_builtin(self, name: str) -> bool:
        return self._cache.get(name, (None, None, ""))[2] == "builtin"

    def build_available_skills_xml(self) -> str:
        """XML block for the agent system prompt — only enabled skills."""
        names = self.enabled_skill_names
        if not names:
            return "<available_skills>\n</available_skills>"
        lines = ["<available_skills>"]
        for name in names:
            props = self._cache[name][0]
            lines += [
                "<skill>",
                f"<name>{html.escape(props.name)}</name>",
                f"<description>{html.escape(props.description)}</description>",
                f"<location>{html.escape(str(props.location))}</location>",
                "</skill>",
            ]
        lines.append("</available_skills>")
        return "\n".join(lines)

    def detect_relevant_skills(self, prompt: str) -> list[str]:
        prompt_lower = prompt.lower()
        prompt_words = set(re.findall(r"[a-z]{4,}", prompt_lower))
        matched: list[str] = []
        for name in self.enabled_skill_names:
            props = self._cache[name][0]
            keywords = set(re.findall(r"[a-z]{4,}", props.description.lower()))
            if keywords & prompt_words:
                matched.append(name)
        return sorted(matched)

    def get_manifest(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in self.skill_names:
            props, _body, source = self._cache[name]
            row = self._state.get(name, {})
            out.append(
                {
                    "name": name,
                    "description": props.description,
                    "location": str(props.location),
                    "source": row.get("source", source),
                    "enabled": bool(row.get("enabled", True)),
                    "builtin": source == "builtin",
                    "imported_at": row.get("imported_at"),
                    "license": props.license,
                    "allowed_tools": props.allowed_tools,
                }
            )
        return out

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._cache:
            raise KeyError(name)
        row = self._state.setdefault(name, {})
        row["enabled"] = bool(enabled)
        self._save_state()

    def import_from_folder(self, src: str | Path) -> str:
        """Copy a folder containing SKILL.md into the user root."""
        src_path = Path(src).expanduser().resolve()
        if not src_path.is_dir():
            raise FileNotFoundError(f"{src_path} is not a directory")
        # Validate the source contains a parseable SKILL.md.
        props, _body = _parse_skill_md(src_path)
        target = self._user_root / props.name
        if target.exists():
            raise FileExistsError(
                f"skill {props.name!r} already exists at {target}; delete it first"
            )
        self._user_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_path, target)
        self._state[props.name] = {
            "enabled": True,
            "source": "local",
            "imported_at": time.time(),
            "origin": str(src_path),
        }
        self._save_state()
        self.reload()
        return props.name

    def import_from_zip_bytes(self, zip_bytes: bytes, *, origin: str = "") -> str:
        """Extract a zip-packaged skill into the user root."""
        target = _safe_unzip_into(zip_bytes, self._user_root)
        props, _body = _parse_skill_md(target)
        self._state[props.name] = {
            "enabled": True,
            "source": "zip",
            "imported_at": time.time(),
            "origin": origin or "<uploaded.zip>",
        }
        self._save_state()
        self.reload()
        return props.name

    def import_from_zip_b64(self, b64: str, *, origin: str = "") -> str:
        return self.import_from_zip_bytes(base64.b64decode(b64), origin=origin)

    def import_from_git(self, url: str, ref: str | None = None) -> str:
        """Shallow-clone a git repo whose root has SKILL.md into the user dir."""
        url = url.strip()
        if not url:
            raise ValueError("empty git URL")

        # Derive a default skill-name from the repo URL — will be renamed if
        # the cloned SKILL.md declares a different name.
        guess = url.rstrip("/").split("/")[-1].removesuffix(".git")
        if not _is_valid_skill_name(guess):
            guess = re.sub(r"[^a-z0-9-]+", "-", guess.lower()).strip("-") or "imported-skill"

        self._user_root.mkdir(parents=True, exist_ok=True)
        tmp_target = self._user_root / f".import-{int(time.time())}-{guess}"
        try:
            _git_clone_into(url, ref, tmp_target)
            # Strip .git folder
            shutil.rmtree(tmp_target / ".git", ignore_errors=True)

            # If the repo's top-level directly contains SKILL.md, rename to
            # the declared name.  Otherwise, search for the first child
            # directory containing SKILL.md and use that.
            roots: list[Path] = []
            if (tmp_target / "SKILL.md").exists() or (tmp_target / "skill.md").exists():
                roots = [tmp_target]
            else:
                roots = [
                    p for p in tmp_target.iterdir() if p.is_dir() and (p / "SKILL.md").exists()
                ]

            if not roots:
                raise ValueError("repo does not contain a SKILL.md at root or top-level subdir")
            chosen = roots[0]
            props, _body = _parse_skill_md(chosen)
            final = self._user_root / props.name
            if final.exists():
                raise FileExistsError(f"skill {props.name!r} already exists")
            shutil.move(str(chosen), str(final))
        finally:
            if tmp_target.exists():
                shutil.rmtree(tmp_target, ignore_errors=True)

        self._state[props.name] = {
            "enabled": True,
            "source": "git",
            "imported_at": time.time(),
            "origin": url,
            "ref": ref,
        }
        self._save_state()
        self.reload()
        return props.name

    def delete(self, name: str) -> None:
        """Remove a user-imported skill from disk.

        Raises ``PermissionError`` for built-in skills (they can be
        disabled but not deleted).
        """
        if name not in self._cache:
            raise KeyError(name)
        if self._cache[name][2] == "builtin":
            raise PermissionError(f"cannot delete built-in skill {name!r}")

        target = self._user_root / name
        if target.exists():
            shutil.rmtree(target)
        self._state.pop(name, None)
        self._save_state()
        self.reload()

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def roots(self) -> list[tuple[str, str]]:
        return [(str(p), s) for p, s in self._roots]


# ---------------------------------------------------------------------------
# Backwards-compatible single-root view — every existing caller still uses
# this name (and existing tests assume single-root behaviour).
# ---------------------------------------------------------------------------


class SkillManager(SkillsRegistry):
    """Single-root variant: loads ONLY the directory passed in.

    Provided for back-compat with the original :class:`SkillManager`
    used by tests and downstream callers that don't want the built-in /
    user roots layered in.  New code should use :class:`SkillsRegistry`.
    """

    def __init__(self, skills_dir: str | Path | None = None) -> None:
        # ``include_user_root=False`` and skipping the built-in root keeps
        # the legacy behaviour: one dir in, one set of skills out.
        super().__init__(
            skills_dir=skills_dir,
            include_user_root=False,
            extra_roots=None,
        )

    def _load_all(self) -> None:  # type: ignore[override]
        # Drop the built-in root injected by SkillsRegistry.__init__ so this
        # class genuinely scans only the user-supplied directory.
        self._roots = [(p, s) for p, s in self._roots if s != "builtin"]
        super()._load_all()
