from __future__ import annotations

import re


def route_agent_request(request: str, has_report: bool) -> dict[str, str | bool]:
    """Select exactly one safe next action; this never executes the action."""
    text = request.casefold()

    def matches(words: str) -> bool:
        return bool(re.search(words, text))

    if matches(r"^\s*(thanks|thank you)[!.?\s]*$"):
        return {"action": "respond", "reason": "Acknowledgment", "requiresApproval": False,
                "message": "You’re welcome. You can ask another question or describe what you’d like to change."}
    if matches(r"^\s*(hi|hello|hey|good morning|good afternoon|good evening|thanks|thank you|who are you|what can you do)[!.?\s]*$"):
        return {
            "action": "respond",
            "reason": "The user is starting or continuing a conversation.",
            "requiresApproval": False,
            "message": "I can help you understand the uploaded workbook, investigate its data, and build or revise a Power BI report. What would you like to do?",
        }
    if text.strip() in {"do it", "change it", "make it better", "fix it", "help me"}:
        return {
            "action": "clarify",
            "reason": "The request does not identify the desired report or data change.",
            "requiresApproval": False,
            "message": "What would you like me to analyze, build, or change in the report?",
        }

    if re.search(r"(?:^|[;\n])\s*page\s+1\b", text) or (
        re.search(r"\b(?:multi[- ]?page|(?:two|three|four|\d+)[- ]pages?)\b", text)
        and matches(r"\b(?:create|build|draw|make|generate|design)\b")
    ):
        return {"action": "plan_dashboard", "reason": "Plan the named report pages.", "requiresApproval": False}
    visual_output = matches(r"\b(?:visuals?|charts?|graphs?|dashboards?|reports?|plots?|cards?|slicers?|gauges?)\b")
    visual_command = matches(r"\b(?:draw|plot|visualize|create|build|generate|design|make|show|give me|add|include)\b")
    edit_command = has_report and matches(r"^\s*(?:please\s+)?(?:add|include|replace|change|edit|update|modify|remove|delete)\b")
    if visual_output and visual_command and not edit_command and not matches(r"\b(?:change|replace|rename|remove|modify|adjust)\b"):
        return {"action": "plan_dashboard", "reason": "The user requested a report or visual preview.", "requiresApproval": False}

    if matches(r"\btables?\b") and matches(r"\bmeasures?\b") and matches(r"\b(?:available|list|what|which)\b") and not matches(r"\b(?:create|build|generate|design|plan|visual|chart|dashboard|report)\b"):
        return {"action": "understand_workbook", "reason": "List the available tables and measures.", "requiresApproval": False}
    if matches(r"\b(clean|prepare|unpivot|header|headers|data type|fix type|transform)\b"):
        return {"action": "prepare_data", "reason": "The request asks for a data-preparation proposal.", "requiresApproval": True}
    if matches(r"\b(relationship|relationships|join|joined|connect tables|link tables)\b"):
        return {"action": "inspect_relationships", "reason": "The request asks about table relationships.", "requiresApproval": False}
    if matches(r"\b(understand|semantic|meaning|actual|budget mapping|scenario mapping)\b"):
        return {"action": "understand_workbook", "reason": "The request asks for business-aware workbook understanding.", "requiresApproval": False}
    if matches(r"\b(suggest|suggestions|proposal|propose|recommend|dax|calculated measure)\b"):
        return {"action": "suggest_kpis", "reason": "The request asks for KPI suggestions.", "requiresApproval": True}
    if matches(r"\bkpi\b") and not has_report:
        return {"action": "suggest_kpis", "reason": "The request asks for KPI suggestions.", "requiresApproval": True}
    if has_report and matches(r"\b(replace|change|make|use|rename|add|include|remove|delete|edit|update|modify|adjust)\b"):
        return {"action": "edit_report", "reason": "The request changes the current report.", "requiresApproval": True}
    if matches(r"\b(why|variance|below budget|above budget|worst|best|lowest|highest|top|bottom|trend|outlier|contribution)\b"):
        return {"action": "answer_data_question", "reason": "The request needs a grounded answer from the uploaded data.", "requiresApproval": False}
    if matches(r"\b(table|tables|column|columns|field|fields|schema|available|measure|measures|what|which|how many|data)\b") and not matches(r"\b(create|build|generate|dashboard|report|visual)\b"):
        return {"action": "answer_data_question", "reason": "The request asks a question about the uploaded data.", "requiresApproval": False}
    if matches(r"^\s*(?:how|what|which|where|when|who|tell me|list|describe)\b") and not matches(r"\b(create|build|generate|dashboard|report|visual)\b"):
        return {"action": "answer_data_question", "reason": "The request asks a question about the uploaded data.", "requiresApproval": False}
    if matches(r"^\s*(?:analyze|analyse|show|summarize|calculate|compute|count|find|give me)\b") and not matches(
        r"\b(create|build|generate|dashboard|report|visual|chart|graph)\b"
    ):
        return {"action": "answer_data_question", "reason": "The request asks for a result from the uploaded data.", "requiresApproval": False}
    if matches(r"\b(create|build|generate|design|plan)\b") and matches(r"\b(dashboard|report|visual|chart)\b"):
        return {"action": "plan_dashboard", "reason": "The request describes a dashboard to create.", "requiresApproval": False}
    return {
        "action": "clarify",
        "reason": "The request does not clearly identify an analysis or report action.",
        "requiresApproval": False,
        "message": "Would you like me to analyze the data, suggest KPIs, prepare a report, or change the current report?",
    }
