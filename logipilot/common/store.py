"""Firestore access. Every case, trace, feedback item, learned rule, and eval run lives here."""

import contextvars
import os
from datetime import UTC, datetime

from google.cloud import firestore

from logipilot.common import config  # noqa: F401  (loads .env before the client reads env vars)


class _LazyClient:
    """Creates the Firestore client on first use, so a missing credential surfaces as a clear
    runtime error (and an unhealthy /api/health) instead of crashing the process at import."""

    _client: firestore.AsyncClient | None = None

    def __getattr__(self, name: str):
        if self._client is None:
            self._client = firestore.AsyncClient(project=os.getenv("GOOGLE_CLOUD_PROJECT"))
        return getattr(self._client, name)


db = _LazyClient()

# Test traffic (session ids starting with "test-": demo cases, evals, probes) keeps its threads,
# tickets and logs in test_* collections, so the ops desk and real history only hold real chats.
# Seed data (shipments, hubs, lanes, inventory) and learning state stay shared.
TEST_SESSION_PREFIX = "test-"
TEST_SCOPED = {"turns", "cases", "tickets", "pii_vault", "incidents"}
test_run: contextvars.ContextVar[bool] = contextvars.ContextVar("test_run", default=False)


def _col(name: str):
    return db.collection(f"test_{name}" if test_run.get() and name in TEST_SCOPED else name)


def now() -> str:
    return datetime.now(UTC).isoformat()


async def get_doc(collection: str, doc_id: str) -> dict | None:
    snap = await _col(collection).document(doc_id).get()
    return snap.to_dict() if snap.exists else None


async def put_doc(collection: str, doc_id: str, data: dict) -> None:
    await _col(collection).document(doc_id).set(data, merge=True)


async def add_doc(collection: str, data: dict) -> str:
    _, ref = await _col(collection).add({**data, "created_at": now()})
    return ref.id


async def list_docs(collection: str, limit: int = 50, **where: object) -> list[dict]:
    query = _col(collection)
    for field, value in where.items():
        query = query.where(filter=firestore.FieldFilter(field, "==", value))
    return [{"id": s.id, **s.to_dict()} async for s in query.limit(limit).stream()]


async def active_rules(category: str) -> list[str]:
    """Learned rules promoted by the improvement loop for one specialist category."""
    docs = await list_docs("learned_rules", status="active", category=category)
    return [d["rule"] for d in docs]
