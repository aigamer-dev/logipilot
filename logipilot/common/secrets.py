"""Secret scrubbing: no API key or token value may appear in a response, an incident or a log line.

Keys are only ever used server-side, but error messages from SDKs can embed request URLs or
headers (Gemini's key can travel as `?key=`). `redact` masks every known secret value and common
key-shaped substrings; `install_log_filter` applies it to every log record the process emits.
"""

import logging
import os
import re

SECRET_ENV = (
    "TYPESAFE_API_KEY", "OLLAMA_KEY", "OLLAMA_API_KEY", "GEMINI_API_KEY", "TOOLS_API_KEY",
    "ADMIN_TOKEN", "KEY_VAULT_TOKEN",
)  # fmt: skip
# Key shapes worth masking even if their value was never loaded into this process.
KEY_SHAPES = re.compile(
    r"apikey_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_\-]{30,}|lpv_[A-Za-z0-9_\-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
    r"|([?&](?:key|api_key|token)=)[^&\s\"']+",
    re.I,
)
MASK = "[secret]"


def _values() -> list[str]:
    found = [os.environ.get(name, "") for name in SECRET_ENV]
    sa = os.environ.get("GCP_SA_KEY_JSON", "")
    if '"private_key"' in sa:
        found += re.findall(r'"private_key(?:_id)?"\s*:\s*"([^"]+)"', sa)
    # Short values could collide with ordinary words; real keys are long.
    return sorted({v for v in found if len(v) >= 12}, key=len, reverse=True)


def redact(text: object) -> str:
    """Mask every known secret value and key-shaped substring in text."""
    out = str(text)
    for value in _values():
        out = out.replace(value, MASK)
    return KEY_SHAPES.sub(lambda m: (m.group(1) or "") + MASK, out)


def redact_obj(value: object) -> object:
    """redact() applied through strings, lists and dicts (for JSON responses)."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    return value


class _RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def install_log_filter() -> None:
    """Scrub secrets from every handler's output (root, uvicorn, litellm, httpx)."""
    flt = _RedactingFilter()
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "LiteLLM", "litellm", "httpx"):
        logger = logging.getLogger(name)
        logger.addFilter(flt)
        for handler in logger.handlers:
            handler.addFilter(flt)
