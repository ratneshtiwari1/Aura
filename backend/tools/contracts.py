"""Typed contracts for every executable agent tool."""
from __future__ import annotations

from typing import Any
from .data_tools import DATA_TOOL_CONTRACTS
from .definitions import ToolContract
from .report_tools import REPORT_TOOL_CONTRACTS
from .workspace_tools import WORKSPACE_TOOL_CONTRACTS


TOOL_CONTRACTS = {
    contract.action: contract
    for contract in (*DATA_TOOL_CONTRACTS, *REPORT_TOOL_CONTRACTS, *WORKSPACE_TOOL_CONTRACTS)
}


def public_tool_contracts() -> list[dict[str, Any]]:
    retired = {"answer_schema", "investigate_data"}
    return [contract.public() for action, contract in TOOL_CONTRACTS.items() if action not in retired]
