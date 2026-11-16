from __future__ import annotations

import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Any

CONNECTION_TTL = timedelta(minutes=15)
_connections: dict[str, tuple[Any, datetime]] = {}


def _quote_odbc_value(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


def connect_sql_server(payload: dict[str, Any]) -> Any:
    try:
        import pyodbc
    except ImportError as error:
        raise RuntimeError("SQL Server support requires pyodbc and an installed Microsoft ODBC Driver.") from error

    drivers = [driver for driver in pyodbc.drivers() if driver.startswith("ODBC Driver") and "for SQL Server" in driver]
    if not drivers:
        raise RuntimeError("No Microsoft ODBC Driver for SQL Server is installed.")
    driver = sorted(drivers, reverse=True)[0]
    authentication = payload.get("authentication", "sql")
    parts = [
        f"DRIVER={_quote_odbc_value(driver)}",
        f"SERVER={_quote_odbc_value(str(payload['server']))}",
        f"DATABASE={_quote_odbc_value(str(payload['database']))}",
        "Encrypt=yes",
        "TrustServerCertificate=no",
        "ApplicationIntent=ReadOnly",
        "Connection Timeout=5",
    ]
    if authentication == "windows":
        parts.append("Trusted_Connection=yes")
    elif authentication == "sql":
        parts.extend((f"UID={_quote_odbc_value(str(payload['username']))}", f"PWD={_quote_odbc_value(str(payload['password']))}"))
    else:
        raise ValueError("Authentication must be 'sql' or 'windows'.")

    return pyodbc.connect(";".join(parts), timeout=5, readonly=True)


def _close_expired_connections() -> None:
    now = datetime.now(timezone.utc)
    expired_ids = [connection_id for connection_id, (_, expires_at) in _connections.items() if expires_at <= now]
    for connection_id in expired_ids:
        connection, _ = _connections.pop(connection_id)
        connection.close()


def open_connection(payload: dict[str, Any]) -> str:
    _close_expired_connections()
    connection = connect_sql_server(payload)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
    except Exception:
        connection.close()
        raise

    connection_id = f"conn-{uuid.uuid4().hex}"
    _connections[connection_id] = (connection, datetime.now(timezone.utc) + CONNECTION_TTL)
    return connection_id


def discover_schema(connection_id: str) -> list[dict[str, Any]]:
    _close_expired_connections()
    entry = _connections.get(connection_id)
    if entry is None:
        raise KeyError("Connection was not found or has expired. Test the connection again.")

    connection, _ = entry
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA') "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()

    tables: dict[tuple[str, str], dict[str, Any]] = {}
    for schema_name, table_name, column_name, data_type in rows:
        key = (str(schema_name), str(table_name))
        table = tables.setdefault(key, {"schema": key[0], "name": key[1], "columns": []})
        table["columns"].append({"name": str(column_name), "dataType": str(data_type)})
    return list(tables.values())


def materialize_tables(connection_id: str, tables: list[dict[str, Any]], row_limit: int = 10000) -> dict[str, list[dict[str, Any]]]:
    """Read a bounded import snapshot using identifiers discovered from the server."""
    _close_expired_connections()
    entry = _connections.get(connection_id)
    if entry is None:
        raise KeyError("Connection was not found or has expired. Test the connection again.")
    if not 1 <= row_limit <= 10000:
        raise ValueError("rowLimit must be between 1 and 10000")
    connection, _ = entry
    results: dict[str, list[dict[str, Any]]] = {}
    for table in tables:
        schema, name = str(table.get("schema", "")), str(table.get("name", ""))
        if not schema or not name or any("\x00" in part for part in (schema, name)):
            raise ValueError("Invalid SQL table identifier")
        quoted = lambda value: "[" + value.replace("]", "]]" ) + "]"
        cursor = connection.cursor()
        try:
            cursor.execute(f"SELECT TOP ({row_limit}) * FROM {quoted(schema)}.{quoted(name)}")
            columns = [column[0] for column in cursor.description]
            def portable(value: Any) -> Any:
                # Source metadata is persisted as JSON; keep SQL numerics usable by DAX.
                return float(value) if isinstance(value, Decimal) else value
            results[f"{schema}.{name}"] = [
                {column: portable(value) for column, value in zip(columns, row)}
                for row in cursor.fetchall()
            ]
        finally:
            cursor.close()
    return results


def close_connections() -> None:
    for connection, _ in _connections.values():
        connection.close()
    _connections.clear()
