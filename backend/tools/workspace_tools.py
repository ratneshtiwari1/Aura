"""Tools that inspect workbook context or manage conversation flow."""
from .definitions import ToolContract

WORKSPACE_TOOL_CONTRACTS = (
    ToolContract("understand_workbook", "SourceContext", "WorkbookMapping", "read:schema", 10),
    ToolContract("suggest_kpis", "SourceContext", "KpiProposals", "read:schema", 10),
    ToolContract("inspect_relationships", "SourceContext", "RelationshipReview", "read:schema", 10),
    ToolContract("prepare_data", "PreparationGoal", "PreparationProposal", "read:schema", 15),
    ToolContract("respond", "ConversationContext", "AssistantMessage", "read:context", 5),
    ToolContract("clarify", "ConversationContext", "Clarification", "read:context", 5),
)
