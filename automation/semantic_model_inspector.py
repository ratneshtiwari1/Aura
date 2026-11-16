import argparse
import json
import re
from pathlib import Path
from typing import Any


_IDENTIFIER = r"(?:'(?:[^']|'')+'|[^\s=]+)"
_TABLE_DECLARATION = re.compile(rf"^table\s+({_IDENTIFIER})\s*$")
_FIELD_DECLARATION = re.compile(rf"^\t(column|measure)\s+({_IDENTIFIER})(?:\s*=.*)?$")
_RELATIONSHIP_DECLARATION = re.compile(rf"^relationship\s+({_IDENTIFIER})\s*$")
_RELATIONSHIP_COLUMN = re.compile(rf"^\t(fromColumn|toColumn):\s*({_IDENTIFIER})\.({_IDENTIFIER})\s*$")


def _unquote_identifier(identifier: str) -> str:
    if identifier.startswith("'") and identifier.endswith("'"):
        return identifier[1:-1].replace("''", "'")
    return identifier


def _definition_directory(model_path: str | Path) -> Path:
    path = Path(model_path).expanduser().resolve()
    if path.name == "tables" and path.is_dir():
        return path.parent
    if path.name == "definition" and (path / "tables").is_dir():
        return path
    definition = path / "definition"
    if definition.is_dir() and (definition / "tables").is_dir():
        return definition
    raise FileNotFoundError(f"TMDL tables directory not found under semantic model: {path}")


def inspect_semantic_model(model_path: str | Path) -> dict[str, Any]:
    definition_dir = _definition_directory(model_path)
    table_files = sorted((definition_dir / "tables").rglob("*.tmdl"))
    if not table_files:
        raise FileNotFoundError(f"No TMDL table files found in {definition_dir / 'tables'}")

    tables: dict[str, dict[str, list[str]]] = {}
    for table_file in table_files:
        current_table: str | None = None
        for line in table_file.read_text(encoding="utf-8-sig").splitlines():
            table_match = _TABLE_DECLARATION.match(line)
            if table_match:
                current_table = _unquote_identifier(table_match.group(1))
                if current_table in tables:
                    raise ValueError(f"Duplicate TMDL table declaration: {current_table}")
                tables[current_table] = {"columns": [], "measures": []}
                continue

            if current_table is None:
                continue
            field_match = _FIELD_DECLARATION.match(line)
            if field_match:
                field_type, identifier = field_match.groups()
                collection = "columns" if field_type == "column" else "measures"
                tables[current_table][collection].append(_unquote_identifier(identifier))

    if not tables:
        raise ValueError(f"No TMDL table declarations found in {definition_dir / 'tables'}")

    relationships = []
    model_file = definition_dir / "model.tmdl"
    if model_file.is_file():
        current_relationship: dict[str, str] | None = None
        for line in model_file.read_text(encoding="utf-8-sig").splitlines():
            declaration = _RELATIONSHIP_DECLARATION.match(line)
            if declaration:
                current_relationship = {"name": _unquote_identifier(declaration.group(1))}
                relationships.append(current_relationship)
                continue
            if current_relationship is None:
                continue
            column_reference = _RELATIONSHIP_COLUMN.match(line)
            if column_reference:
                side, table_name, column_name = column_reference.groups()
                current_relationship[side] = f"{_unquote_identifier(table_name)}.{_unquote_identifier(column_name)}"

    return {"tables": tables, "relationships": relationships}


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect tables, columns, and measures in a TMDL model")
    parser.add_argument("model_path", help="Semantic model folder or its definition/tables folder")
    arguments = parser.parse_args()
    print(json.dumps(inspect_semantic_model(arguments.model_path), indent=2))


if __name__ == "__main__":
    main()