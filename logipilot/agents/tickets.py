"""Human hand-off tickets: every escalation gets a trackable ticket id.

    open -> answered (a human replied via the admin endpoint) -> closed

A ticket is visible only to the chat session that created it, because the case and the human's
reply can contain the customer's details.
"""

import re
import secrets

from logipilot.common import store

TICKET_ID = re.compile(r"\bTKT-[A-Z0-9]{8}\b", re.I)
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I, so ids survive being read aloud


async def create(session_id: str, contract: dict, reason: str) -> str:
    ticket_id = "TKT-" + "".join(secrets.choice(_ALPHABET) for _ in range(8))
    await store.put_doc(
        "tickets",
        ticket_id,
        {
            "ticket_id": ticket_id,
            "session_id": session_id,
            "status": "open",
            "reason": reason,
            "category": contract.get("category"),
            "fragment": contract.get("fragment"),
            "tracking_ids": contract.get("tracking_ids", []),
            "contract_id": contract.get("contract_id"),
            "human_reply": None,
            "created_at": store.now(),
            "updated_at": store.now(),
        },
    )
    return ticket_id


async def get(ticket_id: str, session_id: str) -> dict | None:
    ticket = await store.get_doc("tickets", ticket_id.upper())
    return ticket if ticket and ticket.get("session_id") == session_id else None


async def reply(ticket_id: str, message: str, close: bool) -> dict | None:
    ticket = await store.get_doc("tickets", ticket_id.upper())
    if not ticket:
        return None
    update = {
        "human_reply": message,
        "status": "closed" if close else "answered",
        "updated_at": store.now(),
    }
    await store.put_doc("tickets", ticket_id.upper(), update)
    return {**ticket, **update}


def status_answer(ticket: dict) -> str:
    """Deterministic, model-free status reply for a ticket the user asks about."""
    head = f"**Ticket {ticket['ticket_id']}** ({ticket.get('category') or 'case'}) is "
    if ticket["status"] == "open":
        return (
            head + "**open**: our operations team has the full briefing and hasn't replied yet. "
            f"Reason it went to a person: {ticket.get('reason')}. I'll show their answer here "
            "as soon as they respond."
        )
    return head + f"**{ticket['status']}**. Reply from our team:\n\n> {ticket.get('human_reply')}"
