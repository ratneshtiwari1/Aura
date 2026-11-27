from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from automation.ai.azure_openai_client import AIServiceError, AzureOpenAIClient
from automation.ai.prompts import (
    AGENT_ROUTING_PROMPT,
    DATA_QUESTION_PLANNING_PROMPT,
    DASHBOARD_PLANNING_PROMPT,
    WORKBOOK_UNDERSTANDING_PROMPT,
)
from backend.config import Settings

logger = logging.getLogger("autobi.ai")


class AIProvider(Protocol):
    model: str

    async def test_connection(self) -> None: ...

    async def analyze_workbook(self, profile: dict[str, Any]) -> dict[str, Any]: ...

    async def plan_dashboard(
        self,
        request: str,
        profile: dict[str, Any],
        understanding: dict[str, Any],
        metadata: dict[str, Any],
        visual_count: int | None,
        repair_context: dict[str, Any] | None = None,
        visual_count_mode: str = "analytical",
    ) -> dict[str, Any]: ...

    async def route_agent(self, request: str, context: dict[str, Any]) -> dict[str, Any]: ...

    async def plan_data_question(
        self, question: str, schema: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]: ...


class AzureOpenAIProvider:
    def __init__(self, settings: Settings) -> None:
        self.client = AzureOpenAIClient(settings)
        self.model = settings.azure_openai_deployment or ""

    async def test_connection(self) -> None:
        await self.client.test_connection()

    async def analyze_workbook(self, profile: dict[str, Any]) -> dict[str, Any]:
        return await self.client.generate_json(
            "workbook_understanding", WORKBOOK_UNDERSTANDING_PROMPT, {"workbook_profile": profile}
        )

    async def plan_dashboard(
        self,
        request: str,
        profile: dict[str, Any],
        understanding: dict[str, Any],
        metadata: dict[str, Any],
        visual_count: int | None,
        repair_context: dict[str, Any] | None = None,
        visual_count_mode: str = "analytical",
    ) -> dict[str, Any]:
        from automation.visual_registry import visual_capabilities
        return await self.client.generate_json(
            "dashboard_planning",
            DASHBOARD_PLANNING_PROMPT,
            {
                "user_request": request,
                "visual_count": visual_count,
                "visual_count_mode": visual_count_mode,
                "visual_capabilities": visual_capabilities(),
                "workbook_profile": profile,
                "workbook_understanding": understanding,
                "available_report_metadata": {
                    "preferred_table": metadata.get("preferredTable"),
                    "relationships": metadata.get("relationships", []),
                    "tables": {
                        name: {
                            "columns": table["columns"],
                            "measures": table["measures"],
                        }
                        for name, table in metadata["tables"].items()
                    }
                },
                "repair_context": repair_context,
            },
        )

    async def route_agent(self, request: str, context: dict[str, Any]) -> dict[str, Any]:
        return await self.client.generate_json("agent_routing", AGENT_ROUTING_PROMPT, {"user_request": request, "context": context})

    async def plan_data_question(
        self, question: str, schema: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.client.generate_json(
            "data_question_planning",
            DATA_QUESTION_PLANNING_PROMPT,
            {"user_question": question, "available_schema": schema, "context": context},
        )


class MockProvider:
    model = "mock"

    async def test_connection(self) -> None:
        return None

    async def analyze_workbook(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "tables": [
                {
                    "name": table["name"],
                    "columns": [
                        {
                            "name": column["name"],
                            "semantic_role": column["role_candidate"],
                            "description": "",
                        }
                        for column in table["columns"]
                    ],
                }
                for sheet in profile["sheets"]
                for table in sheet["tables"]
            ]
        }

    async def plan_dashboard(
        self,
        request: str,
        profile: dict[str, Any],
        understanding: dict[str, Any],
        metadata: dict[str, Any],
        visual_count: int | None,
        repair_context: dict[str, Any] | None = None,
        visual_count_mode: str = "analytical",
    ) -> dict[str, Any]:
        from automation.report_automation import _split_visual_instructions, create_report_spec

        if repair_context and repair_context.get("multi_page"):
            candidates = [
                (table_name, measure)
                for table_name, table in metadata["tables"].items()
                for measure in table.get("measures", [])
            ]
            if not candidates:
                return {"status": "needs_clarification", "question": "Which available metric should this dashboard use?", "options": []}
            distinct = list(dict.fromkeys(name for name, _ in candidates))
            count = repair_context.get("requested_page_count") or min(12, max(2, len(distinct)))
            candidates = [next(item for item in candidates if item[0] == name) for name in distinct] + candidates
            pages = []
            for index in range(count):
                table_name, measure = candidates[index % len(candidates)]
                scoped = {"tables": {table_name: metadata["tables"][table_name]}, "preferredTable": table_name}
                spec = create_report_spec(f"Create a card for {measure}", scoped)
                pages.append({**spec, "page": f"{table_name} Overview {index + 1}"})
            return {"status": "ready", **pages[0], "additionalPages": pages[1:]}

        if metadata.get("preferredTable"):
            try:
                return {"status": "ready", **create_report_spec(request, metadata)}
            except ValueError as exc:
                # Match the transport contract in local integration mode too.
                return {"status": "needs_clarification", "question": str(exc), "options": []}

        instructions = _split_visual_instructions(request)
        visuals = []
        for instruction in instructions:
            matches = []
            for table_name, table in metadata["tables"].items():
                try:
                    spec = create_report_spec(instruction, {"tables": {table_name: table}})
                except ValueError:
                    continue
                for visual in spec["visuals"]:
                    measure_fields = ([visual["value"]] if visual.get("value") else []) + visual.get("values", [])
                    measure_words = set(re.findall(r"[a-z0-9]+", instruction.casefold()))
                    measure_score = max(
                        (
                            len(measure_words & set(re.findall(r"[a-z0-9]+", field["field"].casefold())))
                            / max(len(set(re.findall(r"[a-z0-9]+", field["field"].casefold()))), 1)
                            for field in measure_fields
                        ),
                        default=0.0,
                    )
                    matches.append((measure_score, visual))
            if matches:
                best_score = max(score for score, _ in matches)
                best_matches = [visual for score, visual in matches if score == best_score]
            else:
                best_matches = []
            if len(best_matches) > 1:
                return {
                    "status": "needs_clarification",
                    "question": "Which table should this visual use?",
                    "options": sorted({
                        field["table"]
                        for visual in best_matches
                        for field in visual.values()
                        if isinstance(field, dict) and "table" in field
                    }),
                }
            visuals.extend(best_matches)
        if not visuals:
            return {"status": "needs_clarification",
                    "question": "Which available metric should this dashboard use?",
                    "options": [f"Use {name}[{measure}]" for name, table in metadata["tables"].items()
                                for measure in table.get("measures", [])]}
        page_match = re.search(
            r"\b(?:create|build|make|generate)\s+(?:a\s+|an\s+|the\s+)?([a-z0-9 ]+?)\s+dashboard\b",
            request,
            flags=re.IGNORECASE,
        )
        page = f"{page_match.group(1).strip().title()} Dashboard" if page_match else "Generated Report"
        return {"status": "ready", "page": page, "visuals": visuals}

    async def route_agent(self, request: str, context: dict[str, Any]) -> dict[str, Any]:
        from backend.agents.router import route_agent_request
        decision = route_agent_request(request, bool(context.get("has_report")))
        result = {
            "steps": [{"action": decision["action"], "reason": decision["reason"]}],
            "requires_approval": decision["requiresApproval"],
        }
        if isinstance(decision.get("message"), str):
            result["message"] = decision["message"]
        return result

    async def plan_data_question(
        self, question: str, schema: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        tables = schema.get("tables", {})
        selected = context.get("selected_table")
        if not isinstance(selected, str) or selected not in tables:
            selected = next(iter(tables)) if len(tables) == 1 else None
        if selected is None:
            return {
                "status": "needs_clarification",
                "question": "Which source table should I use for this question?",
                "options": list(tables),
            }
        table = tables[selected]
        measures = table.get("measures", [])
        measure = next((name for name in measures if name.casefold() in question.casefold()), None)
        if measure is None and len(measures) == 1:
            measure = measures[0]
        lowered = question.casefold()
        if re.search(r"\b(?:distinct|unique)\b", lowered):
            fields = [name for name in table.get("columns", []) if name.casefold() in lowered]
            if len(fields) == 1:
                return {"status": "ready", "intent": "count_distinct", "table": selected,
                        "field": fields[0], "measure": None, "normalized_question": question}
        ranking = bool(re.search(r"\b(highest|largest|most|top|best|lowest|smallest|least|bottom|worst)\b", lowered))
        trend = bool(re.search(r"\b(trend|over time|timeline)\b", lowered))
        intent = "ranking" if ranking else "trend" if trend else "aggregate"
        normalized_question = question
        if measure and not ranking and not trend and not re.search(
            r"\b(total|sum|average|avg|mean|how much)\b", lowered
        ):
            normalized_question = f"What is the total {measure} {question}"
        date_columns = [
            name for name in table.get("columns", [])
            if str(table.get("column_types", {}).get(name, "")).casefold() in {"date", "datetime"}
        ]
        time_field = next(
            (name for name in date_columns if name.casefold() in question.casefold()),
            date_columns[0] if len(date_columns) == 1 and re.search(r"\b(q[1-4]|quarter)\b", lowered) else None,
        )
        return {
            "status": "ready",
            "intent": intent,
            "table": selected,
            "measure": measure,
            "time_field": time_field,
            "normalized_question": normalized_question,
        }


def get_ai_provider(settings: Settings) -> AIProvider:
    if not settings.use_ai:
        raise AIServiceError("AI mode is disabled.")
    if settings.ai_provider == "mock":
        return MockProvider()
    return AzureOpenAIProvider(settings)


def validate_workbook_understanding(
    understanding: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    known: dict[str, set[str]] = {
        table["name"]: {column["name"] for column in table["columns"]}
        for sheet in profile["sheets"]
        for table in sheet["tables"]
    }
    tables = understanding.get("tables")
    if not isinstance(tables, list):
        raise AIServiceError("AI workbook analysis returned an invalid schema.")
    observed: dict[str, set[str]] = {}
    for table in tables:
        if not isinstance(table, dict) or table.get("name") not in known:
            raise AIServiceError("AI workbook analysis returned an invalid table.")
        if table["name"] in observed:
            raise AIServiceError("AI workbook analysis returned a duplicate table.")
        columns = table.get("columns")
        if not isinstance(columns, list):
            raise AIServiceError("AI workbook analysis returned invalid columns.")
        observed[table["name"]] = set()
        for column in columns:
            if not isinstance(column, dict) or column.get("name") not in known[table["name"]]:
                raise AIServiceError("AI workbook analysis referenced an unknown column.")
            if column["name"] in observed[table["name"]]:
                raise AIServiceError("AI workbook analysis returned a duplicate column.")
            if column.get("semantic_role") not in {
                "measure",
                "dimension",
                "time",
                "identifier",
                "geographic",
                "category",
                "calculated_measure",
                "unknown",
            }:
                raise AIServiceError("AI workbook analysis returned an unsupported semantic role.")
            description = column.get("description", "")
            if not isinstance(description, str) or len(description) > 300:
                raise AIServiceError("AI workbook analysis returned an invalid column explanation.")
            observed[table["name"]].add(column["name"])
    if observed != known:
        raise AIServiceError("AI workbook analysis omitted one or more workbook tables or columns.")
    return understanding
