# ComfyClaw User Guide

ComfyClaw is a `uv`-managed Python project. Unless you activate `.venv`
yourself, run commands as `uv run comfyclaw ...`.

## Runtime Pieces

| Piece | Purpose |
|---|---|
| `uv run comfyclaw serve` | Runs the agent, talks to ComfyUI HTTP, verifies outputs |
| `ComfyClaw-Sync` ComfyUI plugin | Adds the panel and streams workflow updates to the canvas |

Defaults: ComfyUI HTTP is `127.0.0.1:8188`; ComfyClaw WebSocket is `8765`.

## Local ComfyUI App

```bash
git clone https://github.com/Moms-Organic-Agent-Lab/comfyclaw.git
cd comfyclaw
uv sync --extra sync

cp .env.example .env
$EDITOR .env

uv run comfyclaw install-node
```

Restart ComfyUI, then keep the server running:

```bash
uv run comfyclaw serve
```

If ComfyUI is not in the default location:

```bash
uv run comfyclaw install-node --comfyui-dir /path/to/ComfyUI
```

## Deployed or Remote ComfyUI

Panel mode requires installing the plugin on the deployed ComfyUI server:

```bash
uv run comfyclaw node-path
# Copy that directory to <ComfyUI>/custom_nodes/ComfyClaw-Sync on the server.
```

Restart the remote ComfyUI server, then run ComfyClaw where it can reach the
ComfyUI HTTP endpoint:

```bash
uv run comfyclaw serve --comfyui-addr comfyui.example.com:8188
```

The browser must also reach the ComfyClaw WebSocket port. Use a reverse proxy
or SSH tunnel if needed:

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

If you cannot install the plugin, use CLI mode:

```bash
uv run comfyclaw run \
  --comfyui-addr comfyui.example.com:8188 \
  --prompt "a red fox at dawn, photorealistic"
```

## Panel Basics

Open ComfyUI after `serve` starts. The panel has three main tabs:

| Tab | Use |
|---|---|
| Generate | Prompt, run mode, model/backend settings, Generate/Stop |
| Skills | Browse, enable, disable, view, and import skills |
| History | Review previous prompts, images, iteration scores |

Important controls:

| Control | Meaning |
|---|---|
| Scratch | Build a workflow from an empty graph |
| Improve | Edit the current ComfyUI canvas workflow |
| Manual | One pass, no verifier loop |
| Auto | VLM verifier plus automatic refinement |
| Co-pilot | VLM verifier plus human accept/override |
| Build workflow only | Dry-run: update graph but skip image generation |

## CLI Usage

```bash
uv run comfyclaw run \
  --prompt "a red fox at dawn, photorealistic" \
  --iterations 3

uv run comfyclaw run \
  --workflow examples/workflows/qwen_image_2512.json \
  --prompt "make this a rainy neon street"

uv run comfyclaw dry-run --prompt "build a portrait workflow"
```

Video mode:

```bash
uv sync --extra all
uv run comfyclaw run-video --prompt "rain falling on a neon street"
uv run comfyclaw serve-video
```

Outputs are saved under `./comfyclaw_output/` unless `--output-dir` is set.

## Backends and Models

Default backend is `litellm`. Set the key matching your model in `.env`:

| Provider | Model example | Env var |
|---|---|---|
| Anthropic | `anthropic/claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-5.4` | `OPENAI_API_KEY` |
| Gemini | `gemini/gemini-2.0-flash` | `GEMINI_API_KEY` |
| Groq | `groq/llama-3.3-70b-versatile` | `GROQ_API_KEY` |
| Ollama | `ollama/llama3.1` | none |

CLI subscription backends are also supported:

```bash
uv run comfyclaw serve --agent-backend codex
uv run comfyclaw run --agent-backend claude-code --model sonnet --prompt "..."
```

Supported backend ids: `litellm`, `claude-code`, `codex`, `gemini-cli`.
Sign in to the matching CLI first (`claude /login`, `codex login`, or
`gemini`).

Claude Code uses your local Claude subscription login, not the Anthropic API
key path. ComfyClaw strips stale `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`
values before launching `claude`, so a bad API key in `.env` will not override
`claude auth login`.

Local setup helpers:

```bash
uv run comfyclaw configure-local-llm --provider vllm --model Qwen/Qwen3.6-27B --api-base http://127.0.0.1:18000/v1 --check --write-env
uv run comfyclaw models list
uv run comfyclaw models download wan22-t2v
uv run comfyclaw models download qwen-image-2512 --include-optional
```

See [`LOCAL_LLM_AND_MODELS.md`](LOCAL_LLM_AND_MODELS.md) for local vLLM,
Wan2.2 video, and Qwen-Image setup.

## Skills

Built-in skills live in `comfyclaw/skills/`. Imported skills are stored in
`~/.comfyclaw/skills/` unless `COMFYCLAW_USER_SKILLS_DIR` is set.

Use the panel's Skills tab for normal management. For CLI-only runs:

```bash
uv run comfyclaw run --skills-dir ./my_skills --prompt "a studio portrait"
```

## Troubleshooting

Start here:

```bash
uv run comfyclaw doctor
```

| Symptom | Fix |
|---|---|
| Panel missing | Run `uv run comfyclaw install-node`, verify `COMFYUI_DIR`, restart ComfyUI |
| Panel disconnected | Start `uv run comfyclaw serve`; verify WebSocket port `8765` |
| Cannot reach ComfyUI | Set `COMFYUI_ADDR` or pass `--comfyui-addr host:port` |
| Port busy | Stop the old server or pass `--sync-port <port>` |
| Missing model/checkpoint | Install it in ComfyUI or pass `--image-model <name>` |
| LiteLLM auth error | Set the provider API key or use a signed-in CLI backend |
| Claude Code says `Invalid API key` | Restart `comfyclaw serve`; the CLI backend should use `claude auth login`, not `ANTHROPIC_API_KEY` |

Useful commands:

```bash
uv run comfyclaw --help
uv run comfyclaw run --help
uv run comfyclaw node-path
uv run comfyclaw doctor
```
