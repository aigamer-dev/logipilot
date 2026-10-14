"""PII vault: models only ever see tokens like [EMAIL_1]; real values live in Firestore.

Tokens are replaced back with real values only at the two places that need them: tool arguments
(so a notification reaches the right inbox) and the final answer shown to the same user.
"""

import re

from logipilot.common import store

PATTERNS = [
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


def detokenize(value: object, vault: dict[str, str], customer: bool = True) -> object:
    """Swap tokens back to real values inside strings, lists and dicts.

    customer=False leaves [CUST_*] tokens (customer contact details read from records) in place:
    replies only ever reveal PII the user typed themselves.
    """
    if isinstance(value, str):
        return TOKEN.sub(
            lambda m: (
                m.group(0)
                if not customer and m.group(0).startswith("[CUST_")
                else vault.get(m.group(0), m.group(0))
            ),
            value,
        )
    if isinstance(value, list):
        return [detokenize(v, vault, customer) for v in value]
    if isinstance(value, dict):
        return {k: detokenize(v, vault, customer) for k, v in value.items()}
    return value
