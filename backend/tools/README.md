# Tool layer

Tool contracts are separated by capability:

- `data_tools.py` — schema-bound data answers.
- `report_tools.py` — dashboard planning and report edits.
- `workspace_tools.py` — workbook inspection and conversation actions.
- `contracts.py` — the unified registry consumed by the API, LangGraph, and
  run service.

Executors live under `backend/agents/tools.py` because they coordinate agent
context with services; business logic remains in `backend/services/`.

See the [agent architecture](../../docs/AGENTIC_ARCHITECTURE.md) for the
complete workflow and tool assignments.
