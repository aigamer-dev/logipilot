"""Public web app: landing page, chat page and the chat REST API (FastAPI, same origin)."""

import asyncio
import contextlib
import os
import time
import uuid
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from logipilot.agents import engine, improver, tickets
from logipilot.common import config, healing, jev, llm, store
from logipilot.common.secrets import install_log_filter
from logipilot.web import security

install_log_filter()

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
    # The feedback loop runs by itself in the background (AUTO_IMPROVE=0 turns it off).
    learning = (
        asyncio.create_task(improver.learning_loop())
        if os.getenv("AUTO_IMPROVE", "1") != "0"
        else None
    )
    try:
        if not SINGLE_PROCESS:
            yield
            return
        from logipilot.mcp_server.main import mcp

        async with mcp.session_manager.run():
            yield
    finally:
        if learning:
            learning.cancel()


# No public /docs or schema: the README documents the API.
app = FastAPI(
    title="LogiPilot",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
# The pages are served from this same app, so cross-origin calls are off unless listed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv("CORS_ORIGINS", "").split(",") if o],
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
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in security.SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


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
    request: Request,
    session_id: str = Form(..., pattern=SESSION_ID),
    message: str = Form("", max_length=4000),
    files: list[UploadFile] = File(default_factory=list),
) -> dict:
    if not message.strip() and not files:
        raise HTTPException(422, "Send a message or an attachment.")
    ip = security.client_ip(request)
    wait = security.per_ip.retry_after(ip) or security.per_session.retry_after(session_id)
    if wait:
        await security.record("rate_limited", request, session_id=session_id)
        raise HTTPException(
            429,
            "You're sending messages too quickly; please wait a moment.",
            headers={"Retry-After": str(wait)},
        )
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
        envelope = await asyncio.wait_for(engine.turn(session_id, message, uploads), TURN_TIMEOUT_S)
        if envelope.get("guardrail_flags"):
            await security.record(
                "guardrail_refusal", request, session_id=session_id,
                flags=envelope["guardrail_flags"],
            )  # fmt: skip
        return envelope
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
    # Thumbs-up turns join the eval set, so future rules must keep them working.
    added = await improver.add_regression_case(turn, body.turn_id) if body.rating == 1 else 0
    return {"ok": True, "regression_cases_added": added}


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
    """Run one feedback-loop cycle now. Admin only: it changes agent behaviour for everyone."""
    _require_admin(x_admin_token)
    return await improver.improve(trigger="admin")


@app.get("/api/learning")
async def learning() -> dict:
    """Public view of what the system has learned and the evidence for each rule."""
    rules = await store.list_docs("learned_rules", limit=200, status="active")
    runs = await store.list_docs("eval_runs", limit=200)
    cases = await store.list_docs("eval_cases", limit=1000)
    per_category: dict[str, int] = {}
    for c in cases:
        per_category[c.get("category", "?")] = per_category.get(c.get("category", "?"), 0) + 1
    keep = ("id", "category", "rule", "baseline", "score", "created_at")
    return {
        "active_rules": [{k: r.get(k) for k in keep} for r in rules],
        "recent_runs": [
            {k: r.get(k) for k in (*keep, "decision")}
            for r in sorted(runs, key=lambda r: r.get("created_at", ""), reverse=True)[:20]
        ],
        "eval_cases": per_category,
        "regression_cases_from_feedback": sum(c.get("source") == "user_feedback" for c in cases),
        "state": await store.get_doc("learning_state", "main") or {},
    }


@app.post("/api/rules/{rule_id}/revoke")
async def revoke_rule(rule_id: str, x_admin_token: str = Header(default="")) -> dict:
    """A human can switch off any learned rule; it stays on record as revoked."""
    _require_admin(x_admin_token)
    if not await store.get_doc("learned_rules", rule_id):
        raise HTTPException(404, "Unknown rule.")
    await store.put_doc("learned_rules", rule_id, {"status": "revoked", "revoked_at": store.now()})
    return {"ok": True}


async def _probe(fn) -> dict:
    t0 = time.monotonic()
    try:
        detail = await fn()
        return {"ok": True, "detail": f"{detail} ({int((time.monotonic() - t0) * 1000)} ms)"}
    except Exception as exc:  # noqa: BLE001 - health reports failures, it never raises them
        await healing.record("health", exc, "reported unavailable")
        return {"ok": False, "detail": "unavailable"}  # details stay in Firestore incidents


async def _http_ok(url: str) -> str:
    headers = {"X-API-Key": os.getenv("TOOLS_API_KEY", "")}
    async with httpx.AsyncClient(timeout=5, headers=headers) as client:
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


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness for the hosting platform: instant, no external calls."""
    return {"ok": True}


_health_cache: dict = {"at": 0.0, "body": None}


@app.get("/api/health")
async def health() -> dict:
    """Dependency status for the chat page; probes run in parallel and are cached for 30 s."""
    if _health_cache["body"] and time.monotonic() - _health_cache["at"] < 30:
        return _health_cache["body"]
    mcp_health = config.MCP_URL.rsplit("/mcp", 1)[0] + "/health"
    probes = {
        "tools_api": lambda: _http_ok(f"{config.TOOLS_API_URL}/health"),
        "mcp": lambda: _http_ok(mcp_health),
        "jev": _jev_ok,
        "llm": _llm_ok,
        "firestore": _firestore_ok,
    }
    results = await asyncio.gather(
        *(_probe(lambda f=f: asyncio.wait_for(f(), 4)) for f in probes.values())
    )
    components = dict(zip(probes, results, strict=True))
    ok = all(c["ok"] for c in components.values())
    body = {"status": "ok" if ok else "degraded", "components": components}
    _health_cache.update(at=time.monotonic(), body=body)
    return body


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
