"""Deterministic pre-model guardrails. They name what they caught so the audit trail is useful.

Deliberately narrow: a guardrail that fires on ordinary logistics phrasing is worse than one that
misses, because users learn to rephrase around it. The model-layer instructions still apply.
"""

import re

RULES = {
    "G1_PROMPT_INJECTION": re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,30}\b(previous|prior|above|all|your)\b"
        r".{0,20}\b(instruction|prompt|rule|guideline)s?\b|\bdeveloper mode\b",
        re.I,
    ),
    "G2_CONFIDENTIALITY": re.compile(
        r"\b(show|print|reveal|repeat|display|tell me)\b.{0,30}\b(system prompt|your instructions"
        r"|your prompt|api key|secret|environment variables?)\b",
        re.I,
    ),
    "G3_ROLE_CHANGE": re.compile(
        r"\b(you are now|from now on you are|pretend (you are|to be)|roleplay as)\b|\bDAN\b", re.I
    ),
    "G4_OTHER_CUSTOMERS_DATA": re.compile(
        r"\b(list|dump|export|show)\b.{0,20}\b(all|every|other)\b.{0,20}\b(customers?|shipments?"
        r"|addresses|phone numbers)\b",
        re.I,
    ),
    "G5_UNSAFE_HANDLING": re.compile(
        r"\b(hide|conceal|mislabel|undeclare|smuggle)\b.{0,30}\b(hazmat|dangerous goods|batter"
        r"(y|ies)|customs|contents)\b",
        re.I,
    ),
}

REFUSAL = (
    "I can't help with that request. I can help with shipments, deliveries, damage, returns, "
    "customs, inventory, addresses and freight billing."
)


REFUSALS = {
    "G6_UNLAWFUL_REQUEST": (
        "I can't help with that: shipments have to be declared, labelled and valued accurately, "
        "and records can't be deleted on request. I'm happy to help with a legitimate shipment, "
        "customs paperwork, or a delivery problem."
    ),
    "OUT_OF_SCOPE": (
        "That's outside what I can help with: I handle shipments, delays, damage, lost parcels, "
        "returns, customs, stock and freight billing. If this is a medical or other emergency, "
        "please contact your local emergency services."
    ),
}


def refusal(flags: list[str]) -> str:
    return next((REFUSALS[f] for f in flags if f in REFUSALS), REFUSAL)


def scan(text: str) -> list[str]:
    """Return the names of every guardrail the text trips, in rule order."""
    return [name for name, pattern in RULES.items() if pattern.search(text or "")]
