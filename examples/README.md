# ComfyClaw examples

This directory contains runnable demos and reference ComfyUI workflows that
ship with the paper.

## Layout

```
examples/
├── README.md                       you are here
├── demo_incremental.py             standalone live-sync demo
└── workflows/
    ├── sd15_dreamshaper_lcm.json   minimal SD 1.5 / LCM API workflow
    ├── qwen_image_2512.json        Qwen-Image-2512 API workflow
    └── qwen_image_2512.ui.json     Qwen-Image-2512 UI workflow (with positions)
```

## Workflows

All `*.json` files under `workflows/` (except `*.ui.json`) are **API-format**
workflows ready to feed to `ClawHarness.from_workflow_file(...)` or
`comfyclaw run --workflow ...`. The `*.ui.json` variants are the UI-graph
form (with node positions and links) — useful for opening directly in
ComfyUI's web editor.

| File | Architecture | Models referenced |
|---|---|---|
| `sd15_dreamshaper_lcm.json` | SD 1.5 + LCM | `dreamshaper8_lcm.safetensors` |
| `qwen_image_2512.json` | Qwen-Image-2512 (FP8) | `qwen_image_2512_fp8_e4m3fn.safetensors`, `qwen_2.5_vl_7b_fp8_scaled.safetensors`, `qwen_image_vae.safetensors`, Lightning LoRA (8 steps) |
| `qwen_image_2512.ui.json` | Qwen-Image-2512 (UI export) | same as above |

The harness can also be used **without** a workflow file — pass `{}` to
`from_workflow_dict({})` and the agent builds from scratch using the
`workflow-builder` skill.

### Quick run

```bash
# From-scratch (no workflow file)
uv run comfyclaw run --prompt "a red fox at dawn, photorealistic"

# Start from one of the examples here
uv run comfyclaw run \
  --workflow examples/workflows/qwen_image_2512.json \
  --prompt "A majestic red fox sitting in a misty ancient forest at dawn"
```

## Demos

### `demo_incremental.py`

Standalone script that pushes a 7-node SD 1.5 + LCM workflow into ComfyUI
node-by-node so you can watch the live-sync canvas update in real time.
Useful for verifying that the bundled `ComfyClaw-Sync` custom node is
installed and connected:

```bash
# 1. Make sure ComfyUI is running (browser tab open).
# 2. Make sure the ComfyClaw-Sync extension is installed:
uv run comfyclaw install-node

# 3. Run the demo (default: WebSocket port 8765, 1.5 s between nodes):
uv run python examples/demo_incremental.py

# Slow it down to watch each step clearly:
uv run python examples/demo_incremental.py --delay 2.5
```

The demo does not call any LLM — it's a pure WebSocket demo of the
ComfyClaw → ComfyUI sync protocol described in `docs/ARCHITECTURE.md`.
