#!/usr/bin/env python3
"""
Web BFF: anonymous browser session + proxy to API Gateway.

Security model (MVP):
- Browser gets HttpOnly signed cookie with owner_id (UUID).
- All actions (upload/chat/match/letter) are executed in that owner_id context.
"""

import io
import os
import time
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel, Field


SERVICE_NAME = os.getenv("SERVICE_NAME", "web-bff")
BASE_API_URL = os.getenv("BASE_API_URL", "http://api-gateway:8000").rstrip("/")

SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "cand_session")
SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-insecure-change-me")
SESSION_MAX_AGE_SEC = int(os.getenv("SESSION_MAX_AGE_SEC", "172800"))  # 48h

WEB_BFF_UPLOAD_TOKEN = os.getenv("WEB_BFF_UPLOAD_TOKEN", "").strip()

_cors_origins_raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8010").strip()
_cors_origin_regex = os.getenv("CORS_ORIGIN_REGEX", "").strip()
if _cors_origins_raw and _cors_origins_raw != "*":
    _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
    _cors_allow_credentials = True
else:
    _cors_origins = ["*"]
    _cors_allow_credentials = False


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(SESSION_SECRET, salt="candidate-web-session-v1")


def _now() -> int:
    return int(time.time())


def _load_session_owner_id(request: Request) -> str | None:
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return None
    try:
        payload = _serializer().loads(raw)
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    owner_id = str(payload.get("owner_id") or "").strip()
    created_at = int(payload.get("created_at") or 0)
    if not owner_id or not created_at:
        return None
    if _now() - created_at > SESSION_MAX_AGE_SEC:
        return None
    return owner_id


def _set_session_cookie(response: Response, owner_id: str) -> None:
    token = _serializer().dumps({"owner_id": owner_id, "created_at": _now()})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes"),
        max_age=SESSION_MAX_AGE_SEC,
        path="/",
    )


def _require_owner_id(request: Request) -> str:
    owner_id = _load_session_owner_id(request)
    if owner_id:
        return owner_id
    raise HTTPException(status_code=401, detail="session_required")


app = FastAPI(title=SERVICE_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=_cors_origin_regex or None,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Process-Time"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


class SessionStartResponse(BaseModel):
    owner_id: str


@app.post("/session/start", response_model=SessionStartResponse)
async def session_start(request: Request, response: Response):
    existing = _load_session_owner_id(request)
    if existing:
        return SessionStartResponse(owner_id=existing)
    owner_id = str(uuid4())
    _set_session_cookie(response, owner_id)
    return SessionStartResponse(owner_id=owner_id)


@app.get("/session/me", response_model=SessionStartResponse)
async def session_me(request: Request):
    owner_id = _require_owner_id(request)
    return SessionStartResponse(owner_id=owner_id)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


@app.post("/chat")
async def chat(request: Request, body: ChatRequest):
    owner_id = _require_owner_id(request)
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{BASE_API_URL}/chat",
            params={"owner_id": owner_id},
            json={"message": body.message},
        )
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


@app.get("/messages/{message_id}")
async def messages_get(message_id: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{BASE_API_URL}/messages/{message_id}")
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


class MatchRequest(BaseModel):
    vacancy_text: str = Field(..., min_length=1, max_length=4096)
    summary: bool = True
    target_lang: str = "ru"


@app.post("/match-vacancy")
async def match_vacancy(request: Request, body: MatchRequest):
    owner_id = _require_owner_id(request)
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(
            f"{BASE_API_URL}/match-vacancy",
            json={
                "owner_id": owner_id,
                "vacancy_text": body.vacancy_text,
                "summary": bool(body.summary),
                "target_lang": body.target_lang or "ru",
            },
        )
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


class CoverLetterRequest(BaseModel):
    vacancy_text: str = Field(..., min_length=1, max_length=4096)


@app.post("/cover-letter")
async def cover_letter(request: Request, body: CoverLetterRequest):
    owner_id = _require_owner_id(request)
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{BASE_API_URL}/cover-letter",
            json={"owner_id": owner_id, "vacancy_text": body.vacancy_text},
        )
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


class ResumeConvertRawRequest(BaseModel):
    raw_text: str = Field(..., min_length=1, max_length=200000)


@app.post("/resume/convert")
async def resume_convert_raw(body: ResumeConvertRawRequest):
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(f"{BASE_API_URL}/resume/convert", json={"raw_text": body.raw_text})
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


@app.post("/resume/convert/file")
async def resume_convert_file(file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_file")
    async with httpx.AsyncClient(timeout=600.0) as client:
        r = await client.post(
            f"{BASE_API_URL}/resume/convert",
            files={"file": (file.filename or "resume.txt", io.BytesIO(raw), file.content_type or "application/octet-stream")},
        )
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


@app.post("/documents/upload")
async def documents_upload(request: Request, file: UploadFile = File(...)):
    owner_id = _require_owner_id(request)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty_file")

    headers = {}
    if WEB_BFF_UPLOAD_TOKEN:
        headers["X-Upload-Token"] = WEB_BFF_UPLOAD_TOKEN

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(
            f"{BASE_API_URL}/documents/upload",
            data={"owner_id": owner_id},
            files={"file": (file.filename or "resume.txt", io.BytesIO(raw), file.content_type or "application/octet-stream")},
            headers=headers,
        )
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


@app.get("/documents/tasks/{job_id}/status")
async def documents_task_status(job_id: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(f"{BASE_API_URL}/documents/tasks/{job_id}/status")
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))

