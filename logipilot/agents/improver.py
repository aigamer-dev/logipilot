"""Feedback loop: turn thumbs-down and verifier failures into learned rules, but only if they help.

    collect failures -> propose one rule per category (LLM) -> eval gate (Jev-scored, per category)
      -> promote if the score does not regress, else reject (kept for audit)

Stop rules: at most one promotion per run, at most MAX_ACTIVE_RULES active rules in total.

It runs automatically (`learning_loop`, started with the server): after AUTO_IMPROVE_NEGATIVES new
thumbs-down, or every AUTO_IMPROVE_EVERY_H hours when any new feedback arrived, at most once an
hour. Thumbs-up turns become regression eval cases, so the gate gets stricter as the app is used.
"""

import asyncio
import os
import time

from logipilot.agents import engine
from logipilot.common import config, healing, llm, store

MAX_EVAL_CASES = 5  # per category per gate run, newest first: bounds cost and time
AUTO_NEGATIVES = int(os.getenv("AUTO_IMPROVE_NEGATIVES", "3"))
AUTO_EVERY_S = float(os.getenv("AUTO_IMPROVE_EVERY_H", "6")) * 3600
CHECK_EVERY_S = 600
MIN_GAP_S = 3600
_running = asyncio.Lock()


async def _eval_score(category: str, extra_rule: str | None) -> float:
    """Mean verifier score of a category's eval cases, optionally with a candidate rule."""
    cases = await store.list_docs("eval_cases", limit=200, category=category)
    cases = sorted(cases, key=lambda c: c.get("created_at", ""), reverse=True)[:MAX_EVAL_CASES]
    if not cases:
        return 0.0
    rule_id = None
    if extra_rule:
        rule_id = await store.add_doc(
            "learned_rules",
            {"category": category, "rule": extra_rule, "status": "active", "candidate": True},
        )
    try:
        scores = []
        for case in cases:
            env = await engine.turn(f"eval-{case['id']}-{rule_id or 'base'}", case["message"], [])
            ver = [c.get("verification") or {} for c in env["contracts"]]
            scores += [
                (v.get("resolves", 0) + v.get("grounded", 0) + v.get("policy_ok", 0)) / 3
                for v in ver
            ] or [0.0]
        return sum(scores) / len(scores)
    finally:
        if rule_id:
            await store.put_doc("learned_rules", rule_id, {"status": "candidate"})


async def improve(trigger: str = "manual") -> dict:
    """One improvement cycle; concurrent calls are refused rather than run twice."""
    if _running.locked():
        return {"stopped": "a cycle is already running", "promoted": None, "rejected": []}
    async with _running:
        result = await _improve()
    await store.put_doc(
        "learning_state", "main", {"last_run_at": store.now(), "last_trigger": trigger,
                                   "last_result": result},
    )  # fmt: skip
    return result


async def add_regression_case(turn: dict, turn_id: str) -> int:
    """A thumbs-up turn becomes eval cases: each resolved, non-chat intent is a regression test."""
    added = 0
    for i, c in enumerate(turn.get("contracts", [])):
        if c.get("status") == "RELEASED" and c.get("category") not in (None, "general"):
            await store.put_doc(
                "eval_cases", f"fb-{turn_id}-{i}",
                {"category": c["category"], "message": c["fragment"], "source": "user_feedback",
                 "expected_actions": [a["kind"] for a in c.get("actions", [])],
                 "created_at": store.now()},
            )  # fmt: skip
            added += 1
    return added


async def _due() -> str | None:
    """Why a cycle should run now, or None. Counts feedback that arrived since the last run."""
    state = await store.get_doc("learning_state", "main") or {}
    last = state.get("last_run_at", "")
    if last and time.time() - _epoch(last) < MIN_GAP_S:
        return None
    fresh = [f for f in await store.list_docs("feedback", limit=500)
             if f.get("created_at", "") > last]  # fmt: skip
    negatives = sum(f.get("rating") == -1 for f in fresh)
    if negatives >= AUTO_NEGATIVES:
        return f"{negatives} new thumbs-down"
    if fresh and (not last or time.time() - _epoch(last) >= AUTO_EVERY_S):
        return f"scheduled ({len(fresh)} new feedback)"
    return None


def _epoch(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso).timestamp()


async def learning_loop() -> None:
    """Background task: checks every 10 minutes and runs a cycle when one is due."""
    while True:
        await asyncio.sleep(CHECK_EVERY_S)
        try:
            reason = await _due()
            if reason:
                print(f"[logipilot] learning cycle starting: {reason}", flush=True)
                result = await improve(trigger=reason)
                print(f"[logipilot] learning cycle done: promoted={bool(result.get('promoted'))}",
                      flush=True)  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - the loop must survive any single failure
            await healing.record("learning_loop", exc, "skipped this cycle; will retry")


async def _improve() -> dict:
    active = await store.list_docs("learned_rules", limit=500, status="active")
    if len(active) >= config.MAX_ACTIVE_RULES:
        return {"stopped": "active rule cap reached", "promoted": None, "rejected": []}

    feedback = await store.list_docs("feedback", limit=200, rating=-1)
    failures: dict[str, list[str]] = {}
    for fb in feedback:
        turn = await store.get_doc("turns", fb["turn_id"]) or {}
        for c in turn.get("contracts", []):
            note = (
                f"intent: {c['fragment']} | reply: {c['answer'][:400]} | user: {fb.get('comment')}"
            )
            failures.setdefault(c["category"], []).append(note)
    for case in await store.list_docs("cases", limit=200, status="ESCALATED"):
        if (case.get("verification") or {}).get("critique"):
            failures.setdefault(case["category"], []).append(case["verification"]["critique"])

    rejected = []
    for category, notes in sorted(failures.items(), key=lambda kv: -len(kv[1])):
        proposal = await llm.complete_json(
            f"You improve a logistics '{category}' support agent. From these failures, write ONE "
            'short, concrete, testable rule the agent should follow. Return {"rule": "..."}.\n'
            + "\n".join(notes[:15])
        )
        rule = proposal.get("rule", "").strip()
        # No eval cases means no evidence either way, so the gate refuses to promote.
        if not rule or not await store.list_docs("eval_cases", limit=1, category=category):
            continue
        baseline = await _eval_score(category, None)
        candidate = await _eval_score(category, rule)
        record = {"category": category, "rule": rule, "baseline": baseline, "score": candidate}
        if candidate >= baseline:
            await store.add_doc("learned_rules", {**record, "status": "active"})
            await store.add_doc("eval_runs", {**record, "decision": "promoted"})
            return {"promoted": record, "rejected": rejected}
        await store.add_doc("eval_runs", {**record, "decision": "rejected"})
        rejected.append(record)
    return {"promoted": None, "rejected": rejected}
