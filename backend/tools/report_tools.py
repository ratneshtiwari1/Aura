"""Tools that plan or revise report specifications."""
from .definitions import ToolContract

REPORT_TOOL_CONTRACTS = (
    ToolContract("plan_dashboard", "DashboardGoal", "ReportPlan", "read:schema", 30),
    ToolContract("edit_report", "ReportEditGoal", "ReportPlan", "change:model", 30, True),
)
