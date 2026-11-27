"""Shared types for the tool registry."""
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ToolContract:
    action: str
    input_type: str
    output_type: str
    permission: str
    timeout_seconds: int
    mutates: bool = False

    def public(self) -> dict[str, Any]:
        return asdict(self)
