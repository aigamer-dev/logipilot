"""TypeSafe Jev, the System One model used for every typed decision in LogiPilot.

Jev answers typed questions (choice, score, noul) about a state in one parallel pass with calibrated
probabilities. LogiPilot uses it for routing, the hard-stop gate, verification and eval scoring.
If Jev is unreachable, `ask` heals by asking the LLM the same typed questions and marks the answers
`"source": "llm_fallback"`.
"""

import json
import os

import httpx

from logipilot.common import healing, llm

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = os.getenv("JEV_MODEL", "jev-latest")

_client = httpx.AsyncClient(timeout=10.0)
breaker = healing.CircuitBreaker("jev")


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: str, criteria: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": criteria}


def noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


async def _call(state: str | dict, questions: dict[str, dict]) -> dict:
    resp = await _client.post(
        JEV_URL,
        headers={"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"},
        json={"model": JEV_MODEL, "state": state, "questions": questions},
    )
    resp.raise_for_status()
    return resp.json()["answers"]


async def _fallback(state: str | dict, questions: dict[str, dict]) -> dict:
    prompt = (
        "Answer each typed question about the STATE. Return only JSON mapping each question id to "
        'an object: choice -> {"choice": <key>, "confidence": 0..1}; score -> {"score": <0-based '
        'index as float>, "confidence": 0..1}; noul -> {"noul": <probability 0..1>}.\n'
        f"STATE: {json.dumps(state, ensure_ascii=False)}\nQUESTIONS: {json.dumps(questions)}"
    )
    answers = await llm.complete_json(prompt)
    return {k: {**v, "source": "llm_fallback"} for k, v in answers.items()}


async def ask(state: str | dict, questions: dict[str, dict]) -> dict:
    """Return Jev's answers keyed by question id, healing to the LLM if Jev is down."""
    if not breaker.is_open:
        try:
            answers = await healing.retry(lambda: _call(state, questions), "jev", attempts=2)
            breaker.success()
            return answers
        except Exception as exc:
            breaker.failure()
            await healing.record("jev", exc, "answered typed questions with the LLM fallback")
    return await _fallback(state, questions)
