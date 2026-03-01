"""DTOs for search API (CTR-10)."""

from pydantic import BaseModel, Field


class DocChunk(BaseModel):
    chunk_id: str
    text: str
    score: float = 0.0
    metadata: dict = Field(default_factory=dict)
