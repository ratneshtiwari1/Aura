from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool
from backend.agents.tools import execute_tool
from backend.tools import TOOL_CONTRACTS


class ToolInput(BaseModel):
    source_id: str = Field(min_length=1, max_length=256)
    goal: str = Field(min_length=1, max_length=12000)
    context: dict = Field(default_factory=dict)


def _adapter(action):
    async def invoke(source_id: str, goal: str, context: dict):
        return await execute_tool(action, source_id, goal, context)
    return StructuredTool.from_function(
        coroutine=invoke, name=action, args_schema=ToolInput,
        description=f'AURA {action}; {TOOL_CONTRACTS[action].permission}. Only validated source fields are allowed.')


TOOLS = {action:_adapter(action) for action in TOOL_CONTRACTS}
