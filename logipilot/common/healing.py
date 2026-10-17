"""Self-healing primitives: retry with backoff, a circuit breaker, and an incident log.

Every recovery is recorded on the current turn (shown to the user in "How I handled this") and in
the Firestore `incidents` collection, so healing is visible rather than silent.
"""

import asyncio
import contextvars
import random
import time
from collections.abc import Awaitable, Callable

from logipilot.common import store
from logipilot.common.secrets import redact

# Incidents for the turn being processed; the engine resets this per turn.
turn_incidents: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "turn_incidents", default=None
)


async def record(component: str, error: Exception | str, recovery: str) -> None:
    incident = {"component": component, "error": redact(error)[:300], "recovery": recovery}
    if (bucket := turn_incidents.get()) is not None:
        bucket.append(incident)
    try:
        await store.add_doc("incidents", incident)
    except Exception:  # noqa: BLE001 - the incident log must never break the turn it describes
        pass


async def retry[T](
    fn: Callable[[], Awaitable[T]], component: str, attempts: int = 3, base: float = 0.4
) -> T:
    """Call fn, retrying transient failures with exponential backoff and jitter."""
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            if attempt == attempts:
                raise
            await record(component, exc, f"retry {attempt}/{attempts - 1}")
            await asyncio.sleep(base * 2 ** (attempt - 1) + random.random() * 0.2)
    raise AssertionError("unreachable")


class CircuitBreaker:
    """Opens after `threshold` consecutive failures; half-opens again after `cooldown` seconds."""

    def __init__(self, name: str, threshold: int = 2, cooldown: float = 30.0):
        self.name, self.threshold, self.cooldown = name, threshold, cooldown
        self.failures, self.opened_at = 0, 0.0

    @property
    def is_open(self) -> bool:
        return self.failures >= self.threshold and time.monotonic() - self.opened_at < self.cooldown

    def success(self) -> None:
        self.failures = 0

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()
