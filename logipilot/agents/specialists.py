"""ADK agents: the decomposer, eight domain specialists, the concierge and the escalation drafter.

Agents are built by `build_agents(model_name, via_mcp)` so the master can swap in a fallback model or
direct REST tools at runtime (self-healing) without changing any agent's behaviour.
"""

import functools
import json

from google.adk.agents import LlmAgent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

from logipilot.agents import pii_vault
from logipilot.agents.skills import registry
from logipilot.common import config, healing, llm, tools_client

ESCALATION_TOOLS = ["lookup_shipment", "shipment_risk", "check_inventory"]

DECOMPOSER_PROMPT = """You are the decomposer of a logistics control tower. Read the latest user
message in the light of the whole conversation (including any attached images or documents) and
split it into independent intents, one per distinct problem or question.

Return ONLY JSON: {"intents": [{"fragment": "<the part of the message this intent covers, rewritten
to be self-contained using earlier conversation context>", "tracking_ids": ["LP1001"], "skus": [],
"reference_ids": ["INV-1"], "claimed_value_usd": 0, "evidence": "<what attachments show, or ''>",
"missing_info": "<the one detail needed to act that is still missing, or ''>"}]}
Copy ids exactly. Never invent ids or amounts. Greetings or capability questions are one intent
with no ids. Personal data appears as tokens like [EMAIL_1]; keep tokens as they are."""


async def _before_tool(tool, args: dict, tool_context) -> None:
    """Swap PII tokens for real values right before a tool runs, so models never see them."""
    vault = tool_context.state.get("pii_vault") or {}
    if vault:
        args.update(pii_vault.detokenize(args, vault))


async def _on_tool_error(tool, args: dict, tool_context, error: Exception) -> dict:
    """Turn a tool crash into data the specialist can reason about instead of a failed turn."""
    await healing.record(f"tool:{tool.name}", error, "returned the error to the agent as data")
    return {"error": f"{tool.name} failed: {error}. Do not retry more than once."}


def _tools(names: list[str], via_mcp: bool) -> list:
    if via_mcp:
        return [
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(url=config.MCP_URL, timeout=15),
                tool_filter=names,
            )
        ]
    return [getattr(tools_client, n) for n in names]


def _specialist_instruction(category: str):
    def build(ctx: ReadonlyContext) -> str:
        state = ctx.state
        contract = state.get("current_contract") or {}
        rules = state.get("learned_rules") or []
        return f"""{registry()[category].playbook}

## This turn
INTENT TO RESOLVE: {contract.get("fragment")}
KNOWN IDS: tracking {contract.get("tracking_ids")}, skus {contract.get("skus")}, refs {contract.get("reference_ids")}
EVIDENCE FROM ATTACHMENTS: {contract.get("evidence") or "none"}

LEARNED RULES (promoted from past feedback; follow them):
{chr(10).join(f"- {r}" for r in rules) or "- none yet"}

VERIFIER CRITIQUE OF YOUR PREVIOUS ATTEMPT: {state.get("critique") or "none, first attempt"}

Only use your tools; never invent data. Use the customer's email from the shipment record for
notify_customer. Keep PII tokens like [EMAIL_1] exactly as written."""

    return build


@functools.cache
def build_agents(model_name: str, via_mcp: bool) -> dict[str, LlmAgent]:
    model = llm.adk_model(model_name)
    agents: dict[str, LlmAgent] = {
        "decomposer": LlmAgent(
            name="decomposer",
            model=model,
            instruction=DECOMPOSER_PROMPT,
            output_key="decomposition",
        ),
        "general": LlmAgent(
            name="concierge",
            model=model,
            instruction=registry()["general"].playbook,
            output_key="draft",
        ),
        "clarifier": LlmAgent(
            name="clarifier",
            model=model,
            instruction=lambda ctx: (
                "Ask the user ONE short, friendly question to get the missing detail needed to "
                f"help with: {(ctx.state.get('current_contract') or {}).get('fragment')}. Missing: "
                f"{(ctx.state.get('current_contract') or {}).get('missing_info') or 'a tracking id'}."
            ),
            output_key="draft",
        ),
        "escalation": LlmAgent(
            name="escalation_drafter",
            model=model,
            instruction=lambda ctx: (
                "This intent hit a hard-stop rule and goes to a human operator. Reason: "
                f"{ctx.state.get('escalation_reason')}. Intent: "
                f"{json.dumps(ctx.state.get('current_contract'))}. Look up the referenced records, "
                "then reply to the customer: you've passed it to a specialist human team with a "
                "full briefing, what they will check, and any safety advice. Promise nothing else."
            ),
            tools=_tools(ESCALATION_TOOLS, via_mcp),
            before_tool_callback=_before_tool,
            on_tool_error_callback=_on_tool_error,
            output_key="draft",
        ),
    }
    for skill in registry().values():
        if skill.name == "general":
            continue
        agents[skill.name] = LlmAgent(
            name=f"{skill.name}_specialist",
            model=model,
            description=skill.description,
            instruction=_specialist_instruction(skill.name),
            tools=_tools(list(skill.tools), via_mcp),
            before_tool_callback=_before_tool,
            on_tool_error_callback=_on_tool_error,
            output_key="draft",
        )
    return agents
