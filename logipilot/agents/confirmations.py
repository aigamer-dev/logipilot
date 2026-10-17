"""Confirmation harness: every write tool is queued until the customer says yes.

Specialists plan as usual; `specialists._before_tool` intercepts each write call, stores it here as
a pending action (tokenized arguments) and tells the model it was not executed. The master then
asks the customer to confirm the exact list. On "yes" the specialist re-runs with exactly those
actions granted; on "no" nothing runs.
"""

import re

YES = re.compile(
    r"^\W*(yes|y|yeah|yep|sure|ok|okay|confirm(ed)?|go ahead|proceed|do it|please do|haan|ha)\b",
    re.I,
)
NO = re.compile(r"^\W*(no|nope|cancel|stop|don'?t|do not|nahi|na)\b", re.I)

SUMMARIES = {
    "file_damage_claim": "File a claim on {tracking_id} for ${claim_value_usd:,.2f}",
    "create_return": "Create a return for {tracking_id} ({disposition})",
    "reroute_shipment": "Reroute {tracking_id} to {new_destination}",
    "request_customs_documents": "Request customs documents for {tracking_id}: {docs}",
    "open_billing_dispute": (
        "Open a billing dispute on {reference_id} for ${disputed_amount_usd:,.2f}"
    ),
    "create_replenishment_order": "Order {quantity} units of {sku} for {warehouse}",
    "notify_customer": "Send you a written confirmation",
}


def summary(action: dict) -> str:
    args = {**action["args"], "docs": ", ".join(action["args"].get("missing_documents") or [])}
    try:
        return SUMMARIES[action["tool"]].format(**args)
    except (KeyError, ValueError, TypeError):
        return action["tool"].replace("_", " ").capitalize()


def question(actions: list[dict]) -> str:
    lines = "\n".join(f"- {summary(a)}" for a in actions)
    return (
        f"Before I go ahead, please confirm you want me to:\n{lines}\n\n"
        "Reply **yes** to proceed or **no** to cancel. Nothing has been changed yet."
    )


def reply_kind(text: str) -> str:
    """'yes', 'no', or 'other' (the customer moved on, so the pending actions are dropped)."""
    if NO.search(text):
        return "no"
    return "yes" if YES.search(text) else "other"


def is_granted(tool: str, args: dict, granted: list[dict]) -> bool:
    """A confirmed action may be re-planned with new wording, but never with a different amount."""

    def numbers(a: dict) -> dict:
        return {k: v for k, v in a.items() if isinstance(v, int | float)}

    return any(g["tool"] == tool and numbers(g["args"]) == numbers(args) for g in granted)
