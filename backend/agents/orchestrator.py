from __future__ import annotations

import logging
import re
import time
from typing import Any

from automation.ai.ai_service import get_ai_provider
from backend.agents.contracts import validate_agent_plan
from backend.config import get_settings
from backend.services.ai_audit_service import write_ai_audit_event

logger = logging.getLogger("autobi.agent")

_PREPARATION_INTENT = re.compile(
    r"\b(?:clean|prepare|transform|unpivot|normalize|standardize)\b.{0,80}"
    r"\b(?:data|workbook|range|header|type|column|row|table)\b"
    r"|\b(?:review|confirm)\b.{0,80}\b(?:ranges?|headers?|data types?|transformations?)\b",
    re.IGNORECASE,
)


class AgentOrchestrator:
    """Coordinates bounded routing only; execution remains with API tool handlers."""

    @staticmethod
    def _schema_context(metadata: dict[str, Any]) -> dict[str, Any]:
        """Return schema-only context; never send workbook rows to the router."""
        model = metadata.get("metadata", metadata)
        tables = model.get("tables", {}) if isinstance(model, dict) else {}
        return {
            "tables": [
                {
                    "name": name,
                    "columns": list(table.get("columns", [])),
                    "measures": list(table.get("measures", [])),
                }
                for name, table in tables.items()
                if isinstance(table, dict)
            ],
            "relationships": model.get("relationships", []) if isinstance(model, dict) else [],
        }

    async def decide(
        self,
        source_id: str,
        request: str,
        has_report: bool,
        selected_table: object = None,
        metadata: dict[str, Any] | None = None,
        conversation: list[dict[str, str]] | None = None,
        clarification: str | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        started = time.perf_counter()
        logger.info("Agent routing started", extra={
            "event": "agent.step.started", "operation": "agent_routing",
            "step": "route_request", "attempt": 1, "source_id": source_id,
        })
        try:
            provider = get_ai_provider(settings)
            raw_plan = await provider.route_agent(request, {
                "has_report": has_report,
                "selected_table": selected_table,
                "available_schema": self._schema_context(metadata or {}),
                "conversation": (conversation or [])[-10:],
                "clarification_answer": clarification,
            })
            decision = validate_agent_plan(raw_plan, has_report)
            actions = {step["action"] for step in decision["plannedSteps"]}
            if (
                "plan_dashboard" in actions
                and "prepare_data" in actions
                and not _PREPARATION_INTENT.search(request)
            ):
                decision["plannedSteps"] = [
                    step for step in decision["plannedSteps"] if step["action"] != "prepare_data"
                ]
                decision["action"] = decision["plannedSteps"][0]["action"]
                decision["reason"] = decision["plannedSteps"][0]["reason"]
            decision["planner"] = provider.model
            planned_actions = {step["action"] for step in decision["plannedSteps"]}
            if "plan_dashboard" in planned_actions and "edit_report" not in planned_actions:
                # Dashboard planning is read-only; PBIP generation has its own
                # explicit approval after the user reviews the preview.
                decision["requiresApproval"] = False

            decision["fallbackUsed"] = False
            write_ai_audit_event(
                source_id=source_id, operation="agent_routing", provider=settings.ai_provider, model=provider.model,
                outcome="succeeded", validation_result="passed", decisions=decision,
                context={"fallback_used": False},
            )
            logger.info("Agent routing completed", extra={
                "event": "agent.step.completed", "operation": "agent_routing",
                "step": "route_request", "attempt": 1, "source_id": source_id,
                "success": True, "outcome": "succeeded",
                "fallback_used": False, "validation_result": "passed",
                "model_name": decision.get("planner"),
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            })
            return decision
        except Exception as error:
            logger.exception("Agent routing failed", extra={
                "event": "agent.step.failed", "operation": "agent_routing",
                "step": "route_request", "attempt": 1, "source_id": source_id,
                "success": False, "outcome": "failed", "fallback_used": False,
                "failure_type": type(error).__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            })
            raise
