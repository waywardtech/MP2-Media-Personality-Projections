"""Download candidate GGUF models for the MP2 local generative path.

Selection constraints (in priority order):
  1. Permissive license - MP2 is proprietary, so Apache-2.0 only. This rules out
     Qwen2.5-3B (Qwen Research, non-commercial), Gemma (Gemma terms) and Llama
     (custom license with an MAU clause).
  2. Runs on CPU on a 2013 Haswell i7-4770 (AVX2, no AVX-512/VNNI) in <5 GB.
  3. Reliable structured output. llama.cpp constrains decoding with a JSON schema
     grammar, so schema VALIDITY is guaranteed regardless of size; model size buys
     claim quality, not well-formedness.
"""

import os
import shutil
import sys

from huggingface_hub import hf_hub_download

MODELS = {
    # Throughput candidate.
    "qwen2.5-1.5b-instruct-q4_k_m.gguf": (
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
    # Quality reference.
    "qwen2.5-7b-instruct-q4_k_m.gguf": (
        "bartowski/Qwen2.5-7B-Instruct-GGUF", "Qwen2.5-7B-Instruct-Q4_K_M.gguf"),
}

target_dir = sys.argv[1] if len(sys.argv) > 1 else "/models"
only = sys.argv[2] if len(sys.argv) > 2 else None
os.makedirs(target_dir, exist_ok=True)

for local_name, (repo, filename) in MODELS.items():
    if only and only not in local_name:
        continue
    dest = os.path.join(target_dir, local_name)
    if os.path.exists(dest):
        print(f"exists: {dest} ({os.path.getsize(dest):,} bytes)", flush=True)
        continue
    print(f"downloading {repo}/{filename} -> {dest}", flush=True)
    # Download straight into the target directory: /tmp and the models volume are
    # different devices, so a rename across them fails.
    path = hf_hub_download(repo_id=repo, filename=filename, local_dir=target_dir)
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.move(path, dest)
    print(f"done: {dest} ({os.path.getsize(dest):,} bytes)", flush=True)
