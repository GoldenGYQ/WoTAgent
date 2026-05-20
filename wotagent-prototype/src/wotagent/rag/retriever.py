"""RAG retriever — TD knowledge retrieval via vector search.

Supports three embedding backends:

1. ``hf`` (default) — ``BAAI/bge-small-zh-v1.5`` via HuggingFace.
   Download the model first:
   ``python scripts/download_embed_model.py``

   For multilingual support (Chinese + English) try ``BAAI/bge-m3``:
   ``python scripts/download_embed_model.py --model BAAI/bge-m3``

2. ``openai`` — ``text-embedding-3-small`` via OpenAI-compatible API.
   Requires ``OPENAI_API_KEY`` and optionally ``EMBED_MODEL``.

3. ``chroma`` — Chroma's built-in ``all-MiniLM-L6-v2`` (auto-downloaded).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import OpenAIEmbeddings

from ..wot.td import load_tds

_VECTORSTORE: Chroma | None = None


def chroma_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "chroma"


def _get_embeddings():
    provider = os.getenv("EMBED_PROVIDER", "hf").lower()
    if provider == "openai":
        return OpenAIEmbeddings(
            model=os.getenv("EMBED_MODEL", "text-embedding-3-small"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )
    if provider == "chroma":
        # Chroma's built-in embedding — auto-downloads all-MiniLM-L6-v2
        # through sentence-transformers on first use.
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        ef = DefaultEmbeddingFunction()
        return _ChromaEmbeddingAdapter(ef)

    # HuggingFace (default)
    model_name = os.getenv("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
    cache_dir = os.getenv("EMBED_CACHE_DIR")

    # Auto-detect local model in ./models/<model-name>
    local_path = Path(__file__).resolve().parents[3] / "models" / model_name.split("/")[-1]
    if local_path.is_dir() and (local_path / "model.safetensors").exists():
        model_name = str(local_path.resolve())

    kwargs: dict[str, Any] = {"model_name": model_name}
    if cache_dir:
        kwargs["cache_folder"] = cache_dir
    return HuggingFaceEmbeddings(**kwargs)


class _ChromaEmbeddingAdapter:
    """Adapter that wraps a chromadb embedding function as a langchain
    ``Embeddings`` interface so we can pass it to ``Chroma.from_texts``."""

    def __init__(self, ef):
        self._ef = ef

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._ef(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._ef([text])[0]


def build_vector_store(retries: int = 3) -> Chroma | None:
    global _VECTORSTORE
    if _VECTORSTORE is not None:
        return _VECTORSTORE

    tds = load_tds()
    texts = []
    metadatas: list[dict[str, Any]] = []
    for td in tds:
        text = (
            f"title={td.title} location={td.location} "
            f"capabilities={td.capabilities} "
            f"actions={[a.name for a in td.actions]}"
        )
        texts.append(text)
        metadatas.append({
            "id": td.id,
            "title": td.title,
            "location": td.location,
            "capabilities": td.capabilities,
        })

    # ChromaDB on Windows may fail on first attempt due to native
    # module loading races — retry a few times before giving up.
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            embeddings = _get_embeddings()
            chroma_dir = chroma_path()
            chroma_dir.mkdir(parents=True, exist_ok=True)
            _VECTORSTORE = Chroma.from_texts(
                texts=texts,
                embedding=embeddings,
                metadatas=metadatas,
                persist_directory=str(chroma_dir),
            )
            return _VECTORSTORE
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                import time
                time.sleep(0.5 * attempt)

    import warnings
    warnings.warn(
        f"Vector store build failed after {retries} attempts: {last_exc}\n"
        "  Run:  python scripts/download_embed_model.py\n"
        "  Or set EMBED_PROVIDER=chroma to use the built-in fallback."
    )
    _VECTORSTORE = None
    return _VECTORSTORE


def retrieve_td_snippets(query: str, k: int = 3) -> list[str]:
    """Retrieve top-k TD snippets relevant to *query*."""
    store = build_vector_store()
    if store is None:
        return []
    docs = store.similarity_search(query, k=k)
    return [d.page_content for d in docs]
