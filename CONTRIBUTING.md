# Contributing to ComfyClaw

Thank you for your interest in contributing!  This document covers everything
you need to set up a development environment, run tests, and open a pull
request.

If you are looking at ComfyClaw because of the paper, the most useful entry
points are [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (paper sections →
source files) and [`docs/REPRODUCING.md`](docs/REPRODUCING.md).

---

## Table of contents

1. [Development setup](#development-setup)
2. [Pre-commit hooks](#pre-commit-hooks)
3. [CI checks — what runs where](#ci-checks--what-runs-where)
4. [Running tests](#running-tests)
5. [Code style](#code-style)
6. [Adding / editing skills](#adding--editing-skills)
7. [Adding agent tools](#adding-agent-tools)
8. [Pull request workflow](#pull-request-workflow)
9. [Reporting bugs](#reporting-bugs)

---

## Development setup

ComfyClaw uses [**uv**](https://docs.astral.sh/uv/) for dependency management.

### 1. Install uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# or via Homebrew
brew install uv
```

### 2. Clone and bootstrap

```bash
git clone https://github.com/Moms-Organic-Agent-Lab/comfyclaw.git
cd comfyclaw

# Install the package + all dev dependencies into an isolated .venv
uv sync --group dev

# Activate (optional — uv run works without activating)
source .venv/bin/activate   # or: .venv\Scripts\activate on Windows
```

### 3. Configure your environment

ComfyClaw supports any LLM provider via LiteLLM.  Set the env var for the
provider you plan to use:

```bash
cp .env.example .env
# Edit .env — set the API key for your chosen provider, e.g.:
#   ANTHROPIC_API_KEY=sk-ant-...     # Anthropic (default)
#   OPENAI_API_KEY=sk-...            # OpenAI
#   GEMINI_API_KEY=...               # Google Gemini
#   (no key needed for local Ollama)
```

### 4. Install pre-commit hooks

```bash
# Commit-stage hooks (fast checks on every git commit)
uv run pre-commit install

# Push-stage hooks (full suite on every git push)
uv run pre-commit install --hook-type pre-push
```

### 5. Install the ComfyUI plugin (optional, for live-sync testing)

```bash
uv run comfyclaw install-node
# Then restart ComfyUI
```

---

## Pre-commit hooks

The hooks are split into two stages so fast checks don't slow down every commit.

### Commit stage — runs on every `git commit` (~5 s)

Mirrors the CI **Lint** job.

| Hook | What it does |
|---|---|
| `trailing-whitespace` | Remove trailing spaces (skips `.md`) |
| `end-of-file-fixer` | Ensure files end with a newline |
| `check-yaml / toml / json` | Syntax validation |
| `check-merge-conflict` | Catch leftover `<<<<<<<` markers |
| `check-added-large-files` | Block files ≥ 1 MB (e.g. model weights) |
| `debug-statements` | Catch forgotten `pdb` / `breakpoint()` calls |
| `mixed-line-ending --fix=lf` | Normalize to LF on all platforms |
| `ruff --fix` | Auto-fix lint issues; aborts commit if anything was changed so you can review |
| `ruff-format` | Enforce consistent code style |

### Push stage — runs on every `git push` (~30–60 s)

Mirrors the CI **Test** and **Build** jobs.

| Hook | What it does |
|---|---|
| `pytest -ra -q --tb=short` | Full offline test suite |
| `uv build` | Build wheel — catches packaging regressions |

### Running hooks manually

```bash
# Run commit-stage hooks on all files
uv run pre-commit run --all-files

# Run push-stage hooks on all files
uv run pre-commit run --all-files --hook-stage push

# Run a single hook by id
uv run pre-commit run ruff --all-files
uv run pre-commit run pytest --all-files --hook-stage push
```

### Skipping hooks in an emergency

```bash
git commit --no-verify -m "wip: ..."   # skip commit hooks
git push   --no-verify                 # skip push hooks
```

Use sparingly — CI will still run everything.

---

## CI checks — what runs where

The table below maps each GitHub Actions job to its local equivalent so you
know exactly what to run before pushing a PR.

| CI job | Status | Local command | Pre-commit stage |
|---|---|---|---|
| **Lint** — `ruff check .` | blocking | `uv run ruff check .` | commit (auto) |
| **Lint** — `ruff format --check .` | blocking | `uv run ruff format --check .` | commit (auto) |
| **Test** — `pytest -ra -q` | blocking | `uv run pytest -ra -q` | push (auto) |
| **Build** — `uv build` | blocking | `uv build` | push (auto) |
| **Type-check** — `mypy comfyclaw/` | non-blocking | `uv run mypy comfyclaw/` | — (advisory only) |

> mypy is `continue-on-error: true` in CI and is not included in pre-commit
> hooks because the current version crashes on some litellm internals.
> Run it manually as an advisory check.

---

## Running tests

All tests are fully offline — `litellm.completion` is mocked with
`unittest.mock.patch`.

```bash
# Run all tests
uv run pytest

# Verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_workflow.py -v

# Run a specific test class or function
uv run pytest -k "TestClone"
uv run pytest -k "test_topology_accumulation"

# Stop on first failure
uv run pytest -x

# Short traceback (same as CI)
uv run pytest -ra -q --tb=short
```

### Test structure

| File | Coverage |
|---|---|
| `tests/test_workflow.py` | `WorkflowManager` — add/connect/delete/validate/clone |
| `tests/test_memory.py` | `ClawMemory` — record, best, image cap |
| `tests/test_skill_manager.py` | `SkillManager` — load, instructions, detect |
| `tests/test_verifier.py` | `ClawVerifier` — encode once, JPEG/PNG, region issues |
| `tests/test_agent.py` | `ClawAgent` — tool dispatch, LoRA rewire, regional |
| `tests/test_harness.py` | `ClawHarness` — dry-run, early stop, topology accum. |

---

## Code style

The project uses [Ruff](https://docs.astral.sh/ruff/) for both linting and
formatting.  Configuration lives in `pyproject.toml` under `[tool.ruff]`.

Pre-commit hooks handle this automatically on every commit.  To run manually:

```bash
uv run ruff check .           # lint (check only)
uv run ruff check --fix .     # lint + auto-fix
uv run ruff format .          # format
uv run ruff format --check .  # format check (what CI runs)

# Type-check (advisory — not blocking)
uv run mypy comfyclaw/
```

---

## Adding / editing skills

Skills are Markdown files in `comfyclaw/skills/<skill_id>/SKILL.md`.

**Required sections:**

```markdown
# Skill: <Human-Readable Name>

## Description
<1–3 sentences describing what this skill does and when it applies.>
Trigger on: <comma-separated trigger keywords>.

## Instructions

### Steps
1. …
2. …
```

Rules:
- `## Description` is shown to the agent as a one-liner in the manifest.
- `## Instructions` is injected in full when the skill matches the prompt.
- Keywords in the Description line ("Trigger on: …") are used by
  `SkillManager.detect_relevant_skills()` for keyword matching.
- Add a test for your skill's keywords in `tests/test_skill_manager.py`.

---

## Adding agent tools

Agent tools are defined in `comfyclaw/agent.py`:

1. Add an entry to the `_TOOLS` list using the `_tool()` helper (OpenAI /
   LiteLLM function-calling format — `parameters` key, not `input_schema`).
2. Add a `case "your_tool_name":` branch in `ClawAgent._dispatch()`.
3. Implement the logic as a `_your_tool` private method.
4. Add decision guidance to `_SYSTEM_PROMPT` (when to pick this tool).
5. Add a test in `tests/test_agent.py` that patches `litellm.completion` and
   verifies the workflow mutation via `_dispatch()`.

---

## Pull request workflow

1. **Fork** the repository and create a feature branch:
   ```bash
   git checkout -b feat/my-feature
   ```
2. Make your changes, add tests, update `CHANGELOG.md` under `[Unreleased]`.
3. Push — pre-commit push-stage hooks run `pytest` and `uv build`
   automatically.  Fix any failures before opening the PR.
4. Open a PR against `main`.  GitHub Actions CI will run lint, tests, type
   check, and build.
5. Address reviewer feedback.  Squash merge is preferred.

### Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add add_ip_adapter agent tool
fix: correct msg.workflow key in JS sync extension
docs: expand skill authoring guide
test: add harness evolution log tests
refactor: extract _load_dotenv into config module
chore: update pre-commit hook versions
```

---

## Reporting bugs

Please open a [GitHub Issue](https://github.com/Moms-Organic-Agent-Lab/comfyclaw/issues) with:

- ComfyClaw version (`comfyclaw --version` once implemented, or git commit)
- Python version and OS
- ComfyUI version
- LLM provider and model string used
- Minimal reproduction: workflow JSON, prompt, and error message / traceback
