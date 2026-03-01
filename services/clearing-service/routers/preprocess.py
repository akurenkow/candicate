"""Preprocess API: normalize + validate in one call. Idempotent."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from normalize import normalize
from validator import validate, ValidationResult

router = APIRouter(prefix="/preprocess", tags=["preprocess"])


class PreprocessRequest(BaseModel):
    text: str = Field("", description="Raw user text")


class PreprocessResponse(BaseModel):
    normalized: str
    validation_result: str = Field(..., description="OK or BLOCK")
    reasons: list[str] = Field(default_factory=list, description="Block reasons when BLOCK")


@router.post("", response_model=PreprocessResponse)
async def preprocess(req: PreprocessRequest) -> PreprocessResponse:
    raw = req.text or ""
    normalized = normalize(raw)
    outcome = validate(normalized)
    return PreprocessResponse(
        normalized=normalized,
        validation_result=outcome.result.value,
        reasons=list(outcome.reasons),
    )
