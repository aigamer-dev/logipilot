"""Public web app: landing page, chat page and the chat REST API (FastAPI, same origin)."""

import asyncio
import contextlib
import os
import time
import uuid
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from logipilot.agents import engine, improver, tickets
from logipilot.common import config, healing, jev, llm, store

STATIC = Path(__file__).resolve().parents[2] / "web" / "static"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 5
ALLOWED_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".pdf",
    ".csv",
    ".txt",
    ".json",
    ".xlsx",
    ".xlsm",
    ".webm",
    ".ogg",
    ".mp3",
    ".wav",
    ".m4a",
)
SESSION_ID = r"^[A-Za-z0-9_-]{8,64}$"
TURN_TIMEOUT_S = float(os.getenv("TURN_TIMEOUT_S", "120"))

# Single-process mode (small hosts): the tools API and MCP server are mounted in this app and
# still reached over HTTP/MCP, so the agent path is identical to the three-service layout.
SINGLE_PROCESS = os.getenv("SINGLE_PROCESS") == "1"


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    if not SINGLE_PROCESS:
        yield
        return
    from logipilot.mcp_server.main import mcp

    async with mcp.session_manager.run():
        yield


# No public /docs or schema: the README documents the API.
app = FastAPI(
    title="LogiPilot",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
# The GitHub Pages frontend calls this API cross-origin; only listed origins are allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "https://aigamer-dev.github.io").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def friendly_validation_error(_, exc: RequestValidationError):
    """Say which field is wrong without echoing the submitted input back."""
    fields = sorted({str(e["loc"][-1]) for e in exc.errors()})
    return JSONResponse(
        {
            "detail": f"Invalid input in: {', '.join(fields)}. Messages are limited to 4000 "
            "characters; session ids are 8-64 letters, digits, '-' or '_'."
        },
        status_code=422,
    )


INTERNAL_PREFIXES = ("/tools", "/mcp-server")


@app.middleware("http")
async def internal_only(request, call_next):
    """The mounted tools API and MCP server serve this process only, never the internet.

    They return raw shipment records (customer contact details) and can create actions, so any
    request that does not come from loopback gets a 404 as if they did not exist.
    """
    if request.url.path.startswith(INTERNAL_PREFIXES) and (
        request.client is None
        or request.client.host not in ("127.0.0.1", "::1")
        or request.headers.get("x-api-key") != os.getenv("TOOLS_API_KEY")
    ):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


if SINGLE_PROCESS:
    from logipilot.mcp_server.main import mcp
    from logipilot.tools_api.main import app as tools_app

    app.mount("/tools", tools_app)
    app.mount("/mcp-server", mcp.streamable_http_app())


@app.post("/api/chat")
async def chat(
    session_id: str = Form(..., pattern=SESSION_ID),
    message: str = Form("", max_length=4000),
    files: list[UploadFile] = File(default_factory=list),
) -> dict:
    if not message.strip() and not files:
        raise HTTPException(422, "Send a message or an attachment.")
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"At most {MAX_FILES} attachments per message.")
    uploads = []
    for f in files:
        if not (f.filename or "").lower().endswith(ALLOWED_EXTENSIONS):
            raise HTTPException(415, f"{f.filename}: unsupported file type.")
        data = await f.read()
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{f.filename} is larger than 8 MB.")
        uploads.append((f.filename or "file", f.content_type or "application/octet-stream", data))
    try:
        return await asyncio.wait_for(engine.turn(session_id, message, uploads), TURN_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - the chat must always get an answer, never a 500
        timed_out = isinstance(exc, TimeoutError)
        await healing.record("turn", exc, "returned a safe FAILED answer to the user")
        return {
            "session_id": session_id,
            "turn_id": f"failed-{uuid.uuid4().hex[:12]}",
            "answer": (
                "That took longer than it should, so I stopped to avoid a half-finished action. "
                "Please send it again."
                if timed_out
                else "Something went wrong on my side and nothing was changed. Please try again."
            ),
            "status": "FAILED",
            "contracts": [],
            "trace": [],
            "incidents": [{"component": "turn", "recovery": "safe FAILED answer"}],
            "guardrail_flags": [],
            "pii_vaulted": 0,
            "latency_ms": 0,
        }


class Feedback(BaseModel):
    turn_id: str = Field(min_length=6, max_length=64)
    session_id: str = Field(pattern=SESSION_ID)
    rating: Literal[-1, 1]
    comment: str | None = Field(default=None, max_length=1000)


@app.post("/api/feedback")
async def feedback(body: Feedback) -> dict:
    turn = await store.get_doc("turns", body.turn_id)
    # Same 404 for "unknown" and "not yours", so turn ids can't be probed.
    if not turn or turn.get("session_id") != body.session_id:
        raise HTTPException(404, "Unknown turn.")
    await store.put_doc("feedback", body.turn_id, {**body.model_dump(), "created_at": store.now()})
    return {"ok": True}


class TicketReply(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    close: bool = False


def _require_admin(token: str) -> None:
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected or token != expected:
        raise HTTPException(403, "Admin token required.")


@app.get("/api/tickets/{ticket_id}")
async def ticket_status(ticket_id: str, session_id: str) -> dict:
    """A customer checks their own ticket (only from the session that opened it)."""
    ticket = await tickets.get(ticket_id, session_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found for this session.")
    return {
        k: ticket.get(k)
        for k in (
            "ticket_id",
            "status",
            "category",
            "reason",
            "human_reply",
            "created_at",
            "updated_at",
        )
    }


@app.post("/api/tickets/{ticket_id}/reply")
async def ticket_reply(
    ticket_id: str, body: TicketReply, x_admin_token: str = Header(default="")
) -> dict:
    """Operations staff answer a ticket; the customer sees it on their next status check."""
    _require_admin(x_admin_token)
    try:
        ticket = await tickets.reply(ticket_id, body.message, body.close)
    except ValueError:
        raise HTTPException(409, "Ticket is closed; open a new one instead.") from None
    if not ticket:
        raise HTTPException(404, "Unknown ticket.")
    return {"ok": True, "ticket_id": ticket["ticket_id"], "status": ticket["status"]}


@app.post("/api/improve")
async def improve(x_admin_token: str = Header(default="")) -> dict:
    """Run one feedback-loop cycle. Admin only: it changes agent behaviour for everyone."""
    _require_admin(x_admin_token)
    return await improver.improve()


async def _probe(fn) -> dict:
    t0 = time.monotonic()
    try:
        detail = await fn()
        return {"ok": True, "detail": f"{detail} ({int((time.monotonic() - t0) * 1000)} ms)"}
    except Exception as exc:  # noqa: BLE001 - health reports failures, it never raises them
        await healing.record("health", exc, "reported unavailable")
        return {"ok": False, "detail": "unavailable"}  # details stay in Firestore incidents


async def _http_ok(url: str) -> str:
    async with httpx.AsyncClient(timeout=5) as client:
        (await client.get(url)).raise_for_status()
    return "reachable"


async def _jev_ok() -> str:
    await jev._call("ping", {"ok": jev.noul("This is a ping")})
    return "ok"


async def _llm_ok() -> str:
    await llm.complete_json('Return {"ok": true}')
    return "ok"


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
