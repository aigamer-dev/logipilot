"""PII vault: models only ever see tokens like [EMAIL_1]; real values live in Firestore.

Tokens are swapped back to real values in exactly one place: tool arguments, so a notification
reaches the right inbox. Replies show masked values (r***@example.com), never the real ones.
"""

import re

from logipilot.common import store

PATTERNS = [
    # shortcut: house number + street only ("12 MG Road", "4/2 Gandhi Nagar"); city and pincode stay
    # visible so the address specialist can still check completeness. Misses free-form addresses.
    (
        "ADDRESS",
        re.compile(
            r"\b\d{1,5}[A-Za-z]?(?:[/-]\d{1,5})?,?\s+(?:[A-Za-z.']+\s+){0,4}"
            r"(?:Road|Rd|Street|St|Lane|Nagar|Avenue|Ave|Main|Cross|Layout|Colony|Marg|Salai"
            r"|Sector|Block)\b\.?"
        ),
    ),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("PHONE", re.compile(r"(?<![\w-])(?:\+?\d{1,3}[\s-]?)?[6-9]\d{4}[\s-]?\d{5}\b")),
]
TOKEN = re.compile(r"\[(?:CUST_)?(?:EMAIL|CARD|AADHAAR|PHONE|ADDRESS)_\d+\]")


async def load(session_id: str) -> dict[str, str]:
    return (await store.get_doc("pii_vault", session_id) or {}).get("tokens", {})


async def tokenize(session_id: str, text: str) -> tuple[str, int]:
    """Replace PII in text with stable per-session tokens. Returns (text, newly vaulted count)."""
    vault = await load(session_id)
    reverse = {v: k for k, v in vault.items()}
    added = 0
    for kind, pattern in PATTERNS:
        for value in set(pattern.findall(text)):
            if value not in reverse:
                token = f"[{kind}_{sum(k.startswith(f'[{kind}_') for k in vault) + 1}]"
                vault[token], reverse[value] = value, token
                added += 1
            text = text.replace(value, reverse[value])
    if added:
        await store.put_doc("pii_vault", session_id, {"tokens": vault, "updated_at": store.now()})
    return text, added


def detokenize(value: object, vault: dict[str, str]) -> object:
    """Swap tokens back to real values inside strings, lists and dicts (tool arguments only)."""
    if isinstance(value, str):
        return TOKEN.sub(lambda m: vault.get(m.group(0), m.group(0)), value)
    if isinstance(value, list):
        return [detokenize(v, vault) for v in value]
    if isinstance(value, dict):
        return {k: detokenize(v, vault) for k, v in value.items()}
    return value


def mask(value: str, kind: str = "") -> str:
    """Show enough of a value for the user to recognise it, never enough to reuse it."""
    if kind == "ADDRESS":
        return f"*** {value.rstrip('.').split()[-1]}"
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}"
    digits = re.sub(r"\D", "", value)
    return f"{'*' * max(len(digits) - 4, 0)}{digits[-4:]}" if len(digits) >= 8 else "[redacted]"


def mask_text(text: str, vault: dict[str, str]) -> str:
    """Output guardrail: vault tokens become masked values, and any raw PII still present is masked.

    Customer-record tokens ([CUST_*]) are left for the caller to phrase; they never become values.
    """
    text = TOKEN.sub(
        lambda m: (
            m.group(0)
            if m.group(0).startswith("[CUST_") or m.group(0) not in vault
            else mask(vault[m.group(0)], m.group(0)[1:].split("_")[0])
        ),
        text,
    )
    for kind, pattern in PATTERNS:
        text = pattern.sub(lambda m, k=kind: mask(m.group(0), k), text)
    return text


def retokenize(text: str, vault: dict[str, str]) -> str:
    """Swap real values back to their tokens, e.g. in a tool error that echoes its arguments."""
    for token, value in vault.items():
        text = text.replace(value, token)
    return text
