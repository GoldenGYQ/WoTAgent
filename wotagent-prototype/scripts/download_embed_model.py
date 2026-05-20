#!/usr/bin/env python
"""Download an embedding model for the RAG retriever to the project workspace.

Usage:
    python scripts/download_embed_model.py
    python scripts/download_embed_model.py --model BAAI/bge-m3

Downloads model files from hf-mirror.com (China-friendly) into
``./models/<model-name>/`` so ``HuggingFaceEmbeddings`` can load from disk.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import requests


def _get_sibling_files(model_id: str) -> list[str]:
    """Fetch file list from the model's API endpoint."""
    api_url = f"https://hf-mirror.com/api/models/{model_id}"
    resp = requests.get(api_url, headers={"User-Agent": "wotagent/0.2"}, timeout=15)
    resp.raise_for_status()
    return [s["rfilename"] for s in resp.json().get("siblings", [])]


def _pick_weight_file(siblings: list[str]) -> str | None:
    """Pick the best weight file (safetensors > bin > pt)."""
    safetensors = [s for s in siblings if "model.safetensors" in s]
    if safetensors:
        return safetensors[0]
    bins = [s for s in siblings if s.endswith("pytorch_model.bin")]
    if bins:
        return bins[0]
    return None


def _is_essential(path: str) -> bool:
    """Check if a file is needed for inference (skip training/tf/onnx/etc)."""
    skip_patterns = (
        ".gguf", "flax_model", "tf_model", "rust_model",
        "training", "onnx", "imgs/", "long.jpg",
    )
    if any(p in path for p in skip_patterns):
        return False
    # Skip README, gitattributes, etc.
    if path in (".gitattributes", "README.md"):
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Download embedding model for WoTAgent RAG")
    parser.add_argument(
        "--model",
        default="BAAI/bge-m3",
        help="HF model ID (default: BAAI/bge-m3)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: ./models/<model-name>)",
    )
    args = parser.parse_args()

    model_name = args.model.split("/")[-1]
    output_dir = Path(args.output_dir or f"models/{model_name}").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check if already downloaded
    weight_file = _pick_weight_file([str(p.name) for p in output_dir.iterdir()]) if output_dir.exists() else None
    if weight_file:
        print(f"Model already exists at {output_dir}")
        return

    print(f"Fetching file list for {args.model} ...")
    siblings = _get_sibling_files(args.model)

    weight_file = _pick_weight_file(siblings)
    if weight_file:
        print(f"  Weight file: {weight_file}")

    # Collect essential files + weight file
    needed = set()
    for sib in siblings:
        if sib == weight_file or _is_essential(sib):
            needed.add(sib)

    needed = sorted(needed)
    base_url = f"https://hf-mirror.com/{args.model}/resolve/main"

    total = len(needed)
    for i, filename in enumerate(needed, 1):
        dest = output_dir / filename
        if dest.exists():
            print(f"  [{i}/{total}] {filename} (cached)")
            continue

        file_url = f"{base_url}/{filename}"
        print(f"  [{i}/{total}] Downloading {filename} ...")
        r = requests.get(file_url, timeout=60)
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        mb = len(r.content) / (1024 * 1024)
        print(f"    → {mb:.1f} MB")

    print()
    print(f"Done! Model saved to: {output_dir}")
    print()
    print(f"Set EMBED_MODEL if needed:")
    print(f'  EMBED_MODEL="{output_dir}"')
    print()


if __name__ == "__main__":
    main()
