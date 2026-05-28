"""List captured runs across hooks and SDK sources.

Four invariants pinned for future contributors:

1. The SQLite index at ``$AGENTLOG_HOME/index.sqlite3`` is a *cache*, never the
   source of truth — ``runs/<id>/{state,events,cost}.json`` remain canonical.
2. ``ls`` is read-only with respect to ``runs/``. The only file it ever writes
   is the index.
3. Failure contract is **fail-loud**: this is a user CLI, not a hook hot-path.
   User errors exit 2; runtime I/O failures exit 1; success and empty trees
   exit 0.
4. Schema versioning: on mismatch, drop the ``runs`` table and the
   ``schema_version`` row, then re-create. Preserves other tables a future
   feature might add.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any

from agentlog._constants import (
    DEFAULT_DATA_ROOT_NAME,
    INDEX_FILE_NAME,
    INDEX_SCHEMA_VERSION,
    RUNS_DIR_NAME,
    SELF_LOG_NAME,
)

# ---------------------------------------------------------------------------
# Helpers (duplicated from capture.py — inverted failure contract)
# ---------------------------------------------------------------------------


def _data_root() -> Path:
    env = os.environ.get("AGENTLOG_HOME")
    if env:
        return Path(env)
    return Path.home() / DEFAULT_DATA_ROOT_NAME


def _log_self(root: Path, message: str) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        log_path = root / SELF_LOG_NAME
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {message}\n")
    except Exception:  # noqa: BLE001
        pass


def _isoformat(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_CREATE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS runs (
    run_id               TEXT PRIMARY KEY,
    source               TEXT,
    session_id           TEXT,
    parent_session_id    TEXT,
    started_at           TEXT,
    ended_at             TEXT,
    cwd                  TEXT,
    model                TEXT,
    event_count          INTEGER DEFAULT 0,
    total_tokens         INTEGER DEFAULT 0,
    state_mtime          REAL DEFAULT 0.0,
    cost_mtime           REAL DEFAULT 0.0,
    indexed_at           TEXT
)
"""

_CREATE_SCHEMA_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
)
"""

_CREATE_IDX_SOURCE = "CREATE INDEX IF NOT EXISTS idx_runs_source ON runs (source)"
_CREATE_IDX_STARTED = "CREATE INDEX IF NOT EXISTS idx_runs_started ON runs (started_at)"


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    """Create the `schema_version` table. This table's shape is invariant
    across INDEX_SCHEMA_VERSION bumps (it's the bootstrap), so creating it
    first is always safe and lets _check_schema_version run before anything
    else touches the (possibly-incompatible) `runs` table."""
    conn.execute(_CREATE_SCHEMA_VERSION_TABLE)
    conn.commit()


def _check_schema_version(conn: sqlite3.Connection) -> None:
    """Drop the `runs` table (and clear the recorded version) if the index
    file was written by an incompatible INDEX_SCHEMA_VERSION. Safe to run
    on a brand-new DB: an absent row is treated like a mismatch and triggers
    the (no-op) drop. Requires _ensure_schema_version_table to have run."""
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    if row is None or int(row[0]) != INDEX_SCHEMA_VERSION:
        conn.execute("DROP TABLE IF EXISTS runs")
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (INDEX_SCHEMA_VERSION,))
        conn.commit()


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create the `runs` table and its indexes. Assumes any incompatible
    prior version has already been cleared by _check_schema_version."""
    conn.execute(_CREATE_RUNS_TABLE)
    conn.execute(_CREATE_IDX_SOURCE)
    conn.execute(_CREATE_IDX_STARTED)
    conn.commit()


def _open_index(index_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(index_path))
    conn.row_factory = sqlite3.Row
    # Order matters: schema_version table is the bootstrap (invariant shape);
    # version check decides whether to drop the (possibly-incompatible) runs
    # table; then init creates runs + indexes against the current schema.
    _ensure_schema_version_table(conn)
    _check_schema_version(conn)
    _init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Per-run indexing
# ---------------------------------------------------------------------------


def _read_json_safe(path: Path, root: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as f:
            data: Any = json.load(f)
        if isinstance(data, dict):
            return data
        return None
    except (json.JSONDecodeError, OSError) as exc:
        _log_self(root, f"ls: failed to read {path}: {exc}")
        return None


def _count_events_jsonl(events_path: Path) -> int | None:
    """Count non-blank lines in events.jsonl. Returns None on read failure.

    Used for issue #2: state.json::event_count is only finalised when the
    SessionEnd hook fires, so during a live session it lags. For live runs
    (ended_at IS NULL) the displayed `events` column should reflect what's
    actually on disk; we get that by counting lines directly. Live runs are
    few; this scan is cheap.
    """
    try:
        with events_path.open("rb") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return None


def _index_run(
    conn: sqlite3.Connection,
    run_dir: Path,
    state_path: Path,
    cost_path: Path,
    state_mtime: float,
    cost_mtime: float,
    root: Path,
) -> None:
    run_id = run_dir.name
    state = _read_json_safe(state_path, root)
    if state is None:
        print(f"warning: skipped {run_id} (malformed state.json)", file=sys.stderr)
        return

    cost = _read_json_safe(cost_path, root) or {}

    totals = cost.get("totals") or {}
    total_tokens = (
        int(totals.get("input_tokens") or 0)
        + int(totals.get("output_tokens") or 0)
        + int(totals.get("cache_read_tokens") or 0)
        + int(totals.get("cache_creation_tokens") or 0)
    )

    # event_count: prefer the finalised value from state.json (set at
    # SessionEnd). For live runs (no ended_at), state's count is 0/stale, so
    # count events.jsonl lines directly. Issue #2.
    event_count = int(state.get("event_count") or 0)
    if state.get("ended_at") is None:
        live_count = _count_events_jsonl(run_dir / "events.jsonl")
        if live_count is not None:
            event_count = live_count

    conn.execute(
        """
        INSERT OR REPLACE INTO runs (
            run_id, source, session_id, parent_session_id,
            started_at, ended_at, cwd, model,
            event_count, total_tokens, state_mtime, cost_mtime, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            state.get("source"),
            state.get("session_id"),
            state.get("parent_session_id"),
            state.get("started_at"),
            state.get("ended_at"),
            state.get("cwd"),
            state.get("model"),
            event_count,
            total_tokens,
            state_mtime,
            cost_mtime,
            _isoformat(datetime.now(UTC)),
        ),
    )


# ---------------------------------------------------------------------------
# Refresh-on-stale walker
# ---------------------------------------------------------------------------


def _refresh_index(conn: sqlite3.Connection, runs_root: Path, root: Path) -> None:
    seen_ids: set[str] = set()

    for entry in sorted(runs_root.iterdir()):
        if not entry.is_dir():
            continue
        state_path = entry / "state.json"
        if not state_path.exists():
            continue

        run_id = entry.name
        seen_ids.add(run_id)

        cost_path = entry / "cost.json"
        try:
            state_mtime = state_path.stat().st_mtime
        except OSError:
            continue
        cost_mtime = cost_path.stat().st_mtime if cost_path.exists() else 0.0

        row = conn.execute(
            "SELECT state_mtime, cost_mtime, ended_at FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

        if row is not None:
            stored_state_mtime: float = float(row["state_mtime"])
            stored_cost_mtime: float = float(row["cost_mtime"])
            # Skip-on-mtime-match is safe for FINALISED runs only. Live runs
            # (ended_at NULL) keep appending to events.jsonl without state.json
            # being touched, so the cached row's event_count would lag forever.
            # Re-index live runs every time. Issue #2.
            is_finalised = row["ended_at"] is not None
            if (
                stored_state_mtime == state_mtime
                and stored_cost_mtime == cost_mtime
                and is_finalised
            ):
                continue

        _index_run(conn, entry, state_path, cost_path, state_mtime, cost_mtime, root)

    # Purge rows for run dirs that no longer exist.
    placeholders = ",".join("?" for _ in seen_ids)
    if seen_ids:
        conn.execute(
            f"DELETE FROM runs WHERE run_id NOT IN ({placeholders})",
            list(seen_ids),
        )
    else:
        conn.execute("DELETE FROM runs")

    conn.commit()


# ---------------------------------------------------------------------------
# Duration parser
# ---------------------------------------------------------------------------

_DURATION_RE_UNITS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "w": 604800,
}


def _parse_duration(text: str) -> timedelta:
    m = re.match(r"^(\d+)([smhdw])$", text.strip(), re.IGNORECASE)
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid duration {text!r}: expected a positive integer followed by"
            " s/m/h/d/w (e.g. 30m, 24h, 7d)"
        )
    magnitude = int(m.group(1))
    unit = m.group(2).lower()
    if magnitude <= 0:
        raise argparse.ArgumentTypeError(
            f"invalid duration {text!r}: magnitude must be positive"
        )
    return timedelta(seconds=magnitude * _DURATION_RE_UNITS[unit])


# ---------------------------------------------------------------------------
# Query layer
# ---------------------------------------------------------------------------

SORT_COLUMN_MAP: dict[str, str] = {
    "started": "started_at",
    "ended": "ended_at",
    "duration": "(julianday(ended_at) - julianday(started_at))",
    "events": "event_count",
    "tokens": "total_tokens",
    "cost": "total_tokens",  # v0.1 alias; remap to dollar-cost column when item #5 lands
}


def _query_runs(
    conn: sqlite3.Connection,
    *,
    source: str,
    since: timedelta | None,
    sort_key: str,
    reverse: bool,
    limit: int,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []

    if source != "all":
        clauses.append("source = ?")
        params.append(source)

    if since is not None:
        cutoff = datetime.now(UTC) - since
        clauses.append("started_at >= ?")
        params.append(_isoformat(cutoff))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_col = SORT_COLUMN_MAP[sort_key]
    direction = "ASC" if reverse else "DESC"
    limit_clause = f"LIMIT {int(limit)}" if limit > 0 else ""

    sql = f"SELECT * FROM runs {where} ORDER BY {order_col} {direction} {limit_clause}"
    return conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Duration display formatter
# ---------------------------------------------------------------------------


def _format_duration(start_iso: str | None, end_iso: str | None) -> str:
    if not start_iso or not end_iso:
        return "-"
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except ValueError:
        return "-"
    total_seconds = int((end - start).total_seconds())
    if total_seconds < 0:
        return "-"
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return "".join(parts)


def _started_display(started_at: str | None) -> str:
    if not started_at:
        return "-"
    try:
        dt = datetime.fromisoformat(started_at).astimezone(UTC)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return started_at


# ---------------------------------------------------------------------------
# Plain-text formatter
# ---------------------------------------------------------------------------


def _format_plain(rows: list[sqlite3.Row]) -> str:
    headers = ["RUN ID", "SOURCE", "STARTED", "DUR", "EVENTS", "TOKENS", "MODEL"]
    data_rows: list[list[str]] = []
    for row in rows:
        data_rows.append(
            [
                str(row["run_id"] or "-"),
                str(row["source"] or "-"),
                _started_display(row["started_at"]),
                _format_duration(row["started_at"], row["ended_at"]),
                str(int(row["event_count"] or 0)),
                f"{int(row['total_tokens'] or 0):,}",
                str(row["model"] or "-"),
            ]
        )

    col_widths = [len(h) for h in headers]
    for dr in data_rows:
        for i, cell in enumerate(dr):
            col_widths[i] = max(col_widths[i], len(cell))

    lines: list[str] = []
    header_line = "  ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    lines.append(header_line)
    lines.append("  ".join("-" * w for w in col_widths))
    for dr in data_rows:
        lines.append("  ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(dr)))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rich formatter (optional, TTY-gated)
# ---------------------------------------------------------------------------


def _format_rich(rows: list[sqlite3.Row]) -> str | None:
    try:
        import rich.box
        from rich.console import Console
        from rich.table import Table
    except ImportError:
        return None

    table = Table(box=rich.box.SIMPLE)
    for col in ("RUN ID", "SOURCE", "STARTED", "DUR", "EVENTS", "TOKENS", "MODEL"):
        table.add_column(col)

    for row in rows:
        table.add_row(
            str(row["run_id"] or "-"),
            str(row["source"] or "-"),
            _started_display(row["started_at"]),
            _format_duration(row["started_at"], row["ended_at"]),
            str(int(row["event_count"] or 0)),
            f"{int(row['total_tokens'] or 0):,}",
            str(row["model"] or "-"),
        )

    buf = StringIO()
    console = Console(file=buf, highlight=False)
    console.print(table)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# JSON formatter
# ---------------------------------------------------------------------------


def _format_json(rows: list[sqlite3.Row]) -> str:
    """Return a JSON array of run objects.

    Fields: run_id, source, session_id, parent_session_id, started_at, ended_at,
    cwd, model, event_count, total_tokens, state_mtime, cost_mtime, indexed_at,
    duration (derived human-readable string).
    """
    records = []
    for row in rows:
        d = dict(row)
        d["duration"] = _format_duration(d.get("started_at"), d.get("ended_at"))
        records.append(d)
    return json.dumps(records, indent=2)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def run_ls(
    *,
    source: str,
    since: timedelta | None,
    sort_key: str,
    reverse: bool,
    limit: int,
    as_json: bool,
    reindex: bool,
) -> int:
    root = _data_root()
    runs_root = root / RUNS_DIR_NAME

    if not runs_root.exists():
        print(f"no runs found at {runs_root}")
        return 0

    index_path = root / INDEX_FILE_NAME
    try:
        with contextlib.closing(_open_index(index_path)) as conn:
            if reindex:
                conn.execute("DROP TABLE IF EXISTS runs")
                conn.commit()
                _init_schema(conn)

            _refresh_index(conn, runs_root, root)
            rows = _query_runs(
                conn,
                source=source,
                since=since,
                sort_key=sort_key,
                reverse=reverse,
                limit=limit,
            )

            if as_json:
                print(_format_json(rows))
                return 0

            if sys.stdout.isatty():
                rich_output = _format_rich(rows)
                if rich_output is not None:
                    print(rich_output, end="")
                    return 0

            print(_format_plain(rows))
            return 0

    except (sqlite3.DatabaseError, OSError) as exc:
        _log_self(root, f"ls: unexpected error: {exc!r}")
        print(f"agentlog ls: error: {exc}", file=sys.stderr)
        return 1
