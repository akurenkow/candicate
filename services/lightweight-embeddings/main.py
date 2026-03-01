"""
Lightweight Embeddings Service — минимальный embedding API для внешних LLM провайдеров.
Основан на https://github.com/lh0x00/lightweight-embeddings
Модель: bge-m3 (multilingual, 1024 dimensions)

Используется вместо rag-embedding-service при работе с DeepSeek/OpenAI/Gemini.
При LLM_PROVIDER=local используется rag-embedding-service с Ollama.
"""

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_NAME = os.getenv("MODEL_NAME", "BAAI/bge-m3")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "32"))

app = FastAPI(title="Lightweight Embeddings Service")

_model = None


def get_model():
    global _model
    if _model is None:
        logger.info("Loading model: %s", MODEL_NAME)
        _model = SentenceTransformer(MODEL_NAME)
        logger.info("Model loaded, dimension: %s", EMBEDDING_DIMENSION)
    return _model


class EmbedRequest(BaseModel):
    text: str = Field(..., min_length=1)


class EmbedBatchRequest(BaseModel):
    texts: list[str] = Field(..., min_length=1)


class EmbedResponse(BaseModel):
    vector: list[float]


class EmbedBatchResponse(BaseModel):
    vectors: list[list[float]]


@app.on_event("startup")
async def startup():
    get_model()


@app.get("/health")
async def health():
    return {"status": "healthy", "model": MODEL_NAME, "dimension": EMBEDDING_DIMENSION}


@app.post("/embed", response_model=EmbedResponse)
async def embed(body: EmbedRequest) -> dict[str, Any]:
    """Single text embedding (OpenAI-compatible endpoint name)."""
    try:
        model = get_model()
        vector = model.encode(body.text, normalize_embeddings=True).tolist()
        return {"vector": vector}
    except Exception as e:
        logger.exception("Embed failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}") from e


@app.post("/embed_batch", response_model=EmbedBatchResponse)
async def embed_batch(body: EmbedBatchRequest) -> dict[str, Any]:
    """Batch text embeddings."""
    if len(body.texts) > BATCH_SIZE:
        raise HTTPException(status_code=400, detail=f"Too many texts: max {BATCH_SIZE}")
    try:
        model = get_model()
        vectors = model.encode(body.texts, normalize_embeddings=True, batch_size=BATCH_SIZE)
        return {"vectors": [v.tolist() for v in vectors]}
    except Exception as e:
        logger.exception("Embed batch failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}") from e
