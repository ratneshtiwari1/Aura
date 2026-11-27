# Agent layer

- `router.py`: routes requests to an allow-listed action.
- `orchestrator.py`: validates and audits plans.
- `graph_workflow.py`: composes execution nodes.
- `prompts/`: one prompt file per conversational role.
- `tools.py`: execution adapters; stable contracts live in `backend/tools/`.

Business logic is isolated in `backend/services/`; API modules translate HTTP only.
