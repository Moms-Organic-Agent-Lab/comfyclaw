# Reproducing the paper

This guide walks through the exact steps to reproduce the headline
results in *“An Agentic Harness for Skill-Evolving Image Generation
Workflows”* using the open-source release in this repository. The same
recipe works for the multi-provider, multi-backend, and run-mode
ablations.

For a concept-level map from paper sections to source files, see
[`ARCHITECTURE.md`](ARCHITECTURE.md). For interactive use through the
ComfyUI browser panel, see the main [README](../README.md).

---

## 1. Reference environment

The paper experiments were run on the configuration below. Other
configurations should work — the differences are noted where they
matter.

| Component | Version | Notes |
|---|---|---|
| Python | 3.13 (CI also covers 3.10–3.13) | Pin via `.python-version`. |
| `uv` | ≥ 0.4 | The project uses `uv sync` for deterministic installs. |
| ComfyUI | 0.3.x (Desktop or server) | The JS extension uses `app.loadApiJson` and falls back to `app.loadGraphData` / `app.graph.configure` for older releases. |
| OS | macOS 14 (Apple Silicon) / Ubuntu 22.04 | Apple MPS + native-FP8 models have a known caveat (see §6). |
| LLM provider | Anthropic, OpenAI, Google, Groq, Ollama (via LiteLLM) | Any LiteLLM-supported provider works; mix freely between agent and verifier. |

```bash
# Bootstrap a fresh checkout
git clone https://github.com/Moms-Organic-Agent-Lab/comfyclaw.git
cd comfyclaw
uv sync --group dev                      # runtime + dev tools
cp .env.example .env                     # fill in at least one provider key
uv run comfyclaw install-node            # symlink plugin into ComfyUI
# Restart ComfyUI after the symlink lands.
```

Verify the install:

```bash
uv run pytest -ra -q                     # 227 tests, < 1 s
uv run comfyclaw node-path               # path to the bundled plugin
```

## 2. Model assets

The two reference workflows under `examples/workflows/` reference real
ComfyUI checkpoints. Place them in your ComfyUI `models/` tree (paths
shown for the ComfyUI Desktop default `~/Documents/ComfyUI/`).

### Qwen-Image-2512 (recommended, used for headline runs)

| Asset | ComfyUI path | Source |
|---|---|---|
| UNET (FP8) | `models/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors` | Hugging Face: `Qwen/Qwen-Image-2512` |
| Text encoder | `models/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` | bundled with Qwen-Image release |
| VAE | `models/vae/qwen_image_vae.safetensors` | bundled with Qwen-Image release |
| Lightning LoRA (8-step) | `models/loras/Qwen-Image-Lightning-8steps-V2.0.safetensors` | Qwen-Image-Lightning release |

### DreamShaper 8 LCM (used for low-budget / Ollama ablation)

| Asset | ComfyUI path | Source |
|---|---|---|
| Checkpoint | `models/checkpoints/dreamshaper8_lcm.safetensors` | Civitai: DreamShaper 8 LCM |

ComfyClaw never downloads weights for you. If a required asset is
missing, ComfyUI will return an HTTP 400 from `/prompt` and you will
see the agent enter the repair loop with the verbatim error message.

## 3. Reproducing the headline numbers

### 3.1 Wildlife photography (Qwen + Claude Sonnet, auto mode)

```bash
uv run comfyclaw run \
  --workflow examples/workflows/qwen_image_2512.json \
  --model anthropic/claude-sonnet-4-5 \
  --iterations 2 \
  --threshold 0.85 \
  --prompt "A majestic red fox sitting in a misty ancient forest at dawn, photorealistic wildlife photography"
```

Expected behaviour (matches paper Table 1 row 1):

| Metric | Value |
|---|---|
| Verifier score | ≈ 0.89 / 1.00 |
| Outcome | ✅ early stop after iteration 1 |
| Skills loaded by agent | `qwen-image-2512`, `photorealistic` |
| Resolution chosen | 1472 × 1104 (Qwen Lightning bucket) |

Variation: re-run with `--mode copilot` to drop into the co-pilot
flow (VLM scores → you accept / override via the floating panel). The
agent will treat your textual feedback as high-priority input on the
next iteration.

### 3.2 Cyberpunk-city, local Ollama agent + verifier

```bash
uv run comfyclaw run \
  --workflow examples/workflows/qwen_image_2512.json \
  --model ollama/gemma4:e4b \
  --verifier-model ollama/gemma4:e4b \
  --iterations 3 \
  --prompt "a futuristic cyberpunk city skyline at night, neon lights, rain, 8k"
```

Expected behaviour (matches paper Table 1 row N — local-only baseline):

| Iteration | Score | Notes |
|---|---:|---|
| 1 | ≈ 0.36 | Atmosphere present; missing neon, reflections. |
| 2 | ≈ 0.36 | Agent proposes ControlNet but tool-call execution is unreliable on Gemma-4-e4b. |
| 3 | ≈ 0.49 | Rain / reflections improve. |

Takeaway recorded in the paper: small local LMs are usable as
*verifiers* but unreliable at multi-step tool execution. Mixing
backends (e.g. Sonnet agent + LLaVA verifier) recovers most of the
quality at a fraction of the cost.

### 3.3 Build-from-scratch (no workflow file)

```bash
uv run comfyclaw run \
  --model anthropic/claude-sonnet-4-5 \
  --iterations 3 \
  --prompt "a red fox at dawn, photorealistic, DSLR"
```

The agent calls `read_skill("workflow-builder")` first, then
`query_available_models("checkpoints")` and walks the architecture
recipes until it finds one that matches your installed models. Watch
the ComfyUI canvas via the panel — nodes appear node-by-node as the
agent constructs the graph.

## 4. Ablation grid

The paper ablates over four axes. Every combination is reachable from
the CLI without code changes:

| Axis | Flag (env var) | Values |
|---|---|---|
| Agent backend | `--agent-backend` (`COMFYCLAW_AGENT_BACKEND`) | `litellm` / `claude-code` / `codex` / `gemini-cli` |
| Agent model | `--model` (`COMFYCLAW_MODEL`) | any LiteLLM model string |
| Verifier model | `--verifier-model` (`COMFYCLAW_VERIFIER_MODEL`) | any LiteLLM model string (vision-capable) |
| Verifier mode | `--verifier-mode` (`COMFYCLAW_VERIFIER_MODE`) | `vlm` / `human` / `hybrid` |
| Run mode | `--mode` (`COMFYCLAW_RUN_MODE`) | `manual` / `auto` / `copilot` |
| Topology accumulation | env: `COMFYCLAW_EVOLVE_FROM_BEST` | `true` / `false` |
| Max repair attempts | `--max-repair-attempts` | integer (0 disables) |
| Iteration cap | `--iterations` | integer |
| Success threshold | `--threshold` | 0.0–1.0 |

Example: agent-on-`codex` / verifier-on-Anthropic / co-pilot mode:

```bash
export OPENAI_API_KEY=...      # for codex CLI
export ANTHROPIC_API_KEY=...   # for the vision verifier

uv run comfyclaw run \
  --agent-backend codex \
  --model openai/o4-mini \
  --verifier-model anthropic/claude-sonnet-4-5 \
  --mode copilot \
  --iterations 3 \
  --workflow examples/workflows/qwen_image_2512.json \
  --prompt "a cute corgi wearing wizard robes, studio lighting"
```

## 5. Dry-run (agent-only) for reviewers without a GPU

If you do not have ComfyUI or a GPU available, you can still observe
the full agent → workflow loop (every tool call, every validation,
every produced workflow JSON) without ever submitting to ComfyUI:

```bash
uv run comfyclaw dry-run --prompt "a red fox at dawn, photorealistic"
```

Or, from the panel: tick **Build workflow only (no image)** in the
Generate tab. This exercises the agent loop, the skills library, the
verifier-feedback handling for previous iterations, and the
incremental sync, but skips the GPU step entirely.

## 6. Known constraints and caveats

| Constraint | Where it matters | Resolution |
|---|---|---|
| Apple MPS + FP8 (`Float8_e4m3fn`) | running natively-FP8 checkpoints on Apple Silicon | Agent auto-repairs by setting `weight_dtype: "default"` on the UNETLoader. If the file is *natively* FP8, the hardware incompatibility persists; use a non-FP8 checkpoint or run on a CUDA box. |
| Transient ComfyUI `BrokenPipeError` from `tqdm` writing to closed stderr | rare, intermittent | Auto-detected via `_INFRA_ERROR_SIGNALS`; the harness sleeps 5 s and retries the *same* workflow once without invoking agent-repair. |
| `--no-sync` + `comfyclaw serve` | unsupported combo | The serve loop relies on WebSocket triggers. Disable sync only for `comfyclaw run` / `dry-run`. |
| `--max-repair-attempts` | bounds agent retries on a single iteration | Default 2. Increase for flaky local models; set to 0 to make iteration failures terminal. |
| Workflow format | only API format is queued | UI-format files (saved via *Workflow → Save*) are auto-converted on load by `_ui_to_api`. |

## 7. Cost notes

Per the paper, a typical Sonnet-on-cloud + Sonnet-vision run uses
≈ 30–80 K input tokens per iteration (system prompt + skills + tool
results) and ≈ 1–3 K output tokens (tool calls). A 3-iteration run on
Sonnet 4.5 costs roughly USD 0.05–0.20 at posted prices.

Local-only runs (Ollama agent + Ollama vision verifier) cost zero in
API fees but require a machine with enough VRAM to host both the image
diffusion model and the LM at once. We used a single H100 for the
mixed-backend ablations.

## 8. Sanity checks before reporting numbers

Before quoting a number from a re-run:

1. Make sure no model is being silently swapped. Pin the checkpoint with
   `--image-model "<exact-comfyui-filename>"` (or `COMFYCLAW_IMAGE_MODEL`).
   The pin is re-applied after every topology change in
   `WorkflowManager.apply_image_model`.
2. Use `--iterations` and `--threshold` exactly as reported. Defaults
   (`3`, `0.85`) are used unless overridden.
3. Save the full evolution log: `ClawHarness._evolution_log.format()`
   contains a per-iteration node-count delta and the agent's
   self-reported rationale.
4. The verifier's blend weights are configurable
   (`--score-weights req_w,detail_w`); the paper uses the default
   `(0.6, 0.4)`.
