"""Execution contracts: one per intent in a user message, with a strict status lifecycle.

    ISSUED       master       intent decomposed, contract written
    NEEDS_INPUT  master       a required detail is missing; resumes on the next message
    ACCEPTED     master       passed every hard-stop gate, bound to a specialist
    EXECUTING    specialist   tools are being called
    DRAFTED      specialist   a resolution has been written
    VERIFIED     verifier     Jev confirmed it resolves, is grounded and is policy-safe
    REPAIRED     verifier     Jev rejected the draft; the critique goes back to the specialist
    RELEASED     master       answer returned to the user
    ESCALATED    master       a hard stop fired; a human gets a read-only briefing
    FAILED       master       unrecoverable error after self-healing
    REFUSED      master       guardrail or out-of-scope

Illegal transitions raise, so a corrupted trace can never be released silently.
"""

import uuid

from pydantic import BaseModel, Field

from logipilot.common.store import now

LEGAL: dict[str, set[str]] = {
    "ISSUED": {"ACCEPTED", "NEEDS_INPUT", "ESCALATED", "REFUSED", "FAILED"},
    "NEEDS_INPUT": set(),
    "ACCEPTED": {"EXECUTING", "FAILED"},
    "EXECUTING": {"DRAFTED", "FAILED", "ESCALATED"},
    "DRAFTED": {"VERIFIED", "REPAIRED", "FAILED"},
    "REPAIRED": {"EXECUTING", "ESCALATED"},
    "VERIFIED": {"RELEASED"},
    "RELEASED": set(),
    "ESCALATED": set(),
    "FAILED": set(),
    "REFUSED": set(),
}


class IllegalTransition(RuntimeError):
    pass


class Contract(BaseModel):
    contract_id: str = Field(default_factory=lambda: f"ct-{uuid.uuid4().hex[:10]}")
    fragment: str
    category: str = "general"
    confidence: float = 0.0
    severity: float = 0.0
    hazmat: float = 0.0
    action_requested: float = 0.0
    tracking_ids: list[str] = Field(default_factory=list)
    skus: list[str] = Field(default_factory=list)
    reference_ids: list[str] = Field(default_factory=list)
    claimed_value_usd: float = 0.0
    missing_info: str = ""
    evidence: str = ""
    status: str = "ISSUED"
    history: list[dict] = Field(default_factory=list)
    attempts: int = 0
    verification: dict | None = None
    actions: list[dict] = Field(default_factory=list)
    answer: str = ""
    escalation_reason: str = ""

    def advance(self, target: str, by: str, note: str = "") -> None:
        if target not in LEGAL[self.status]:
            raise IllegalTransition(f"{self.status} -> {target} is not a legal transition")
        self.history.append(
            {"from": self.status, "to": target, "by": by, "at": now(), "note": note}
        )
        self.status = target
