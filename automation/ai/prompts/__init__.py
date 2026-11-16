"""Versioned, role-specific AI prompts.

Each prompt lives beside the role that consumes it so changes are traceable and
do not accidentally alter another agent's instructions.
"""
from .dashboard_planner import DASHBOARD_PLANNING_PROMPT
from .data_analyst import DATA_QUESTION_PLANNING_PROMPT
from .router import AGENT_ROUTING_PROMPT
from .workbook_analyst import WORKBOOK_UNDERSTANDING_PROMPT

PROMPT_VERSION = "aura-v9"

__all__ = [
    "AGENT_ROUTING_PROMPT", "DASHBOARD_PLANNING_PROMPT",
    "DATA_QUESTION_PLANNING_PROMPT", "PROMPT_VERSION",
    "WORKBOOK_UNDERSTANDING_PROMPT",
]
