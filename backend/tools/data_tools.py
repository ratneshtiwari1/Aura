"""Read-only tools that answer grounded data questions."""
from .definitions import ToolContract

DATA_TOOL_CONTRACTS = (
    ToolContract("answer_data_question", "GroundedQuestion", "AnalysisAnswer", "read:data", 20),
    # Execution support for runs persisted before action unification.
    ToolContract("answer_schema", "GroundedQuestion", "AnalysisAnswer", "read:data", 15),
    ToolContract("investigate_data", "GroundedQuestion", "AnalysisAnswer", "read:data", 20),
)
