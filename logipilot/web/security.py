"""HTTP-layer security: rate limits, security headers and an audit trail of security events."""

import time
from collections import defaultdict, deque

from fastapi import Request

from logipilot.common import store

# Content-Security-Policy for the bundled pages: own scripts plus the two pinned CDN libraries.
CSP = (
    "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
}


class RateLimiter:
    """Sliding-window limiter: at most `limit` hits per `window` seconds for each key.

    shortcut: in-process state, which is right for the single-instance deployment; move to a
    shared store (Redis, Firestore counters) before running more than one instance.
    """

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: defaultdict[str, deque[float]] = defaultdict(deque)

    def retry_after(self, key: str) -> int:
        """0 when the request may proceed (and is counted), else seconds until it may."""
        now = time.monotonic()
        hits = self.hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return int(self.window - (now - hits[0])) + 1
        hits.append(now)
        return 0


per_session = RateLimiter(limit=8, window=60)
per_ip = RateLimiter(limit=30, window=60)


def client_ip(request: Request) -> str:
    """The caller's address; behind the platform proxy it is the first X-Forwarded-For hop."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


async def record(kind: str, request: Request, **detail: object) -> None:
    """Append a security event to Firestore for audit; never breaks the request it describes."""
    try:
        await store.add_doc(
            "security_events",
            {"kind": kind, "ip": client_ip(request), "path": request.url.path, **detail},
        )
    except Exception:  # noqa: BLE001 - audit logging is best effort
        pass
