"""LLM access: Gemma 4 31B on Ollama cloud, through ADK's LiteLLM wrapper and plain litellm."""

import json
import re

import litellm
from google.adk.models.lite_llm import LiteLlm

from logipilot.common import config

litellm.drop_params = True


def adk_model(name: str | None = None) -> LiteLlm:
    """An ADK model handle for an Ollama cloud model (primary unless a name is given)."""
    return LiteLlm(
        model=f"ollama_chat/{name or config.LLM_MODEL}",
        api_base=config.OLLAMA_API_BASE,
        api_key=config.OLLAMA_KEY,
        temperature=0,
    )


async def complete_json(prompt: str, model: str | None = None) -> dict:
    """One JSON-returning completion, used where an ADK agent would be overkill."""
    resp = await litellm.acompletion(
        model=f"ollama_chat/{model or config.LLM_MODEL}",
        api_base=config.OLLAMA_API_BASE,
        api_key=config.OLLAMA_KEY,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
        timeout=30,
    )
    return parse_json(resp.choices[0].message.content or "")


def parse_json(text: str) -> dict:
    """Pull the first JSON object out of model text (models sometimes wrap it in a code fence)."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"No JSON object in model output: {text[:200]!r}")
    return json.loads(match.group(0))
