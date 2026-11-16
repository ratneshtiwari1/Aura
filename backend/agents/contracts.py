from __future__ import annotations

from typing import Literal

from backend.policy import (
    MAX_AGENT_MESSAGE_CHARACTERS,
    MAX_AGENT_PLAN_STEPS,
    MAX_AGENT_REASON_CHARACTERS,
)

AgentAction = Literal[
    "answer_data_question", "understand_workbook", "suggest_kpis",
    "inspect_relationships", "prepare_data", "plan_dashboard", "edit_report",
    "respond", "clarify",
]

ALLOWED_ACTIONS = {
    "answer_data_question", "understand_workbook", "suggest_kpis",
    "inspect_relationships", "prepare_data", "plan_dashboard", "edit_report",
    "respond", "clarify",
}

# Discovery and validation must precede report planning or editing. This is a
# workflow dependency order, not a business-domain assumption.
ACTION_EXECUTION_ORDER = {
    "understand_workbook": 10,
    "inspect_relationships": 20,
    "answer_data_question": 30,
    "suggest_kpis": 50,
    "prepare_data": 60,
    "plan_dashboard": 80,
    "edit_report": 90,
    "respond": 100,
    "clarify": 100,
}


def validate_agent_plan(plan: object, has_report: bool) -> dict[str, object]:
    """Accept only a short, allow-listed plan before an executor sees it."""
    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get("steps"), list)
        or not 1 <= len(plan["steps"]) <= MAX_AGENT_PLAN_STEPS
    ):
        raise ValueError(f"Agent plan must include one to {MAX_AGENT_PLAN_STEPS} steps")
    steps = []
    for step in plan["steps"]:
        if not isinstance(step, dict) or step.get("action") not in ALLOWED_ACTIONS:
            raise ValueError("Agent plan selected an unsupported action")
        reason = step.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_AGENT_REASON_CHARACTERS:
            raise ValueError("Agent plan contains an invalid action reason")
        if step["action"] == "edit_report" and not has_report:
            raise ValueError("Agent cannot edit a report that does not exist")
        steps.append({"action": step["action"], "reason": reason.strip()})
    steps.sort(key=lambda step: ACTION_EXECUTION_ORDER[step["action"]])
    requires_approval = plan.get("requires_approval")
    if not isinstance(requires_approval, bool):
        raise ValueError("Agent plan must specify requires_approval")
    message = plan.get("message")
    if steps[0]["action"] in {"respond", "clarify"}:
        if not isinstance(message, str) or not message.strip() or len(message) > MAX_AGENT_MESSAGE_CHARACTERS:
            raise ValueError("Conversational agent actions require a concise message")
    result = {
        "action": steps[0]["action"],
        "reason": steps[0]["reason"],
        "requiresApproval": requires_approval,
        "plannedSteps": steps,
    }
    if isinstance(message, str) and message.strip():
        result["message"] = message.strip()
    return result
