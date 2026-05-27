"""Capture Claude Code hook events into the unified runs/<id>/ schema.

Public surface:
  dispatch(event, payload, *, now=None) -> int   -- pure-ish entry point
  run_hook(event) -> int                          -- CLI-facing, fail-open boundary

All code paths in run_hook are wrapped in a top-level try/except so a buggy
handler NEVER breaks a Claude Code session (CLAUDE.md hard rule #2).
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentlog._constants import (
    DEFAULT_DATA_ROOT_NAME,
    MAX_INLINE_BYTES,
    RUNS_DIR_NAME,
    SCHEMA_VERSION,
    SELF_LOG_NAME,
    SOURCE_HOOKS,
    UNKNOWN_SESSION_PREFIX,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _data_root() -> Path:
    env = os.environ.get("AGENTLOG_HOME")
    if env:
        return Path(env)
    return Path.home() / DEFAULT_DATA_ROOT_NAME


def _session_dir(root: Path, session_id: str) -> Path:
    return root / RUNS_DIR_NAME / session_id


def _isoformat(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def _fallback_session_id() -> str:
    return f"{UNKNOWN_SESSION_PREFIX}-{os.getpid()}-{int(time.time() * 1000)}"


def _resolve_session_id(payload: dict[str, Any]) -> str:
    sid = payload.get("session_id")
    if isinstance(sid, str) and sid:
        return sid
    return _fallback_session_id()


def _truncate(value: str, limit: int) -> tuple[str, int]:
    """Clip value to at most limit UTF-8 bytes without splitting codepoints."""
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, 0
    clipped = encoded[:limit].decode("utf-8", errors="ignore")
    dropped = len(encoded) - len(clipped.encode("utf-8"))
    return clipped, dropped


def _log_self(root: Path, message: str) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        log_path = root / SELF_LOG_NAME
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {message}\n")
    except Exception:  # noqa: BLE001
        pass


def _append_event(session_dir: Path, record: dict[str, Any]) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    with (session_dir / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _write_state(session_dir: Path, state: dict[str, Any]) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "state.json"
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"))
        f.write("\n")
    os.replace(tmp, path)


def _write_cost(session_dir: Path, cost: dict[str, Any]) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "cost.json"
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cost, f, separators=(",", ":"))
        f.write("\n")
    os.replace(tmp, path)


def _read_json(path: Path, root: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data: Any = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        _log_self(root, f"malformed JSON in {path}: {exc}")
        return {}
    if isinstance(data, dict):
        return data
    return {}


# ---------------------------------------------------------------------------
# Per-event recorders
# ---------------------------------------------------------------------------


def _on_session_start(
    payload: dict[str, Any],
    now: datetime,
    session_dir: Path,
    session_id: str,
    root: Path,
) -> None:
    del root  # unused — uniform recorder signature
    session_dir.mkdir(parents=True, exist_ok=True)
    parent_session_id = payload.get("parent_session_id")
    cwd = payload.get("cwd")
    model = payload.get("model")

    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "parent_session_id": parent_session_id,
        "started_at": _isoformat(now),
        "ended_at": None,
        "cwd": cwd,
        "model": model,
        "event_count": 0,
        "source": SOURCE_HOOKS,
        "summary": None,
    }
    _write_state(session_dir, state)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event": "session_start",
        "timestamp": _isoformat(now),
        "session_id": session_id,
        "source": SOURCE_HOOKS,
        "parent_session_id": parent_session_id,
        "cwd": cwd,
        "model": model,
    }
    _append_event(session_dir, record)


def _on_user_prompt_submit(
    payload: dict[str, Any],
    now: datetime,
    session_dir: Path,
    session_id: str,
    root: Path,
) -> None:
    del root  # unused — uniform recorder signature
    raw_text: str | None = None
    for key in ("prompt", "text", "user_prompt"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            raw_text = val
            break
    if raw_text is None:
        raw_text = json.dumps(payload, separators=(",", ":"))

    text_bytes = len(raw_text.encode("utf-8"))
    clipped, truncated_bytes = _truncate(raw_text, MAX_INLINE_BYTES)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event": "prompt",
        "timestamp": _isoformat(now),
        "session_id": session_id,
        "source": SOURCE_HOOKS,
        "text": clipped,
        "text_bytes": text_bytes,
        "truncated_bytes": truncated_bytes,
    }
    _append_event(session_dir, record)


def _on_post_tool_use(
    payload: dict[str, Any],
    now: datetime,
    session_dir: Path,
    session_id: str,
    root: Path,
) -> None:
    del root  # unused — uniform recorder signature
    tool_raw = payload.get("tool_name") or payload.get("tool") or "unknown"
    tool = str(tool_raw)

    params_raw = json.dumps(payload.get("tool_input") or {}, separators=(",", ":"))
    result_raw = json.dumps(
        payload.get("tool_response") or payload.get("tool_result") or {},
        separators=(",", ":"),
    )

    params_summary, params_truncated = _truncate(params_raw, MAX_INLINE_BYTES)
    result_summary, result_truncated = _truncate(result_raw, MAX_INLINE_BYTES)
    total_truncated = params_truncated + result_truncated

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event": "tool_use",
        "timestamp": _isoformat(now),
        "session_id": session_id,
        "source": SOURCE_HOOKS,
        "tool": tool,
        "params_summary": params_summary,
        "result_summary": result_summary,
        "duration_ms": payload.get("duration_ms"),
        "truncated_bytes": total_truncated,
    }
    _append_event(session_dir, record)


def _on_stop(
    payload: dict[str, Any],
    now: datetime,
    session_dir: Path,
    session_id: str,
    root: Path,
) -> None:
    usage = payload.get("usage") or {}
    usage_record: dict[str, int] = {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cache_creation_tokens": int(usage.get("cache_creation_tokens") or 0),
    }

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event": "stop",
        "timestamp": _isoformat(now),
        "session_id": session_id,
        "source": SOURCE_HOOKS,
        "usage": usage_record,
    }
    _append_event(session_dir, record)

    cost_path = session_dir / "cost.json"
    existing = _read_json(cost_path, root)
    totals: dict[str, int] = existing.get("totals") or {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        totals[key] = int(totals.get(key) or 0) + usage_record[key]

    cost: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "totals": totals,
        "phases": existing.get("phases") or {},
    }
    _write_cost(session_dir, cost)


def _on_session_end(
    payload: dict[str, Any],
    now: datetime,
    session_dir: Path,
    session_id: str,
    root: Path,
) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event": "session_end",
        "timestamp": _isoformat(now),
        "session_id": session_id,
        "source": SOURCE_HOOKS,
        "summary": payload.get("summary"),
    }
    _append_event(session_dir, record)

    state_path = session_dir / "state.json"
    state = _read_json(state_path, root)
    if not state:
        state = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "parent_session_id": None,
            "started_at": None,
            "ended_at": None,
            "cwd": None,
            "model": None,
            "event_count": 0,
            "source": SOURCE_HOOKS,
            "summary": None,
        }
    state["ended_at"] = _isoformat(now)
    state["summary"] = payload.get("summary")

    events_path = session_dir / "events.jsonl"
    event_count = 0
    try:
        with events_path.open(encoding="utf-8") as f:
            event_count = sum(1 for line in f if line.strip())
    except FileNotFoundError:
        pass
    state["event_count"] = event_count

    _write_state(session_dir, state)


def _on_unknown(
    event: str,
    payload: dict[str, Any],
    now: datetime,
    session_dir: Path,
    session_id: str,
    root: Path,
) -> None:
    del root  # unused — uniform recorder signature
    raw_json = json.dumps(payload, separators=(",", ":"))
    raw: Any
    if len(raw_json.encode("utf-8")) <= MAX_INLINE_BYTES:
        raw = payload
    else:
        clipped, _ = _truncate(raw_json, MAX_INLINE_BYTES)
        raw = clipped

    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event": "unknown",
        "timestamp": _isoformat(now),
        "session_id": session_id,
        "source": SOURCE_HOOKS,
        "original_event": event,
        "raw": raw,
    }
    _append_event(session_dir, record)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, Callable[..., None]] = {
    "SessionStart": _on_session_start,
    "UserPromptSubmit": _on_user_prompt_submit,
    "PostToolUse": _on_post_tool_use,
    "Stop": _on_stop,
    "SessionEnd": _on_session_end,
}

# Invariant: set(_DISPATCH) == set(EVENTS). Enforced by
# tests/test_capture.py::test_dispatch_table_matches_events rather than a
# module-level assert — an `assert` here would either be stripped under
# `python -O` (silent drift) or raise from every handler call on drift, which
# would itself violate the fail-open contract (CLAUDE.md hard rule #2).


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def dispatch(
    event: str,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> int:
    """Write event records for one hook invocation. Returns 0 always."""
    if now is None:
        now = datetime.now(UTC)
    session_id = _resolve_session_id(payload)
    root = _data_root()
    session_dir = _session_dir(root, session_id)

    recorder = _DISPATCH.get(event)
    if recorder is not None:
        recorder(payload, now, session_dir, session_id, root)
    else:
        _on_unknown(event, payload, now, session_dir, session_id, root)
    return 0


def run_hook(event: str) -> int:
    """CLI entry point. Reads JSON from stdin, calls dispatch, returns 0 always."""
    root = _data_root()
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            _log_self(root, f"empty stdin on {event}")
            return 0
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            _log_self(root, f"malformed JSON on {event}: {exc}")
            return 0
        if not isinstance(payload, dict):
            _log_self(root, f"non-object payload on {event}: {type(payload).__name__}")
            return 0
        return dispatch(event, payload)
    except Exception as exc:  # noqa: BLE001 — fail-open per CLAUDE.md hard rule #2
        with contextlib.suppress(Exception):  # even logging is best-effort
            _log_self(root, f"unhandled in {event}: {exc!r}")
        return 0
