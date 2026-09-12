from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastembed.text.onnx_embedding import OnnxTextEmbedding


MODEL_NAME = "BAAI/bge-small-zh-v1.5"
CACHE_DIR = Path("/opt/fastembed-seed")


model = next(
    candidate
    for candidate in OnnxTextEmbedding._list_supported_models()
    if candidate.model == MODEL_NAME
)
if not model.sources.url:
    raise RuntimeError(f"{MODEL_NAME} has no pinned fallback URL")

# Air can reach FastEmbed's official Google Storage mirror but not Hugging Face.
# Force the pinned fallback instead of relying on fastembed 0.8's incomplete
# ConnectError fallback handling.
fallback_model = replace(model, sources=replace(model.sources, hf=None))
model_dir = OnnxTextEmbedding.download_model(fallback_model, str(CACHE_DIR))
model_file = model_dir / model.model_file
if not model_file.is_file():
    raise RuntimeError(f"FastEmbed seed is incomplete: {model_file}")
