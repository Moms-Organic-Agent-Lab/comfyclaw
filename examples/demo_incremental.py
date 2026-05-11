#!/usr/bin/env python3
"""
Demo script: simulates an agent building a workflow node-by-node.

Run this while ComfyUI browser is open to see nodes appear incrementally.

Usage:
    python demo_incremental.py [--port 8765] [--delay 1.5]
"""

from __future__ import annotations

import argparse
import time

from comfyclaw.sync_server import SyncServer
from comfyclaw.workflow import WorkflowManager


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--delay", type=float, default=1.5, help="Seconds between each node addition"
    )
    args = parser.parse_args()

    sync = SyncServer(port=args.port)
    sync.start()
    if not sync.is_running():
        print("Failed to start SyncServer. Is websockets installed?")
        return

    print(f"\n{'=' * 60}")
    print("ComfyClaw Incremental Sync Demo")
    print(f"WebSocket server: ws://0.0.0.0:{args.port}")
    print("Open ComfyUI in your browser to watch nodes appear.")
    print(f"Delay between nodes: {args.delay}s")
    print(f"{'=' * 60}\n")

    wm = WorkflowManager()

    print("Starting in 3 seconds — open ComfyUI browser now!")
    time.sleep(3)

    # Node 1: CheckpointLoaderSimple
    print("[1/7] Adding CheckpointLoaderSimple...")
    nid1 = wm.add_node(
        "CheckpointLoaderSimple",
        "Load Checkpoint",
        ckpt_name="dreamshaper8_lcm.safetensors",
    )
    sync.broadcast(wm.to_dict())
    time.sleep(args.delay)

    # Node 2: CLIPTextEncode (positive)
    print("[2/7] Adding CLIPTextEncode (Positive)...")
    nid2 = wm.add_node(
        "CLIPTextEncode",
        "Positive Prompt",
        clip=[nid1, 1],
        text="a cyberpunk city at night, neon lights, rain, photorealistic, 8k",
    )
    sync.broadcast(wm.to_dict())
    time.sleep(args.delay)

    # Node 3: CLIPTextEncode (negative)
    print("[3/7] Adding CLIPTextEncode (Negative)...")
    nid3 = wm.add_node(
        "CLIPTextEncode",
        "Negative Prompt",
        clip=[nid1, 1],
        text="blurry, low quality, watermark, ugly, deformed",
    )
    sync.broadcast(wm.to_dict())
    time.sleep(args.delay)

    # Node 4: EmptyLatentImage
    print("[4/7] Adding EmptyLatentImage...")
    nid4 = wm.add_node(
        "EmptyLatentImage",
        "Empty Latent",
        batch_size=1,
        height=512,
        width=512,
    )
    sync.broadcast(wm.to_dict())
    time.sleep(args.delay)

    # Node 5: KSampler
    print("[5/7] Adding KSampler...")
    nid5 = wm.add_node(
        "KSampler",
        "KSampler",
        model=[nid1, 0],
        positive=[nid2, 0],
        negative=[nid3, 0],
        latent_image=[nid4, 0],
        seed=42,
        steps=6,
        cfg=2.0,
        sampler_name="lcm",
        scheduler="sgm_uniform",
        denoise=1.0,
    )
    sync.broadcast(wm.to_dict())
    time.sleep(args.delay)

    # Node 6: VAEDecode
    print("[6/7] Adding VAEDecode...")
    nid6 = wm.add_node(
        "VAEDecode",
        "VAE Decode",
        samples=[nid5, 0],
        vae=[nid1, 2],
    )
    sync.broadcast(wm.to_dict())
    time.sleep(args.delay)

    # Node 7: SaveImage
    print("[7/7] Adding SaveImage...")
    wm.add_node(
        "SaveImage",
        "Save Image",
        filename_prefix="ComfyClaw",
        images=[nid6, 0],
    )
    sync.broadcast(wm.to_dict())

    print(f"\nWorkflow complete! {len(wm)} nodes built incrementally.")
    print("Check your ComfyUI browser — you should have seen nodes appear one by one.")

    # Now demonstrate an update
    time.sleep(2)
    print("\n[bonus] Updating positive prompt text...")
    wm.set_param(nid2, "text", "a majestic red dragon in a fantasy landscape, epic, detailed, 8k")
    sync.broadcast(wm.to_dict())

    print("\nDone! Press Ctrl+C to stop the sync server.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        sync.stop()
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
