"""Runtime settings, read once from the environment (and `.env` when present)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "https://ollama.com")
OLLAMA_KEY = os.getenv("OLLAMA_KEY") or os.getenv("OLLAMA_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma4:31b")
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "gpt-oss:20b")

TOOLS_API_URL = os.getenv("TOOLS_API_URL", "http://localhost:8080")
MCP_URL = os.getenv("MCP_URL", "http://localhost:8081/mcp")

# Hard-stop criteria. Each one is a code check on a typed value, never a model opinion.
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
TURN_DEADLINE_S = float(os.getenv("TURN_DEADLINE_S", "60"))
MIN_ROUTE_CONFIDENCE = float(os.getenv("MIN_ROUTE_CONFIDENCE", "0.6"))
MAX_CLARIFICATIONS = int(os.getenv("MAX_CLARIFICATIONS", "3"))
HIGH_VALUE_USD = float(os.getenv("HIGH_VALUE_USD", "5000"))
MAX_ACTIVE_RULES = int(os.getenv("MAX_ACTIVE_RULES", "20"))
