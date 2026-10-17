"""The master orchestrator: a custom ADK agent that runs the same deterministic workflow every turn.

    decompose -> issue one contract per intent -> Jev triage -> hard-stop gate
      -> specialist (skill playbook + MCP tools) -> Jev verifier (repair loop) -> compose -> release

Models fill in perception and judgment; the order of steps, the gates and the stop rules are code.
Guardrails and the PII vault run before this agent, in the engine, so raw PII never enters the
session that models read.
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types

from logipilot.agents import tickets
from logipilot.agents.contracts import Contract
from logipilot.agents.skills import registry
from logipilot.agents.specialists import build_agents
from logipilot.common import config, healing, jev, llm, store, tools_client

mcp_breaker = healing.CircuitBreaker("mcp")
# Tool results arrive as JSON, sometimes nested as escaped text inside an MCP content block.
ACTION_ID = re.compile(r'action_id\\*"\s*:\s*\\*"(\w+)')
ACTION_KIND = re.compile(r'"?kind\\*"\s*:\s*\\*"(\w+)')
MONEY_SKILLS = {"damage", "lost", "returns", "billing"}
# Deterministic id extraction backs up the decomposer: ids in the text are never dropped.
ID_PATTERNS = {
    "tracking_ids": re.compile(r"\bLP\d{4}\b", re.I),
    "skus": re.compile(r"\bSKU-[A-Z0-9-]+\b", re.I),
    "reference_ids": re.compile(r"\b(?:INV|PO|ORD)-\d+\b", re.I),
}


ACTION_CLAIM = re.compile(
    r"\bI(?:'ve| have| just)?\s+(?:filed|opened|created|raised|submitted|requested|arranged|"
    r"rerouted|initiated|scheduled|sent|notified|placed|updated|changed|booked)\b"
    r"|\b(?:cancelled|canceled|withdrawn|refunded|reversed)\b",
    re.I,
)


def _merge_ids(found: list[str], text: str, pattern: re.Pattern) -> list[str]:
    ids = [i.upper() for i in found] + [m.upper() for m in pattern.findall(text)]
    return list(dict.fromkeys(ids))


class MasterOrchestrator(BaseAgent):
    """Owns every contract's lifecycle; specialists only ever draft."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        started = time.monotonic()
        state = ctx.session.state
        trace: list[dict] = state.get("turn_trace") or []

        def step(name: str, detail: str, t0: float) -> None:
            trace.append(
                {"step": name, "detail": detail, "ms": int((time.monotonic() - t0) * 1000)}
            )

        t0 = time.monotonic()
        intents: list[dict] = []

        # 1. Decompose the message into intents.
        if not intents:
            async for event, _ok in self._run("decomposer", ctx):
                if event:
                    yield event
            try:
                intents = llm.parse_json(state.get("decomposition") or "")["intents"]
            except (ValueError, KeyError, TypeError) as exc:
                await healing.record("decomposer", exc, "treated the whole message as one intent")
            if not intents:
                intents = [{"fragment": _user_text(ctx)}]
            step("decomposer", f"{len(intents)} intent(s)", t0)

        # 2. Issue contracts and triage them with Jev in parallel.
        t0 = time.monotonic()
        contracts = [
            Contract(
                fragment=i.get("fragment") or _user_text(ctx),
                tracking_ids=_merge_ids(
                    i.get("tracking_ids") or [],
                    i.get("fragment") or "",
                    ID_PATTERNS["tracking_ids"],
                ),
                skus=_merge_ids(i.get("skus") or [], i.get("fragment") or "", ID_PATTERNS["skus"]),
                reference_ids=_merge_ids(
                    i.get("reference_ids") or [],
                    i.get("fragment") or "",
                    ID_PATTERNS["reference_ids"],
                ),
                claimed_value_usd=float(i.get("claimed_value_usd") or 0),
                missing_info=i.get("missing_info") or "",
                evidence=i.get("evidence") or "",
                language=i.get("language") or "",
            )
            for i in intents
        ]
        await asyncio.gather(*(self._triage(c) for c in contracts))
        step("jev_triage", ", ".join(f"{c.category}@{c.confidence:.2f}" for c in contracts), t0)

        # 3. Gate and execute each contract in order (deterministic order = replayable turns).
        for contract in contracts:
            async for event in self._execute(ctx, contract, started, step):
                yield event

        # 4. Compose and release.
        answer = (
            contracts[0].answer
            if len(contracts) == 1
            else "\n\n".join(f"**{c.fragment}**\n\n{c.answer}" for c in contracts)
        )
        statuses = {c.status for c in contracts}
        overall = next(
            (s for s in ("ESCALATED", "NEEDS_INPUT", "FAILED", "REFUSED") if s in statuses),
            "RELEASED",
        )
        yield self._final(ctx, state, trace, answer, overall, contracts)

    def _final(self, ctx, state, trace, answer: str, status: str, contracts: list) -> Event:
        """The one event that carries the turn's envelope and persists session-level state."""
        envelope = {
            "answer": answer,
            "status": status,
            "contracts": [c.model_dump() for c in contracts],
            "trace": trace,
        }
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            content=types.Content(role="model", parts=[types.Part(text=answer)]),
            actions=EventActions(
                state_delta={
                    "envelope": envelope,
                    "clarifications": state.get("clarifications", 0),
                    "turn_trace": [],
                }
            ),
        )

    async def _triage(self, contract: Contract) -> None:
        skills = registry()
        answers = await jev.ask(
            {
                "intent": contract.fragment,
                "evidence": contract.evidence,
                "ids": contract.tracking_ids + contract.skus + contract.reference_ids,
            },
            {
                "category": jev.choice(
                    "Which logistics skill should handle this intent",
                    {name: s.description for name, s in skills.items()},
                ),
                "severity": jev.score(
                    "How severe is the problem for the customer or the goods",
                    [
                        "Routine question",
                        "Inconvenient",
                        "Serious loss or delay",
                        "Critical: safety risk, medical or perishable goods at risk, or large loss",
                    ],
                ),
                "action_requested": jev.noul(
                    "The customer wants something done or fixed: an explicit request (file a "
                    "claim, reroute, change address, return, reorder stock, notify someone) or a "
                    "complaint that needs a remedy (charged twice, wrong fee, damaged or lost "
                    "goods). False when they only ask for status or information."
                ),
                "hazmat": jev.noul(
                    "The goods involve hazardous materials such as lithium batteries, chemicals or "
                    "flammables, or there is a leak or spill"
                ),
            },
        )
        cat = answers["category"]
        contract.category = cat.get("choice") if cat.get("choice") in skills else "general"
        contract.confidence = float(cat.get("confidence") or 0)
        contract.severity = float(answers["severity"].get("score") or 0)
        contract.hazmat = float(answers["hazmat"].get("noul") or 0)
        contract.action_requested = float(answers["action_requested"].get("noul") or 0)

    async def _gate(self, ctx: InvocationContext, contract: Contract) -> tuple[str, str]:
        """Hard-stop gate. Returns (decision, reason), decision in proceed|ask|escalate."""
        skill = registry()[contract.category]
        missing = [f for f in skill.required_fields if not getattr(contract, f)]
        has_ids = bool(contract.tracking_ids or contract.skus or contract.reference_ids)
        # With an id in hand a lookup beats a question; without one, low confidence means ask.
        unsure = (
            contract.category != "general"
            and contract.confidence < config.MIN_ROUTE_CONFIDENCE
            and not has_ids
        )
        if missing and not unsure and contract.action_requested < 0.5:
            # A general how-to question ("what is a commercial invoice?") needs no id.
            contract.category = "general"
            return "proceed", "general knowledge question, no id needed"
        if missing or unsure:
            if ctx.session.state.get("clarifications", 0) >= config.MAX_CLARIFICATIONS:
                return "escalate", "too many clarifying questions without the needed details"
            if unsure:
                contract.missing_info = "what exactly went wrong (late, lost, damaged, ...)"
                return "ask", f"routing confidence {contract.confidence:.2f} below threshold"
            contract.missing_info = contract.missing_info or f"{', '.join(missing)}"
            return "ask", f"missing {missing}"
        if contract.severity >= 2.5:
            return "escalate", "critical severity"
        # Hazmat escalates when something must be done or is going wrong, not for an ETA question.
        risky = contract.action_requested >= 0.5 or contract.severity >= 1.5
        if contract.hazmat >= 0.7 and risky:
            return "escalate", "hazardous materials involved"
        records = await asyncio.gather(
            *(tools_client.lookup_shipment(t) for t in contract.tracking_ids),
            return_exceptions=True,
        )
        unknown = [
            t
            for t, rec in zip(contract.tracking_ids, records, strict=True)
            if isinstance(rec, dict) and "error" in rec
        ]
        if unknown:
            contract.missing_info = f"a valid tracking id ({', '.join(unknown)} was not found)"
            return "unknown_id", f"not found: {unknown}"
        for rec in records:
            if not isinstance(rec, dict):
                continue
            if rec.get("hazmat") and risky:
                return "escalate", f"{rec['tracking_id']} is a hazmat shipment"
            value = max(float(rec.get("declared_value_usd") or 0), contract.claimed_value_usd)
            if contract.category in MONEY_SKILLS and value > config.HIGH_VALUE_USD:
                return "escalate", f"{rec['tracking_id']} has ${value:,.0f} at stake"
        if contract.category in MONEY_SKILLS and contract.claimed_value_usd > config.HIGH_VALUE_USD:
            return "escalate", f"${contract.claimed_value_usd:,.0f} at stake"
        return "proceed", "all gates passed"

    async def _execute(self, ctx, contract: Contract, started: float, step):
        state = ctx.session.state
        state["current_contract"] = contract.model_dump()
        state["critique"] = ""
        t0 = time.monotonic()
        ticket_ref = tickets.TICKET_ID.search(contract.fragment)
        if ticket_ref:
            ticket = await tickets.get(ticket_ref.group(0), ctx.session.id)
            contract.category = "general"
            for status in ("ACCEPTED", "EXECUTING", "DRAFTED", "VERIFIED", "RELEASED"):
                contract.advance(status, "master", "answered from the ticket record")
            contract.answer = (
                tickets.status_answer(ticket)
                if ticket
                else f"I can't find ticket {ticket_ref.group(0).upper()} in this conversation."
            )
            step("ticket_lookup", "found" if ticket else "not found", t0)
            return
        decision, reason = await self._gate(ctx, contract)
        step("hard_stop_gate", f"{contract.category}: {decision} ({reason})", t0)

        if decision == "unknown_id":
            contract.advance("NEEDS_INPUT", "master", reason)
            state["clarifications"] = state.get("clarifications", 0) + 1
            contract.answer = (
                f"I couldn't find {', '.join(reason.split('[')[1].strip(']').split(', '))} in our "
                "system. Could you check the tracking id? It looks like LP followed by 4 digits, "
                "for example LP1001."
            ).replace("'", "")
            return
        if decision == "ask":
            contract.advance("NEEDS_INPUT", "master", reason)
            state["clarifications"] = state.get("clarifications", 0) + 1
            async for event, _ in self._run("clarifier", ctx):
                if event:
                    yield event
            contract.answer = (
                state.get("draft") or "Could you share your tracking id (like LP1001)?"
            )
            return
        if decision == "escalate":
            contract.escalation_reason = reason
            contract.advance("ESCALATED", "master", reason)
            state["escalation_reason"] = reason
            async for event, _ in self._run("escalation", ctx):
                if event:
                    yield event
            contract.answer = state.get("draft") or "I've passed this to our operations team."
            await self._open_ticket(ctx, contract)
            return

        state["clarifications"] = 0
        # Write tools are bound only when the customer asked for an action (deterministic gate).
        state["read_only"] = contract.action_requested < 0.5
        state["learned_rules"] = await store.active_rules(contract.category)
        contract.advance("ACCEPTED", "master", reason)
        evidence: list[str] = []  # accumulated across attempts so repairs stay grounded
        while True:
            contract.attempts += 1
            contract.advance("EXECUTING", "specialist", f"attempt {contract.attempts}")
            t0 = time.monotonic()
            ok = False
            async for event, ok in self._run(  # noqa: B007 - last value read after the loop
                contract.category, ctx
            ):
                if event:
                    for resp in event.get_function_responses() or []:
                        evidence.append(json.dumps(resp.response, default=str)[:3000])
                    yield event
            for result in evidence:
                for action_id in ACTION_ID.findall(result):
                    if all(a["action_id"] != action_id for a in contract.actions):
                        kind = ACTION_KIND.search(result)
                        contract.actions.append(
                            {"action_id": action_id, "kind": kind.group(1) if kind else "action"}
                        )
            if not ok:
                contract.advance("FAILED", "master", "specialist failed after self-healing")
                done = ", ".join(f"{a['kind']} ({a['action_id']})" for a in contract.actions)
                contract.answer = (
                    f"I completed: {done}. I couldn't finish the rest of the reply; please ask "
                    "again and I'll pick up from there."
                    if done
                    else "Sorry, I couldn't complete that right now and nothing was changed."
                )
                contract.escalation_reason = "system error after self-healing"
                await self._open_ticket(ctx, contract)
                return
            contract.answer = state.get("draft") or ""
            contract.advance("DRAFTED", "specialist")
            if not contract.actions and contract.answer.rstrip().endswith("?"):
                # The specialist needs something from the customer; that is a question, not a
                # failed answer, so it neither loops the verifier nor opens a human ticket.
                contract.advance("NEEDS_INPUT", "specialist", "asked the customer for details")
                return
            if contract.category == "general":
                # Small talk has nothing to ground or verify; release it directly.
                contract.advance("VERIFIED", "master", "no claims to verify")
                contract.advance("RELEASED", "master")
                return
            step(
                f"{contract.category}_specialist",
                f"attempt {contract.attempts}, " f"{len(evidence)} tool result(s)",
                t0,
            )

            t0 = time.monotonic()
            contract.verification = await self._verify(contract, evidence)
            step("jev_verifier", json.dumps(contract.verification), t0)
            if contract.verification["passed"]:
                contract.advance("VERIFIED", "verifier")
                contract.advance("RELEASED", "master")
                return
            contract.advance("REPAIRED", "verifier", contract.verification["critique"])
            out_of_time = time.monotonic() - started > config.TURN_DEADLINE_S
            if (contract.attempts >= config.MAX_ATTEMPTS or out_of_time) and state.get("read_only"):
                # Information-only answers change nothing; release the best effort with a caveat
                # instead of spending a human's time.
                contract.advance("RELEASED", "master", "best-effort read-only answer")
                contract.answer += (
                    '\n\n_If any of this looks off, say "talk to a person" and I\'ll open '
                    "a ticket._"
                )
                return
            if contract.attempts >= config.MAX_ATTEMPTS or out_of_time:
                reason = "time budget exhausted" if out_of_time else "verifier rejected 3 drafts"
                contract.escalation_reason = reason
                contract.advance("ESCALATED", "master", reason)
                contract.answer += (
                    "\n\n_I've also asked a human teammate to double-check this for you._"
                )
                await self._open_ticket(ctx, contract)
                return
            state["critique"] = contract.verification["critique"]

    async def _open_ticket(self, ctx: InvocationContext, contract: Contract) -> None:
        """Every hand-off gets a ticket the customer can follow up on."""
        contract.ticket_id = await tickets.create(
            ctx.session.id, contract.model_dump(), contract.escalation_reason
        )
        contract.answer += (
            f'\n\n**Ticket ID: {contract.ticket_id}**. Ask me "status of {contract.ticket_id}" '
            "anytime to see whether our team has replied."
        )

    async def _verify(self, contract: Contract, evidence: list[str]) -> dict:
        answers = await jev.ask(
            {
                "intent": contract.fragment,
                "reply": contract.answer,
                "tool_evidence": "\n".join(evidence)[:8000],
            },
            {
                "resolves": jev.noul("The reply directly addresses and resolves the intent"),
                "grounded": jev.noul(
                    "Every status, date, amount and action id in the reply appears in the tool "
                    "evidence (or the reply makes no such claims)"
                ),
                "policy_ok": jev.noul(
                    "The reply follows policy: no refunds promised beyond declared value, no "
                    "rerouting of hazardous goods, no other customers' data, no invented facts"
                ),
            },
        )
        scores = {
            k: float(answers[k].get("noul") or 0) for k in ("resolves", "grounded", "policy_ok")
        }
        failed = [
            k
            for k, floor in (("resolves", 0.6), ("grounded", 0.5), ("policy_ok", 0.7))
            if scores[k] < floor
        ]
        # No tool can cancel or refund, so those claims are always invented.
        phantom = re.search(r"\b(cancell?ed|withdrawn|refunded|reversed)\b", contract.answer, re.I)
        if (ACTION_CLAIM.search(contract.answer) and not contract.actions) or phantom:
            failed.append("phantom_action")
        critique = (
            "Fix these problems: "
            + ", ".join(
                {
                    "resolves": "the reply does not resolve the intent",
                    "grounded": "the reply states facts not found in tool results; look them up",
                    "policy_ok": "the reply breaks policy",
                    "phantom_action": "the reply claims an action that no tool performed; "
                    "describe only actions with an action id from the tool results",
                }[k]
                for k in failed
            )
            if failed
            else ""
        )
        return {**scores, "passed": not failed, "critique": critique}

    async def _run(self, key: str, ctx: InvocationContext):
        """Run one sub-agent with self-healing: MCP -> direct REST tools -> fallback model.

        Yields (event, ok) pairs; the final pair is (None, success).
        """
        models = [config.LLM_MODEL, config.LLM_FALLBACK_MODEL]
        if config.GEMINI_API_KEY:
            models.append(config.GEMINI_FALLBACK_MODEL)
        # A model that just failed is skipped for a while, so later calls go straight to one
        # that works instead of re-walking the whole chain (the last model is always tried).
        live = [m for m in models if not _model_breaker(m).is_open] or models[-1:]
        plans = [(live[0], True)] if not mcp_breaker.is_open else []
        plans += [(m, False) for m in live]
        for model_name, via_mcp in plans:
            agent = build_agents(model_name, via_mcp, bool(ctx.session.state.get("read_only")))[key]
            try:
                async for event in agent.run_async(ctx):
                    yield event, True
                _model_breaker(model_name).success()
                if via_mcp:
                    mcp_breaker.success()
                yield None, True
                return
            except Exception as exc:  # noqa: BLE001 - every failure gets a recovery path below
                if _is_model_error(exc):
                    breaker = _model_breaker(model_name)
                    # A rejected key won't fix itself in 30 s; keep that model out for 10 min.
                    breaker.cooldown = 600.0 if "Authentication" in type(exc).__name__ else 30.0
                    breaker.failure()
                    await healing.record(f"llm:{model_name}", exc, "retrying with the next model")
                elif via_mcp:
                    mcp_breaker.failure()
                    await healing.record("mcp", exc, "switched tools to the direct REST API")
                else:
                    await healing.record(f"agent:{key}", exc, "retrying with the next model")
        yield None, False


_model_breakers: dict[str, healing.CircuitBreaker] = {}


def _model_breaker(model: str) -> healing.CircuitBreaker:
    return _model_breakers.setdefault(model, healing.CircuitBreaker(f"llm:{model}", threshold=1))


def _is_model_error(exc: Exception) -> bool:
    """LLM provider failures (auth, rate limit, outage) come from litellm, not from MCP."""
    return type(exc).__module__.startswith("litellm") or "litellm" in str(exc).lower()


def _user_text(ctx: InvocationContext) -> str:
    parts = (ctx.user_content.parts if ctx.user_content else None) or []
    return " ".join(p.text for p in parts if p.text) or "(attachment only)"


master_agent = MasterOrchestrator(
    name="master", description="Logistics master orchestrator (contract-based workflow)"
)
