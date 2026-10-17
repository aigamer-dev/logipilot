"""Fast-start front door: binds the public port at once, runs the real app in a child process.

Importing Google ADK and litellm takes ~20 s on a small (0.25 vCPU) container, longer than some
platforms' health-check grace period. This tiny ASGI app answers /healthz immediately, starts
`logipilot.web.server` as a child uvicorn on a private loopback port, and forwards every request to
it once it is up. Each process imports on its own main thread (threaded imports of litellm
deadlock). Until the child is ready, browsers get a self-refreshing "waking up" page and API
callers get a 503 with Retry-After.
"""

import asyncio
import os
import sys

import httpx

INTERNAL_PORT = os.getenv("APP_INTERNAL_PORT", "8001")
_state: dict = {"child": None, "client": None, "ready": False}

WARMING_HTML = (
    b"<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=5>"
    b"<title>LogiPilot</title><body style='font-family:sans-serif;background:#0b0f19;color:#eee;"
    b"display:grid;place-items:center;height:100vh'><p>LogiPilot is waking up "
    b"(about 30 seconds). This page refreshes on its own.</p>"
)
HOP_BY_HOP = {b"connection", b"keep-alive", b"transfer-encoding", b"upgrade", b"host"}


async def _supervise() -> None:
    """Start the app process and restart it whenever it exits (self-healing front door)."""
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{INTERNAL_PORT}", timeout=300)
    _state["client"] = client
    restarts = 0
    while True:
        _state["ready"] = False
        child = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "uvicorn", "logipilot.web.server:app",
            "--host", "127.0.0.1", "--port", INTERNAL_PORT, "--no-server-header",
            "--log-level", "warning",
        )  # fmt: skip
        _state["child"] = child
        while child.returncode is None and not _state["ready"]:
            try:
                if (await client.get("/healthz", timeout=2)).status_code == 200:
                    _state["ready"] = True
                    print(f"[logipilot] app ready (restarts: {restarts})", flush=True)
            except httpx.TransportError:
                await asyncio.sleep(0.5)
        code = await child.wait()
        _state["ready"] = False
        if _state.get("stopping"):
            return
        restarts += 1
        hint = " (killed: likely out of memory)" if code in (-9, 137) else ""
        print(f"[logipilot] app process exited with {code}{hint}; restarting", flush=True)
        await asyncio.sleep(min(2 * restarts, 10))


async def _respond(send, status: int, body: bytes, content_type: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type.encode()), (b"retry-after", b"10")],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _proxy(scope, receive, send) -> None:
    body = bytearray()
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    path = scope["path"] + (("?" + scope["query_string"].decode()) if scope["query_string"] else "")
    headers = [(k, v) for k, v in scope["headers"] if k.lower() not in HOP_BY_HOP]
    resp = await _state["client"].request(
        scope["method"], path, headers=headers, content=bytes(body)
    )
    dropped = HOP_BY_HOP | {b"content-length", b"content-encoding"}
    out = [
        (k.encode(), v.encode())
        for k, v in resp.headers.multi_items()
        if k.lower().encode() not in dropped
    ]
    await send({"type": "http.response.start", "status": resp.status_code, "headers": out})
    await send({"type": "http.response.body", "body": resp.content})


async def app(scope, receive, send) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                asyncio.create_task(_supervise())
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                _state["stopping"] = True
                child = _state["child"]
                if child and child.returncode is None:
                    child.terminate()
                    await asyncio.wait_for(child.wait(), 15)
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    if scope["path"] == "/healthz":
        return await _respond(send, 200, b'{"ok": true}', "application/json")
    if _state["ready"]:
        return await _proxy(scope, receive, send)
    return await _warming(scope, send)


async def _warming(scope, send) -> None:
    if scope["path"].startswith("/api/"):
        body = b'{"detail": "LogiPilot is starting up; retry in a few seconds."}'
        return await _respond(send, 503, body, "application/json")
    return await _respond(send, 503, WARMING_HTML, "text/html; charset=utf-8")
