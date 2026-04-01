#!/usr/bin/env python3

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


SERVICE_NAME = os.getenv("SERVICE_NAME", "web-ui")
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title=SERVICE_NAME)


@app.get("/health")
async def health():
    return {"status": "ok", "service": SERVICE_NAME}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))

