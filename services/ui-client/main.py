#!/usr/bin/env python3
"""UI Client: business logic and API Gateway orchestration for chat clients (Telegram, future MAX/VK/www)."""

import io
import logging
import os
from contextlib import asynccontextmanager

import asyncpg
import httpx
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from db import (
    create_pool,
    create_user,
    generate_hash_token,
    get_chunk_count_by_document_id,
    get_user_by_external_id,
    get_users_for_restart_notify,
    insert_hash,
    link_hash,
    set_last_restart_message_id,
    set_pending_state,
    update_language,
    update_last_telegram_message_id,
    update_mode,
    update_role,
)
from asyncpg.exceptions import UniqueViolationError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BASE_API_URL = os.getenv("BASE_API_URL", "").rstrip("/")
POLL_INTERVAL_SEC = 2
POLL_MAX_ATTEMPTS = 90
MESSAGE_POLL_MAX_ATTEMPTS = 45
RESUME_CONVERT_REQUEST_TIMEOUT = float(os.getenv("RESUME_CONVERT_REQUEST_TIMEOUT", "600.0"))
MATCH_REQUEST_TIMEOUT = float(os.getenv("MATCH_REQUEST_TIMEOUT", "360.0"))
MAX_VACANCY_TEXT_CHARS = 4096


def _get_allowed_upload_user_ids() -> frozenset[str]:
    raw = os.getenv("ALLOWED_UPLOAD_USER_IDS", "").strip()
    if not raw:
        return frozenset()
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _is_candidate_eligible(role, mode) -> bool:
    if role == "candidate":
        return True
    if role == "admin" and mode == "candidate":
        return True
    return False


def _is_hr_eligible(role, mode) -> bool:
    if role == "recruiter":
        return True
    if role is None:
        return True
    if role == "admin" and mode == "hr":
        return True
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await create_pool()
    app.state.pool = pool
    yield
    await pool.close()


app = FastAPI(title="ui-client", lifespan=lifespan)


async def get_pool():
    return app.state.pool


# --- Request/Response models ---


class StartRequest(BaseModel):
    external_id: str
    source: str = "telegram"


class StartResponse(BaseModel):
    need_language: bool
    user: dict | None = None
    menu: dict | None = None  # role, mode, dialog_owner_id, language


class LanguageRequest(BaseModel):
    external_id: str
    source: str = "telegram"
    language: str


class LinkRequest(BaseModel):
    external_id: str
    source: str = "telegram"
    token: str


class LinkResponse(BaseModel):
    ok: bool
    message: str
    owner_id: str | None = None
    user: dict | None = None


class HashGenerateResponse(BaseModel):
    token: str


class MatchRequest(BaseModel):
    external_id: str
    source: str = "telegram"
    vacancy_text: str


class MatchResponse(BaseModel):
    parts: list[str]
    relevance_score: float | None = None
    summary: str | None = None
    error: str | None = None
    no_results: bool = False


class CoverLetterRequest(BaseModel):
    external_id: str
    source: str = "telegram"
    vacancy_text: str


class CoverLetterResponse(BaseModel):
    letter: str | None = None
    error: str | None = None


class ChatRequest(BaseModel):
    owner_id: str
    message: str


class ChatResponse(BaseModel):
    message_id: str


class ResumeConvertRequest(BaseModel):
    raw_text: str


class ResumeConvertResponse(BaseModel):
    structured_text: str | None = None
    error: str | None = None
    success: bool


class RestartNotifyStoreRequest(BaseModel):
    external_id: str
    source: str = "telegram"
    message_id: int


class RestartNotifyLastMessageRequest(BaseModel):
    external_id: str
    source: str = "telegram"
    message_id: int


# --- Routes ---


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/users/start", response_model=StartResponse)
async def users_start(req: StartRequest, pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, req.external_id)
    if user is None:
        async with pool.acquire() as conn:
            try:
                await create_user(conn, req.external_id, req.source)
                if req.external_id in _get_allowed_upload_user_ids():
                    await update_role(conn, req.external_id, "candidate")
            except UniqueViolationError:
                pass
        async with pool.acquire() as conn:
            user = await get_user_by_external_id(conn, req.external_id)
        if user is None:
            raise HTTPException(status_code=500, detail="create_user failed")
        return StartResponse(
            need_language=True,
            user=_user_to_dict(user),
            menu=None,
        )
    allowed = _get_allowed_upload_user_ids()
    role = user.get("role")
    if req.external_id in allowed and role != "candidate":
        async with pool.acquire() as conn:
            await update_role(conn, req.external_id, "candidate")
        role = "candidate"
    return StartResponse(
        need_language=False,
        user=_user_to_dict(user),
        menu=_menu_from_user(user),
    )


def _user_to_dict(user) -> dict:
    return {
        "role": user.get("role"),
        "mode": str(user.get("mode") or ""),
        "dialog_owner_id": user.get("dialog_owner_id"),
        "language": str(user.get("language") or "en"),
        "pending_state": user.get("pending_state"),
    }


def _menu_from_user(user) -> dict:
    return {
        "role": user.get("role"),
        "mode": str(user.get("mode") or ""),
        "dialog_owner_id": user.get("dialog_owner_id"),
        "language": str(user.get("language") or "en"),
    }


@app.get("/users/me")
async def users_me(external_id: str, source: str = "telegram", pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, external_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return _user_to_dict(user)


@app.post("/users/language")
async def users_language(req: LanguageRequest, pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        n = await update_language(conn, req.external_id, req.language)
    if n == 0:
        raise HTTPException(status_code=404, detail="user_not_found")
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, req.external_id)
    return {"ok": True, "user": _user_to_dict(user) if user else None, "menu": _menu_from_user(user) if user else None}


@app.post("/users/link", response_model=LinkResponse)
async def users_link(req: LinkRequest, pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, req.external_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    async with pool.acquire() as conn:
        ok, msg, owner_id = await link_hash(conn, req.external_id, req.token)
    if not ok:
        return LinkResponse(ok=False, message=msg, owner_id=None, user=None)
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, req.external_id)
    return LinkResponse(ok=True, message=msg, owner_id=owner_id, user=_user_to_dict(user) if user else None)


@app.post("/hash/generate", response_model=HashGenerateResponse)
async def hash_generate(external_id: str, source: str = "telegram", pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, external_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    role = user.get("role")
    mode = str(user.get("mode") or "")
    if not _is_candidate_eligible(role, mode):
        raise HTTPException(status_code=403, detail="candidates_only")
    token = generate_hash_token()
    async with pool.acquire() as conn:
        await insert_hash(conn, external_id, token)
    return HashGenerateResponse(token=token)


@app.post("/match", response_model=MatchResponse)
async def match(req: MatchRequest, pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, req.external_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    if not _is_hr_eligible(user.get("role"), str(user.get("mode") or "")):
        raise HTTPException(status_code=403, detail="recruiters_only")
    dialog_owner_id = user.get("dialog_owner_id")
    if not dialog_owner_id:
        raise HTTPException(status_code=400, detail="use_link_first")
    if not BASE_API_URL:
        raise HTTPException(status_code=503, detail="service_error")
    lang = str(user.get("language") or "en")
    if lang not in ("ru", "en"):
        lang = "en"
    try:
        async with httpx.AsyncClient(timeout=MATCH_REQUEST_TIMEOUT) as client:
            r = await client.post(
                f"{BASE_API_URL}/match-vacancy",
                json={
                    "owner_id": str(dialog_owner_id),
                    "vacancy_text": req.vacancy_text[:MAX_VACANCY_TEXT_CHARS],
                    "summary": True,
                    "target_lang": lang,
                },
            )
    except Exception as e:
        logger.exception("Match API error: %s", e)
        raise HTTPException(status_code=502, detail="request_error")
    if r.status_code != 200:
        detail = "error_generic"
        try:
            d = r.json()
            d = d.get("detail", r.text)
            d = d[0] if isinstance(d, list) and d else d
            detail = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
        except Exception:
            pass
        raise HTTPException(status_code=r.status_code, detail=detail[:500])
    data = r.json()
    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="invalid_response")
    if not data:
        return MatchResponse(parts=[], no_results=True)
    item = data[0]
    score = item.get("relevance_score", 0)
    summary = item.get("summary") or ""
    error = item.get("error")
    snippets = item.get("matched_snippets") or []
    lines = [f"Relevance: {score:.2f}"]
    if error:
        lines.append("")
        lines.append(f"Summary failed: {error}")
    if summary and summary.strip():
        lines.append("")
        lines.append(summary.strip())
    if not summary and not error:
        for i, s in enumerate(snippets[:5], 1):
            t = str(s)
            lines.append(f"\n[{i}] {t[:300]}{'...' if len(t) > 300 else ''}")
    full = "\n".join(lines) if lines else "OK"
    if len(full) <= MAX_VACANCY_TEXT_CHARS:
        return MatchResponse(parts=[full], relevance_score=score, summary=summary or None, error=error)
    parts = []
    rest = full
    while rest:
        chunk = rest[:MAX_VACANCY_TEXT_CHARS]
        idx = chunk.rfind("\n")
        if idx > MAX_VACANCY_TEXT_CHARS // 2:
            chunk = rest[: idx + 1]
        parts.append(chunk)
        rest = rest[len(chunk) :].lstrip()
    return MatchResponse(parts=parts, relevance_score=score, summary=summary or None, error=error)


@app.post("/cover-letter", response_model=CoverLetterResponse)
async def cover_letter(req: CoverLetterRequest, pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, req.external_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    if not _is_candidate_eligible(user.get("role"), str(user.get("mode") or "")):
        raise HTTPException(status_code=403, detail="candidates_only")
    if not BASE_API_URL:
        raise HTTPException(status_code=503, detail="service_error")
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                f"{BASE_API_URL}/cover-letter",
                json={"owner_id": req.external_id, "vacancy_text": req.vacancy_text.strip()},
            )
    except Exception as e:
        logger.exception("Cover letter API error: %s", e)
        raise HTTPException(status_code=502, detail="request_error")
    if r.status_code != 200:
        try:
            d = r.json().get("detail", r.text)
            d = d[0] if isinstance(d, list) and d else d
            err = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
        except Exception:
            err = "Unknown error"
        raise HTTPException(status_code=r.status_code, detail=err[:500])
    data = r.json()
    letter = data.get("letter")
    err = data.get("error")
    if letter:
        return CoverLetterResponse(letter=letter[:MAX_VACANCY_TEXT_CHARS], error=None)
    if err == "no_documents":
        return CoverLetterResponse(letter=None, error="no_documents")
    return CoverLetterResponse(letter=None, error=err or "service_error")


@app.post("/documents/upload")
async def documents_upload(
    external_id: str = Form(...),
    source: str = Form("telegram"),
    file: UploadFile = File(...),
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, external_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    if not _is_candidate_eligible(user.get("role"), str(user.get("mode") or "")):
        raise HTTPException(status_code=403, detail="candidates_only")
    if not BASE_API_URL:
        raise HTTPException(status_code=503, detail="service_error")
    filename = file.filename or "resume.txt"
    raw = await file.read()
    files = {"file": (filename, io.BytesIO(raw), "text/plain")}
    data = {"owner_id": external_id}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{BASE_API_URL}/documents/upload", files=files, data=data)
    except Exception as e:
        logger.exception("Upload API error: %s", e)
        raise HTTPException(status_code=502, detail="upload_error")
    if r.status_code != 202:
        try:
            d = r.json().get("detail", r.text)
            d = d[0] if isinstance(d, list) and d else d
            msg = d.get("msg", str(d)) if isinstance(d, dict) else str(d)
        except Exception:
            msg = r.text or str(r.status_code)
        raise HTTPException(status_code=r.status_code, detail=msg[:500])
    body = r.json()
    return {"job_id": body.get("job_id", "")}


@app.get("/documents/tasks/{job_id}")
async def documents_task_status(job_id: str, pool: asyncpg.Pool = Depends(get_pool)):
    if not BASE_API_URL:
        raise HTTPException(status_code=503, detail="service_error")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{BASE_API_URL}/documents/tasks/{job_id}/status")
    except Exception as e:
        logger.warning("Poll task status failed: %s", e)
        raise HTTPException(status_code=502, detail="request_error")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:200])
    data = r.json()
    status = data.get("status", "")
    if status == "completed":
        doc_id = data.get("document_id")
        n = 0
        if doc_id:
            async with pool.acquire() as conn:
                n = await get_chunk_count_by_document_id(conn, doc_id)
        return {"status": "completed", "document_id": doc_id, "chunk_count": n}
    if status == "failed":
        return {"status": "failed", "error_message": data.get("error_message", "Error")}
    return {"status": status}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not BASE_API_URL:
        raise HTTPException(status_code=503, detail="service_error")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{BASE_API_URL}/chat",
                params={"owner_id": req.owner_id},
                json={"message": req.message},
            )
    except Exception as e:
        logger.exception("Chat API error: %s", e)
        raise HTTPException(status_code=502, detail="request_error")
    if r.status_code != 202:
        try:
            d = r.json().get("detail", r.text) if r.text else {}
            d = d[0] if isinstance(d, list) and d else d
            msg = d.get("msg", str(d)) if isinstance(d, dict) else (r.text or str(r.status_code))
        except Exception:
            msg = r.text or str(r.status_code)
        raise HTTPException(status_code=r.status_code, detail=msg[:500])
    body = r.json()
    message_id = body.get("message_id", "")
    if not message_id:
        raise HTTPException(status_code=502, detail="response_error")
    return ChatResponse(message_id=message_id)


@app.get("/messages/{message_id}")
async def messages_get(message_id: str):
    if not BASE_API_URL:
        raise HTTPException(status_code=503, detail="service_error")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{BASE_API_URL}/messages/{message_id}")
    except Exception as e:
        logger.warning("Get message failed: %s", e)
        raise HTTPException(status_code=502, detail="request_error")
    if r.status_code == 404:
        return {"status": "not_found"}
    if r.status_code == 503:
        return {"status": "processing"}
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:200])
    data = r.json()
    return {
        "status": data.get("status", ""),
        "assistant_text": data.get("assistant_text"),
        "assistant_text_translated": data.get("assistant_text_translated"),
    }


async def _resume_convert_forward(response: httpx.Response) -> ResumeConvertResponse:
    if response.status_code != 200:
        try:
            d = response.json()
            err = d.get("detail", response.text)
        except Exception:
            err = response.text or str(response.status_code)
        err_str = str(err)[:500] if not isinstance(err, str) else err[:500]
        if response.status_code == 504:
            err_str = "resume_convert_chunk_timeout"
        return ResumeConvertResponse(structured_text=None, error=err_str, success=False)
    data = response.json()
    out = (data.get("structured_text") or "").strip()
    if not out:
        return ResumeConvertResponse(structured_text=None, error="empty_response", success=False)
    return ResumeConvertResponse(structured_text=out, error=None, success=True)


@app.post("/resume/convert", response_model=ResumeConvertResponse)
async def resume_convert(req: ResumeConvertRequest):
    if not BASE_API_URL:
        raise HTTPException(status_code=503, detail="service_error")
    try:
        async with httpx.AsyncClient(timeout=RESUME_CONVERT_REQUEST_TIMEOUT) as client:
            r = await client.post(f"{BASE_API_URL}/resume/convert", json={"raw_text": req.raw_text})
    except httpx.TimeoutException:
        return ResumeConvertResponse(structured_text=None, error="timeout", success=False)
    except Exception as e:
        logger.exception("Resume convert error: %s", e)
        return ResumeConvertResponse(structured_text=None, error=str(e)[:500], success=False)
    return await _resume_convert_forward(r)


@app.post("/resume/convert/file", response_model=ResumeConvertResponse)
async def resume_convert_file(file: UploadFile = File(...)):
    """Accept resume file, forward to api-gateway /resume/convert (multipart)."""
    if not BASE_API_URL:
        raise HTTPException(status_code=503, detail="service_error")
    content = await file.read()
    if not content:
        return ResumeConvertResponse(structured_text=None, error="empty_file", success=False)
    try:
        async with httpx.AsyncClient(timeout=RESUME_CONVERT_REQUEST_TIMEOUT) as client:
            r = await client.post(
                f"{BASE_API_URL}/resume/convert",
                files={"file": (file.filename or "resume.txt", content)},
            )
    except httpx.TimeoutException:
        return ResumeConvertResponse(structured_text=None, error="timeout", success=False)
    except Exception as e:
        logger.exception("Resume convert file error: %s", e)
        return ResumeConvertResponse(structured_text=None, error=str(e)[:500], success=False)
    return await _resume_convert_forward(r)


@app.get("/users/restart-notify")
async def users_restart_notify(source: str = "telegram", pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        users = await get_users_for_restart_notify(conn, source)
    return [
        {"external_id": uid, "last_restart_message_id": mid, "language": lang, "last_telegram_message_id": last_mid}
        for uid, mid, lang, last_mid in users
    ]


@app.post("/users/restart-notify/last-message")
async def users_restart_notify_last_message(req: RestartNotifyLastMessageRequest, pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        await update_last_telegram_message_id(conn, req.external_id, req.message_id)
    return {"ok": True}


@app.post("/users/restart-notify/store")
async def users_restart_notify_store(req: RestartNotifyStoreRequest, pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        await set_last_restart_message_id(conn, req.external_id, req.message_id)
    return {"ok": True}


@app.post("/users/switch-mode")
async def users_switch_mode(external_id: str, source: str = "telegram", pool: asyncpg.Pool = Depends(get_pool)):
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, external_id)
    if user is None or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="access_denied")
    cur = str(user.get("mode") or "candidate")
    new_mode = "hr" if cur == "candidate" else "candidate"
    async with pool.acquire() as conn:
        await update_mode(conn, external_id, new_mode)
    async with pool.acquire() as conn:
        user = await get_user_by_external_id(conn, external_id)
    return {"ok": True, "mode": new_mode, "user": _user_to_dict(user) if user else None}


@app.post("/users/set-pending-state")
async def users_set_pending_state(
    external_id: str,
    source: str = "telegram",
    state: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
):
    async with pool.acquire() as conn:
        await set_pending_state(conn, external_id, state)
    return {"ok": True}
