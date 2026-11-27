"""Separate, transactional token ledger. Stores metadata only, never prompts or rows."""
from __future__ import annotations

import csv
import io
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from backend.config import SESSION_ID, get_settings
from backend.logging_config import request_id_context, trace_id_context, agent_run_id_context, chat_session_id_context
from backend.services.identity_service import current_user_id


def _current_path() -> Path:
    override = os.getenv('TOKEN_USAGE_DB')
    return Path(override) if override else get_settings().app_data_dir / 'sessions' / SESSION_ID / 'usage' / 'token-usage.sqlite3'


def _daily_path() -> Path:
    override = os.getenv('TOKEN_USAGE_DB')
    root = Path(override).parent if override else get_settings().app_data_dir
    return root / 'daily-usage' / 'daily-usage.sqlite3'


def _initialize_daily(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS daily_usage (
                local_date TEXT PRIMARY KEY, calls INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL, calls_without_usage INTEGER NOT NULL)''')
    finally:
        db.close()


def _ledger_paths(scope: str) -> list[Path]:
    current = _current_path()
    if os.getenv('TOKEN_USAGE_DB') or scope == 'backend':
        return [current]
    root = get_settings().app_data_dir
    paths = sorted((root / 'sessions').glob('*/usage/token-usage.sqlite3'))
    legacy = root / 'usage' / 'token-usage.sqlite3'
    if legacy.is_file():
        paths.append(legacy)
    if current not in paths:
        paths.append(current)
    return paths


def _connect(path: Path | None = None):
    path = path or _current_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS token_usage (
        call_id TEXT PRIMARY KEY, recorded_at TEXT, owner_id TEXT, operation TEXT,
        model TEXT, outcome TEXT, usage_known INTEGER, input_tokens INTEGER,
        output_tokens INTEGER, total_tokens INTEGER, cached_tokens INTEGER,
        reasoning_tokens INTEGER, latency_ms REAL, request_id TEXT, trace_id TEXT,
        agent_run_id TEXT, response_id TEXT, backend_session_id TEXT, chat_session_id TEXT)''')
    columns = {row['name'] for row in db.execute('PRAGMA table_info(token_usage)')}
    for column in ('backend_session_id', 'chat_session_id'):
        if column not in columns:
            try:
                db.execute(f'ALTER TABLE token_usage ADD COLUMN {column} TEXT')
            except sqlite3.OperationalError as error:
                if 'duplicate column name' not in str(error).lower():
                    db.close()
                    raise
    return db


def _csv_safe_row(row):
    return ["'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value for value in row]


def _write_csv(db: sqlite3.Connection, target: Path, query: str) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(
            mode='w', encoding='utf-8-sig', newline='', dir=target.parent,
            prefix=target.stem + '-', suffix='.tmp', delete=False,
        ) as file:
            temporary = Path(file.name)
            rows = db.execute(query)
            writer = csv.writer(file)
            writer.writerow([column[0] for column in rows.description])
            for row in rows:
                writer.writerow(_csv_safe_row(row))
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_token_usage(*, operation, model, response=None, outcome, latency_ms):
    usage = getattr(response, 'usage', None)
    input_tokens = getattr(usage, 'prompt_tokens', None)
    output_tokens = getattr(usage, 'completion_tokens', None)
    total_tokens = getattr(usage, 'total_tokens', None)
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    # Cached/reasoning counts are subsets of input/output, never added to total.
    cached = getattr(getattr(usage, 'prompt_tokens_details', None), 'cached_tokens', None)
    reasoning = getattr(getattr(usage, 'completion_tokens_details', None), 'reasoning_tokens', None)
    path = _current_path()
    daily_path = _daily_path()
    _initialize_daily(daily_path)
    db = _connect(path)
    try:
        db.execute('ATTACH DATABASE ? AS daily', (str(daily_path),))
        recorded_at = datetime.now(timezone.utc)
        with db:
            db.execute('''INSERT INTO token_usage (
                call_id, recorded_at, owner_id, operation, model, outcome, usage_known,
                input_tokens, output_tokens, total_tokens, cached_tokens, reasoning_tokens,
                latency_ms, request_id, trace_id, agent_run_id, response_id, backend_session_id, chat_session_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                uuid4().hex, recorded_at.isoformat(), current_user_id(), operation,
                model, outcome, int(total_tokens is not None), input_tokens, output_tokens,
                total_tokens, cached, reasoning, latency_ms, request_id_context.get(),
                trace_id_context.get(), agent_run_id_context.get(), getattr(response, 'id', None),
                SESSION_ID, chat_session_id_context.get()))
            db.execute('''INSERT INTO daily.daily_usage (
                local_date, calls, input_tokens, output_tokens, total_tokens, calls_without_usage
                ) VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(local_date) DO UPDATE SET
                calls = calls + excluded.calls,
                input_tokens = input_tokens + excluded.input_tokens,
                output_tokens = output_tokens + excluded.output_tokens,
                total_tokens = total_tokens + excluded.total_tokens,
                calls_without_usage = calls_without_usage + excluded.calls_without_usage''', (
                recorded_at.astimezone().date().isoformat(),
                input_tokens if input_tokens is not None else 0,
                output_tokens if output_tokens is not None else 0,
                total_tokens if total_tokens is not None else 0,
                int(total_tokens is None)))
        # Lock both ledgers while publishing snapshots to prevent stale concurrent writes.
        with db:
            db.execute('BEGIN IMMEDIATE')
            _write_csv(db, daily_path.with_suffix('.csv'),
                       'SELECT * FROM daily.daily_usage ORDER BY local_date')
            _write_csv(db, path.with_suffix('.csv'),
                       'SELECT * FROM token_usage ORDER BY recorded_at, call_id')
    finally:
        db.close()


def _scope_filter(scope: str) -> tuple[str, tuple[str, ...]]:
    owner = current_user_id()
    if scope == 'all':
        return 'owner_id=?', (owner,)
    if scope == 'backend':
        return 'owner_id=? AND backend_session_id=?', (owner, SESSION_ID)
    if scope == 'chat':
        chat_id = chat_session_id_context.get()
        if not chat_id:
            raise ValueError('A chat session ID is required for chat usage.')
        return 'owner_id=? AND chat_session_id=?', (owner, chat_id)
    raise ValueError('Invalid token usage scope.')


def usage_summary(scope: str = 'all'):
    where, params = _scope_filter(scope)
    totals = dict.fromkeys(('calls', 'input_tokens', 'output_tokens', 'total_tokens', 'calls_without_usage'), 0)
    groups: dict[tuple[str, str], dict] = {}
    for path in _ledger_paths(scope):
        db = _connect(path)
        try:
            row = dict(db.execute('''SELECT COUNT(*) AS calls,
            COALESCE(SUM(input_tokens),0) AS input_tokens,
            COALESCE(SUM(output_tokens),0) AS output_tokens,
            COALESCE(SUM(total_tokens),0) AS total_tokens,
            COALESCE(SUM(1-usage_known),0) AS calls_without_usage
            FROM token_usage WHERE ''' + where, params).fetchone())
            for key, value in row.items():
                totals[key] += value
            for group in db.execute('''SELECT operation, model, COUNT(*) AS calls,
            COALESCE(SUM(total_tokens),0) AS total_tokens FROM token_usage
            WHERE ''' + where + ''' GROUP BY operation,model ORDER BY operation,model''', params):
                key = (group['operation'], group['model'])
                entry = groups.setdefault(key, {'operation': key[0], 'model': key[1], 'calls': 0, 'total_tokens': 0})
                entry['calls'] += group['calls']
                entry['total_tokens'] += group['total_tokens']
        finally:
            db.close()
    return {**totals, 'by_operation': [groups[key] for key in sorted(groups)],
            'notice': 'Provider-reported usage only. Calls without usage are unknown, not zero. '
                      'SDK-internal retries without returned usage are not measurable here.'}


def usage_csv(scope: str = 'all'):
    where, params = _scope_filter(scope)
    output = io.StringIO()
    writer = csv.writer(output)
    header_written = False
    for path in _ledger_paths(scope):
        db = _connect(path)
        try:
            rows = db.execute('SELECT * FROM token_usage WHERE ' + where + ' ORDER BY recorded_at', params)
            if not header_written:
                writer.writerow([column[0] for column in rows.description])
                header_written = True
            for row in rows:
                writer.writerow(_csv_safe_row(row))
        finally:
            db.close()
    return output.getvalue()
