"""Runtime settings, read once from the environment (and `.env` when present)."""

import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def _load_from_vault() -> None:
    """Fill missing keys from the Cloudflare Worker vault (judges' setup: one access token only).

    Keys already set in the environment always win; the vault only fills gaps.
    """
    url, token = os.getenv("KEY_VAULT_URL"), os.getenv("KEY_VAULT_TOKEN")
    if not url or not token:
        return
    import httpx

    resp = httpx.get(f"{url.rstrip('/')}/v1/keys", headers={"Authorization": f"Bearer {token}"},
                     timeout=10)  # fmt: skip
    resp.raise_for_status()
    for key, value in resp.json().items():
        os.environ.setdefault(key, value)
    sa_json = os.environ.get("GCP_SA_KEY_JSON")
    if sa_json and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        path = Path(tempfile.gettempdir()) / f"logipilot-sa-{os.getuid()}.json"
        path.write_text(sa_json)
        path.chmod(0o600)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)


_load_from_vault()

OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "https://ollama.com")
OLLAMA_KEY = os.getenv("OLLAMA_KEY") or os.getenv("OLLAMA_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma4:31b")
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "gpt-oss:20b")

TOOLS_API_URL = os.getenv("TOOLS_API_URL", "http://localhost:8090")
MCP_URL = os.getenv("MCP_URL", "http://localhost:8091/mcp")

# Hard-stop criteria. Each one is a code check on a typed value, never a model opinion.
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
TURN_DEADLINE_S = float(os.getenv("TURN_DEADLINE_S", "60"))
MIN_ROUTE_CONFIDENCE = float(os.getenv("MIN_ROUTE_CONFIDENCE", "0.6"))
MAX_CLARIFICATIONS = int(os.getenv("MAX_CLARIFICATIONS", "3"))
HIGH_VALUE_USD = float(os.getenv("HIGH_VALUE_USD", "5000"))
MAX_ACTIVE_RULES = int(os.getenv("MAX_ACTIVE_RULES", "20"))
