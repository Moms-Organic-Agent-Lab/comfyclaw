---
name: comfyclaw-manual
description: >-
  Introduce ComfyClaw to a user and explain how to use it end-to-end — the
  ComfyUI panel, run modes, CLI, skills, verifier, and the troubleshooting
  paths for a red 🔴 status badge. Load this skill when the user asks
  "what is ComfyClaw?", "how do I use this?", "how does this work?",
  "where do I start?", "怎么用", "说明书", "新手指南", "getting started",
  asks about the 🐾 panel, the Generate / Skills / History tabs, the
  Manual / Auto / Co-pilot toggle, the VLM / Human / Hybrid verifier,
  CLI subcommands (`serve`, `run`, `dry-run`, `install-node`), env vars
  (`COMFYUI_ADDR`, `ANTHROPIC_API_KEY`, `COMFYCLAW_*`), or any first-time
  setup / orientation question. Also triggers on "panel is empty",
  "🔴 disconnected", "backend chip is red", "no API key", and other
  install / setup symptoms.
license: MIT
compatibility: User-orientation skill — no workflow tools required.
metadata:
  author: davidliuk
  version: "0.1.0"
---

# ComfyClaw — User Manual

ComfyClaw is an **agentic harness** that drives an unmodified ComfyUI
server. The user types a prompt, the agent builds or evolves a ComfyUI
workflow node-by-node on the canvas, a vision LLM (or the user) scores
the rendered image, and the loop iterates until the score crosses a
threshold or the iteration budget is spent.

When you load this skill, your job is to **orient the user, not to
generate an image**. Image-gen recipes live in the other skills
(`workflow-builder`, `photorealistic`, …). This skill is the
installation, panel, and "what does this button do" reference.

## Five-step quick start

Tell the user this is the whole onboarding flow.

1. **Install** — clone, then `uv sync` (or `pip install -e ".[sync]"`).
2. **Configure** — `cp .env.example .env`, set **one** LLM key
   (`ANTHROPIC_API_KEY` is the default) and `COMFYUI_ADDR` to wherever
   ComfyUI is listening (defaults to `127.0.0.1:8188`).
3. **Install the ComfyUI plugin** once — `comfyclaw install-node`, then
   **restart ComfyUI**.
4. **Start the server** — `comfyclaw serve` (leave it running).
5. **Generate from ComfyUI** — open ComfyUI in the browser; the
   🐾 ComfyClaw panel is in the top-right, the status badge is in the
   bottom-right.

A user with a CLI subscription (Claude / ChatGPT / Gemini) can **skip
the API key** by signing into the matching CLI once
(`claude /login` · `codex login` · `gemini`) and selecting
`claude-code` / `codex` / `gemini-cli` from the panel's backend picker.

## The 🐾 panel — what each tab does

| Tab | Purpose |
|---|---|
| **Generate** | Prompt box + run mode toggle (Manual / Auto / Co-pilot) + ▶ Generate button + live status + scoreboard. The primary tab. |
| **Skills** | Browse / enable / disable skills. Import from local folder, `.zip`, or `git clone` URL. Built-in skills can be toggled but not deleted. |
| **History** | Image gallery + iteration timeline for the runs the server has seen this session. |

The status badge in the bottom-right reports liveness:

| Badge | Meaning | What to do |
|---|---|---|
| 🟢 live | Server is up, panel is connected | Type a prompt, click Generate |
| 🔄 connecting | Reconnecting after a hiccup | Wait a few seconds |
| 🔴 disconnected | `comfyclaw serve` is not running, or port mismatch | Start the server, or check `--sync-port` (default `8765`) |

The backend chip (next to the badge) reports auth state of the
selected CLI:

- 🟢 installed **and** signed in (CLI subscription is cached)
- 🟡 installed but `needs_auth` — click for the sign-in modal / command
- 🔴 `needs_install` — install the CLI from its vendor

## Run modes — the Manual / Auto / Co-pilot toggle

```
[ Manual ]   [ Auto ]   [ Co-pilot ]
```

| Mode | Iterations | Verifier | Use case |
|---|---|---|---|
| `manual` | always 1 | none | Quick single-shot, no scoring |
| `auto` (default) | up to `--iterations` (3) | VLM | Full self-optimising loop |
| `copilot` | up to `--iterations` | VLM + human approve/override | Highest control, slowest |

In **Auto** and **Co-pilot**, the scoreboard card on each iteration has
an **Accept now** button that ends the loop early with the current
result.

## Verifier modes — VLM / Human / Hybrid

Independent of run mode, the *verifier* picks who scores the image:

| Mode | Flag | Behaviour |
|---|---|---|
| **VLM** (default) | `--verifier-mode vlm` | Vision LLM scores against a checklist + detail score. |
| **Human** | `--verifier-mode human` | You score from the floating panel: 👍 0.9 · 👌 0.6 · 👎 0.3 + free-text feedback. |
| **Hybrid** | `--verifier-mode hybrid` | VLM proposes a score; you accept it or override + add feedback. |

The `--verifier-model` must support image inputs — Claude, GPT-4o,
Gemini, or `ollama/llava` are all fine choices.

## Generate from scratch vs. evolve current

Inside the Generate tab there are two mode buttons:

- **✨ From Scratch** — the agent starts from an empty graph and builds
  the entire workflow. Uses `workflow-builder` skill internally.
- **🔧 Improve Current** — the agent takes whatever is currently on the
  ComfyUI canvas and mutates it. Use this after the user manually
  tweaks a node and wants the agent to layer on a LoRA / ControlNet /
  hires-fix pass.

## CLI cheatsheet (when the user prefers a terminal)

```bash
comfyclaw serve                 # persistent server, drive from the panel
comfyclaw run --prompt "..."    # one-shot agent → generate → verify loop
comfyclaw dry-run --prompt "…"  # agent-only (no ComfyUI execution)
comfyclaw install-node          # symlink the ComfyClaw-Sync ComfyUI plugin
comfyclaw node-path             # print the bundled plugin path
```

Most useful flags (apply to `run` / `dry-run` / `serve`):

| Flag | Default | What it does |
|---|---|---|
| `--comfyui-addr HOST:PORT` | `127.0.0.1:8188` | Where ComfyUI is listening |
| `--model MODEL` | `anthropic/claude-sonnet-4-5` | LiteLLM model id for the agent |
| `--verifier-model MODEL` | same as `--model` | LiteLLM model id for the vision verifier (must support images) |
| `--verifier-mode {vlm,human,hybrid}` | `vlm` | Who scores the image |
| `--mode {manual,auto,copilot}` | `auto` | Iteration loop behaviour |
| `--agent-backend {litellm,claude-code,codex,gemini-cli}` | `litellm` | Drive the tool-use loop via API or a CLI subscription |
| `--iterations N` | `3` | Cap on agent → generate → verify cycles |
| `--threshold S` | `0.85` | Stop early when verifier score ≥ S |
| `--skills-dir DIR` | built-in | Extra skill root on top of `~/.comfyclaw/skills/` |
| `--debug-no-generate` | off | Build workflow only, skip the image render |

Every flag also has a `COMFYCLAW_*` env var equivalent — see the README
table under **Environment variables** if the user wants to set
defaults globally.

## Skills, in one paragraph

A skill is a `SKILL.md` markdown file under `comfyclaw/skills/`,
`~/.comfyclaw/skills/`, or `--skills-dir`. The agent only sees each
skill's **name + description** at startup; it loads the body on demand
via `read_skill("name")`. The user manages them from the **Skills**
tab — toggle on/off, **+ Folder** to import a local skill, **+ .zip**
for a packaged one, **+ Git URL** to clone a public repo. Built-in
skills (`workflow-builder`, `photorealistic`, …) can be disabled but
not deleted; user-imported skills can be deleted with the ✕ button.

If the user is asking *how to write a new skill*, point them at the
`skill-creator` skill in the same folder.

## Troubleshooting decision table

| Symptom | Likely cause | Fix |
|---|---|---|
| 🔴 disconnected (badge) | Server isn't running | Run `comfyclaw serve` in a terminal |
| 🔄 connecting and never resolves | `--sync-port` mismatch | Default is `8765`; match it on both sides |
| Panel not visible at all | Plugin not installed / ComfyUI not restarted | `comfyclaw install-node` then **restart ComfyUI** |
| "Falling back to litellm — which will require an API key" in server log | Selected a CLI backend but not signed in | Click the amber chip, or run `claude /login` / `codex login` / `gemini` once |
| "no API key" error on Generate | Selected `litellm` backend without an env var | Set `ANTHROPIC_API_KEY` (or the matching provider key) in `.env` |
| Stuck at "Waiting for ComfyUI" | Wrong `--comfyui-addr` | Pass the correct `host:port` (often `127.0.0.1:7130` for Desktop) |
| ComfyUI 400 on `/prompt` | Workflow uses the wrong format | ComfyClaw only sends **API format**; export via *Workflow → Export (API)* |
| `Float8_e4m3fn` error on Apple Silicon | MPS doesn't support fp8 | Auto-repair sets `weight_dtype: "default"`; native fp8 files won't work |
| Score never crosses threshold | Threshold too high or model too weak | Lower `--threshold` (0.7 is reasonable) or switch the agent model to Sonnet / GPT-5 |

## When to redirect the user to a different skill

This skill is for orientation. Hand off explicitly when the user's
actual need is generative:

| User intent | Redirect to |
|---|---|
| "Build me a workflow for X" | `workflow-builder` |
| "Make it look photorealistic / DSLR" | `photorealistic` |
| Counting / spatial / text rendering issues | `counting-prompts` / `spatial` / `text-rendering` |
| "How do I write my own skill?" | `skill-creator` |
| Validation / wiring errors | `workflow-guardrails` |

Don't paste the full body of those skills into the answer — name them
and let the agent's normal `read_skill` flow load them when relevant.

## Pointers for deeper reading

- `README.md` — the project README mirrors this skill but in much more
  detail (paper results, full CLI table, architecture, WebSocket
  protocol, agent tool catalogue).
- `docs/ARCHITECTURE.md` — concept-level map from paper sections to
  source files and symbols.
- `docs/REPRODUCING.md` — exact commands and model files for
  reproducing the paper numbers.
- `comfyclaw/skills/README.md` — the catalogue of built-in skills with
  one-line summaries.
