"""Public web app: landing page, chat page and the chat REST API (FastAPI, same origin)."""

import time
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from logipilot.agents import engine, improver
from logipilot.common import config, jev, llm, store

STATIC = Path(__file__).resolve().parents[2] / "web" / "static"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 5

app = FastAPI(title="LogiPilot", version="1.0.0")


@app.post("/api/chat")
async def chat(
    session_id: str = Form(..., min_length=8, max_length=64),
    message: str = Form("", max_length=4000),
    files: list[UploadFile] = File(default_factory=list),
) -> dict:
    if not message.strip() and not files:
        raise HTTPException(422, "Send a message or an attachment.")
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"At most {MAX_FILES} attachments per message.")
    uploads = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than 8 MB.")
        uploads.append((f.filename or "file", f.content_type or "application/octet-stream", data))
    return await engine.turn(session_id, message, uploads)


class Feedback(BaseModel):
    turn_id: str = Field(min_length=6, max_length=64)
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=1000)


@app.post("/api/feedback")
async def feedback(body: Feedback) -> dict:
    if not await store.get_doc("turns", body.turn_id):
        raise HTTPException(404, "Unknown turn.")
    await store.put_doc("feedback", body.turn_id, {**body.model_dump(), "created_at": store.now()})
    return {"ok": True}


@app.post("/api/improve")
async def improve() -> dict:
    """Run one feedback-loop cycle (also callable from Cloud Scheduler)."""
    return await improver.improve()


async def _probe(fn) -> dict:
    t0 = time.monotonic()
    try:
        detail = await fn()
        return {"ok": True, "detail": f"{detail} ({int((time.monotonic() - t0) * 1000)} ms)"}
    except Exception as exc:  # noqa: BLE001 - health reports failures, it never raises them
        return {"ok": False, "detail": str(exc)[:200]}


async def _http_ok(url: str) -> str:
    async with httpx.AsyncClient(timeout=5) as client:
        (await client.get(url)).raise_for_status()
    return "reachable"


async def _jev_ok() -> str:
    await jev._call("ping", {"ok": jev.noul("This is a ping")})
    return "jev-latest"


async def _llm_ok() -> str:
    await llm.complete_json('Return {"ok": true}')
    return config.LLM_MODEL


async def _firestore_ok() -> str:
    await store.get_doc("hubs", "MAA")
    return "connected"


@app.get("/api/health")
async def health() -> dict:
    mcp_health = config.MCP_URL.rsplit("/mcp", 1)[0] + "/health"
    components = {
        "tools_api": await _probe(lambda: _http_ok(f"{config.TOOLS_API_URL}/health")),
        "mcp": await _probe(lambda: _http_ok(mcp_health)),
        "jev": await _probe(_jev_ok),
        "llm": await _probe(_llm_ok),
        "firestore": await _probe(_firestore_ok),
    }
    ok = all(c["ok"] for c in components.values())
    return {"status": "ok" if ok else "degraded", "components": components}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
