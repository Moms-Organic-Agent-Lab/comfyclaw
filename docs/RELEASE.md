# Releasing ComfyClaw

This document is for maintainers. End users should look at the main
[README](../README.md) instead.

ComfyClaw lives in a single public repository at
`Moms-Organic-Agent-Lab/comfyclaw`. Versions are cut by tagging on
`main` — there is no separate downstream / mirror to keep in sync.

## Cutting a release

```bash
# 0. Make sure you're on main with a clean working tree.
git checkout main
git pull --ff-only

# 1. Pre-flight: lint, format, tests, wheel build.
uv run ruff check .
uv run ruff format --check .
uv run pytest -ra -q                  # 227 passed, < 1 s
uv build                              # sanity-check wheel + sdist

# 2. Bump the version everywhere it appears:
#    - pyproject.toml         [project] version
#    - comfyclaw/__init__.py  __version__
#    - CITATION.cff           version, date-released
#    Then add a dated section in CHANGELOG.md describing what changed.

# 3. Commit the bump.
git add pyproject.toml comfyclaw/__init__.py CITATION.cff CHANGELOG.md
git commit -m "chore(release): v0.X.Y"

# 4. Tag and push.
git tag -a v0.X.Y -m "v0.X.Y"
git push origin main
git push origin v0.X.Y
```

The tag push triggers `.github/workflows/release.yml`, which:

1. runs the full test suite,
2. builds the wheel + sdist,
3. extracts the matching CHANGELOG section,
4. creates a GitHub Release with the dist artefacts attached.

PyPI publishing is staged behind a commented-out step in
`release.yml`. Uncomment it once PyPI's trusted-publisher is configured
for the GitHub org.

## What does NOT belong in the repo

- Real `.env` files or API keys. `.env.example` only.
- Outputs (`comfyclaw_output/`, `outputs/`, `*.png`, …). Already in
  `.gitignore` but worth a manual sweep before tagging.
- Personal notes, paper drafts, experiment logs, or other research
  artefacts that don't belong to the open-source release.
- Anything under a license incompatible with GPL-3.0, except the one
  Apache-2.0 exception already documented in `LICENSE` (the
  `skill-creator` skill under `comfyclaw/skills/skill-creator/`).

Use `git status` + `git ls-files` to confirm before tagging that
nothing accidental is along for the ride.

## Coordination with the paper

`CITATION.cff` and the README BibTeX block both carry placeholders for
the paper's venue / arXiv id. Whenever the paper changes status:

- arXiv preprint posted → fill in the arXiv id in
  `CITATION.cff:preferred-citation.url` and the README `@article{...}`
  block.
- Accepted at a venue → update `journal`, `volume`, `pages`, `doi`.
- Camera-ready posted → confirm the README hero blurb and the
  CHANGELOG v0.1.0 entry still match the camera-ready abstract.

Bump the patch version (`0.1.1`) when the citation metadata is the
only thing that changed, so users have a stable hash to pin against.

## Rewriting history (emergency only)

If you ever need to wipe the git history and start over from the
current working tree — for example, to drop a leak of secrets that
git-filter-repo can't cleanly remove — use the utility script:

```bash
scripts/release/init_public_repo.sh /tmp/comfyclaw-clean
```

It snapshots the current working tree (respecting `.gitignore` and
including untracked-but-not-ignored files) into a fresh directory,
initialises a new git repo, and creates a single commit. Sanity-check
the snapshot, then force-push it as the new history:

```bash
cd /tmp/comfyclaw-clean
git remote add origin git@github.com:Moms-Organic-Agent-Lab/comfyclaw.git
git push --force-with-lease origin main
```

This is a sledgehammer — only use it for the v0.1.0 cut or for genuine
incidents. Force-pushing is destructive to anyone who has cloned the
repo. Prefer `git filter-repo` or `BFG Repo-Cleaner` for surgical
history rewrites.
