# Local LLM and Model Setup

This guide covers two common local setups:

1. Running the ComfyClaw agent through a local OpenAI-compatible LLM server
   such as vLLM.
2. Preparing ComfyUI model weights for image and video generation.

## Local vLLM

Start vLLM with an OpenAI-compatible endpoint. For large text models that share
one GPU with ComfyUI video generation, keep the KV cache bounded:

```bash
vllm serve Qwen/Qwen3.6-27B \
  --host 127.0.0.1 \
  --port 18000 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.65 \
  --max-num-seqs 2
```

Then write ComfyClaw's `.env` settings:

```bash
uv run comfyclaw configure-local-llm \
  --provider vllm \
  --model Qwen/Qwen3.6-27B \
  --api-base http://127.0.0.1:18000/v1 \
  --run-mode manual \
  --check \
  --write-env
```

Use `manual` mode when the local model is text-only. `auto` and `copilot`
require a verifier model that can read images or video frames.

## ComfyUI Panel Setup

Most users can configure the same pieces from the ComfyUI GUI:

1. Start ComfyUI and the ComfyClaw panel server:

   ```bash
   uv run comfyclaw serve-video --mode manual
   ```

2. Open ComfyUI, then open the ComfyClaw sidebar.
3. Click **Settings → Setup**.
4. In **Local LLM**, set the model and API base, then click **Check endpoint**.
   Click **Use in panel** after the endpoint is reachable.
5. In **Generation Models**, choose `Wan2.2 text-to-video` or
   `Qwen-Image-2512 text-to-image`, then click **Check installed**. If files are
   missing, click **Download missing**.
6. For a first Wan2.2 run, click **Set Video + Manual** and generate a short
   prompt before switching to longer or automated runs.

The GUI download action writes into `COMFYUI_DIR/models` using the same bundle
definitions as the CLI. Restart ComfyUI after downloads complete so model
dropdowns refresh.

## Model Bundles

List built-in download recipes:

```bash
uv run comfyclaw models list
```

Check whether a bundle is present in `COMFYUI_DIR/models`:

```bash
uv run comfyclaw models check wan22-t2v
uv run comfyclaw models check qwen-image-2512
```

Download missing files:

```bash
uv run comfyclaw models download wan22-t2v
uv run comfyclaw models download qwen-image-2512 --include-optional
```

Restart ComfyUI after downloading so node dropdowns refresh.

## Video Generation

Wan2.2 T2V requires these files:

```text
ComfyUI/models/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors
ComfyUI/models/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors
ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
ComfyUI/models/vae/wan_2.1_vae.safetensors
```

Run a one-shot video job:

```bash
uv run comfyclaw run-video \
  --prompt "slow dolly forward, a red fox walking through a misty forest at dawn" \
  --mode manual \
  --output-dir comfyclaw_output
```

Start the ComfyUI panel server in video mode:

```bash
uv run comfyclaw serve-video --mode manual
```

The panel connects to `ws://127.0.0.1:8765`.

## Troubleshooting

If ComfyUI fails with `[Errno 5] Input/output error` inside `tqdm`, restart
ComfyUI with stdout/stderr redirected to a real log file:

```bash
cd /path/to/ComfyUI
python3 main.py --port 8188 > comfyui.log 2>&1
```

If Wan2.2 runs out of memory while vLLM is active, reduce vLLM's
`--max-model-len`, `--gpu-memory-utilization`, or `--max-num-seqs`, or stop
vLLM while generating.
