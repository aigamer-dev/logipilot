"""Feedback loop: turn thumbs-down and verifier failures into learned rules, but only if they help.

    collect failures -> propose one rule per category (LLM) -> eval gate (Jev-scored, per category)
      -> promote if the score does not regress, else reject (kept for audit)

Stop rules: at most one promotion per run, at most MAX_ACTIVE_RULES active rules in total.
"""

from logipilot.agents import engine
from logipilot.common import config, llm, store


async def _eval_score(category: str, extra_rule: str | None) -> float:
    """Mean verifier score of this category's eval cases, optionally with a candidate rule active."""
    cases = await store.list_docs("eval_cases", category=category)
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


async def improve() -> dict:
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
        if not rule:
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
