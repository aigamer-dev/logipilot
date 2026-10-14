"""Chat engine: one conversational turn end to end.

guardrails -> PII vault -> attachments to parts -> ADK Runner(master) -> detokenize -> persist
"""

import io
import time
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pypdf import PdfReader

from logipilot.agents import guardrails, pii_vault
from logipilot.agents.master import master_agent
from logipilot.common import healing, store

APP = "logipilot"
# shortcut: sessions live in process memory (transcripts are also persisted to Firestore);
# move to a database-backed ADK session service before running more than one instance.
sessions = InMemorySessionService()
runner = Runner(
    app_name=APP, agent=master_agent, session_service=sessions, auto_create_session=True
)

MAX_TEXT_CHARS = 8000


def _attachment_part(name: str, mime: str, data: bytes) -> tuple[types.Part | None, str]:
    """Turn one upload into a model part (images) or extracted text (PDF, CSV, text)."""
    if mime.startswith("image/"):
        return types.Part.from_bytes(data=data, mime_type=mime), ""
    if mime == "application/pdf" or name.lower().endswith(".pdf"):
        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        return None, f"[Attached PDF {name}]\n{text[:MAX_TEXT_CHARS]}"
    if mime.startswith("text/") or name.lower().endswith((".csv", ".txt", ".json")):
        return None, f"[Attached file {name}]\n{data.decode('utf-8', 'replace')[:MAX_TEXT_CHARS]}"
    if mime.startswith("audio/"):
        return None, f"[Voice note {name} attached; use the mic button to dictate instead]"
    return None, f"[Attachment {name} of type {mime} is not supported]"


async def turn(session_id: str, message: str, files: list[tuple[str, str, bytes]]) -> dict:
    started = time.monotonic()
    turn_id = f"turn-{uuid.uuid4().hex[:12]}"
    incidents: list[dict] = []
    token = healing.turn_incidents.set(incidents)
    trace: list[dict] = []
    try:
        flags = guardrails.scan(message)
        trace.append({"step": "guardrails", "detail": ", ".join(flags) or "clean", "ms": 0})
        if flags:
            envelope = {"answer": guardrails.REFUSAL, "status": "REFUSED", "contracts": []}
            pii_count = 0
        else:
            parts, texts = [], []
            for name, mime, data in files:
                part, text = _attachment_part(name, mime, data)
                if part:
                    parts.append(part)
                if text:
                    texts.append(text)
            raw = "\n\n".join([message, *texts]).strip() or "(see attachment)"
            t0 = time.monotonic()
            safe_text, pii_count = await pii_vault.tokenize(session_id, raw)
            vault = await pii_vault.load(session_id)
            trace.append(
                {
                    "step": "pii_vault",
                    "detail": f"{pii_count} item(s) vaulted",
                    "ms": int((time.monotonic() - t0) * 1000),
                }
            )
            content = types.Content(role="user", parts=[types.Part(text=safe_text), *parts])
            envelope = None
            async for event in runner.run_async(
                user_id=session_id,
                session_id=session_id,
                new_message=content,
                state_delta={"pii_vault": vault, "turn_trace": trace},
            ):
                delta = event.actions.state_delta if event.actions else {}
                if event.author == "master" and "envelope" in delta:
                    envelope = delta["envelope"]
            if envelope is None:
                raise RuntimeError("master produced no envelope")
            envelope["answer"] = pii_vault.detokenize(envelope["answer"], vault)
            trace = envelope.pop("trace", trace)
        envelope.update(
            session_id=session_id,
            turn_id=turn_id,
            trace=trace,
            incidents=incidents,
            guardrail_flags=flags,
            pii_vaulted=pii_count,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        # Stored with the tokenized message only; the vault is the single home of raw PII.
        await store.put_doc(
            "turns",
            turn_id,
            {**envelope, "message": message if flags else "[tokenized]", "created_at": store.now()},
        )
        for c in envelope["contracts"]:
            await store.put_doc(
                "cases", c["contract_id"], {**c, "turn_id": turn_id, "session_id": session_id}
            )
        return envelope
    finally:
        healing.turn_incidents.reset(token)
