"""Translate cc_raw_output.jsonl (stream-json) into the unified runs/<id>/ schema.

CLAUDE.md hard rules pinned by this module:
  #6  Local-first: zero network calls. Reads local files, writes to $AGENTLOG_HOME.
  #7  Schema discipline: every JSONL record carries schema_version:1.
       Unknown/unparseable records -> event:"unknown" with raw payload.

Failure contract (INVERSE of capture.run_hook):
  tail is a user-invoked CLI command, not a Claude Code hot-path hook.
  Errors surface via stderr + non-zero exit code rather than being swallowed.
  The fail-open contract (CLAUDE.md hard rule #2) remains scoped to capture.run_hook.

Helpers are duplicated from capture.py rather than shared. The two modules have
inverted failure contracts (fail-open vs fail-loud) that will drift over time.
A shared _io.py extraction is deferred to v0.2+ (see spec for rationale).

v0.1 limitations (documented, not bugs):
  - Tool-use back-fill deferred: tool_use events ship with result_summary=null,
    duration_ms=null. Back-fill of tool_result into prior tool_use is v0.2+.
  - assistant.thinking blocks skipped (stay minimal; may surface in v0.2+).
  - user records containing only tool_result content skipped (back-fill is v0.2+).
  - No live tail -f mode (v0.2+).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentlog._constants import (
    DEFAULT_DATA_ROOT_NAME,
    MAX_INLINE_BYTES,
    RUNS_DIR_NAME,
    SCHEMA_VERSION,
    SELF_LOG_NAME,
    SOURCE_SDK,
)

_MAX_DEPTH = 5

# ---------------------------------------------------------------------------
# Helpers (duplicated from capture.py — inverted failure contract)
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


# ---------------------------------------------------------------------------
# Run-id derivation
# ---------------------------------------------------------------------------


def _derive_run_id(path: Path, explicit: str | None, root: Path) -> tuple[str, bool]:
    """Return (run_id, used_fallback).

    explicit -> verbatim (no sdk- prefix applied).
    First non-blank line is system/init with session_id -> 'sdk-' + session_id.
    Otherwise -> 'sdk-' + sha1(abspath)[:12], used_fallback=True.
    """
    if explicit is not None:
        return (explicit, False)

    try:
        with path.open(encoding="utf-8") as f:
            for raw_line in f:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    record: Any = json.loads(stripped)
                except json.JSONDecodeError:
                    break
                if (
                    isinstance(record, dict)
                    and record.get("type") == "system"
                    and record.get("subtype") == "init"
                ):
                    sid = record.get("session_id")
                    if isinstance(sid, str) and sid:
                        return (f"sdk-{sid}", False)
                break
    except OSError:
        pass

    fallback = f"sdk-{hashlib.sha1(str(path.resolve()).encode(), usedforsecurity=False).hexdigest()[:12]}"
    _log_self(root, f"no init record in {path}; using fallback id")
    return (fallback, True)


# ---------------------------------------------------------------------------
# Translator (pure — no I/O, no clock)
# ---------------------------------------------------------------------------


def _message_content(record: dict[str, Any]) -> list[Any]:
    """Pull the `message.content` list out of an assistant/user record, robustly."""
    msg = record.get("message")
    if not isinstance(msg, dict):
        return []
    raw = msg.get("content")
    return raw if isinstance(raw, list) else []


def _text_event(base: dict[str, Any], event: str, raw_text: str) -> dict[str, Any]:
    """Build a text-bearing event (assistant_text or prompt) with truncation."""
    text_bytes = len(raw_text.encode("utf-8"))
    clipped, truncated_bytes = _truncate(raw_text, MAX_INLINE_BYTES)
    return {
        **base,
        "event": event,
        "text": clipped,
        "text_bytes": text_bytes,
        "truncated_bytes": truncated_bytes,
    }


def _translate_system(record: dict[str, Any], base: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if record.get("subtype") != "init":
        yield from _translate_unknown(record, base)
        return
    yield {
        **base,
        "event": "session_start",
        "cwd": record.get("cwd"),
        "model": record.get("model"),
        "parent_session_id": None,
    }


def _translate_assistant(
    record: dict[str, Any], base: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    for block in _message_content(record):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            yield _text_event(base, "assistant_text", str(block.get("text") or ""))
        elif block_type == "tool_use":
            tool_name = str(block.get("name") or "unknown")
            params_raw = json.dumps(block.get("input") or {}, separators=(",", ":"))
            params_summary, _ = _truncate(params_raw, MAX_INLINE_BYTES)
            yield {
                **base,
                "event": "tool_use",
                "tool": tool_name,
                "params_summary": params_summary,
                "result_summary": None,
                "duration_ms": None,
                "truncated_bytes": 0,
            }
        # thinking blocks intentionally skipped in v0.1


def _translate_user(record: dict[str, Any], base: dict[str, Any]) -> Iterator[dict[str, Any]]:
    # tool_result-only user records (no text blocks) are skipped — they're
    # protocol echoes, not human input.
    for block in _message_content(record):
        if isinstance(block, dict) and block.get("type") == "text":
            yield _text_event(base, "prompt", str(block.get("text") or ""))


def _translate_result(
    record: dict[str, Any], base: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    usage_raw = record.get("usage") or {}
    usage: dict[str, int] = {
        "input_tokens": int(usage_raw.get("input_tokens") or 0),
        "output_tokens": int(usage_raw.get("output_tokens") or 0),
        "cache_read_tokens": int(usage_raw.get("cache_read_input_tokens") or 0),
        "cache_creation_tokens": int(usage_raw.get("cache_creation_input_tokens") or 0),
    }
    yield {
        **base,
        "event": "stop",
        "usage": usage,
        "duration_ms": record.get("duration_ms"),
        "total_cost_usd": record.get("total_cost_usd"),
        "is_error": bool(record.get("is_error")),
    }


def _translate_unknown(
    record: dict[str, Any], base: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    raw_json = json.dumps(record, separators=(",", ":"))
    raw: Any = record
    if len(raw_json.encode("utf-8")) > MAX_INLINE_BYTES:
        raw, _ = _truncate(raw_json, MAX_INLINE_BYTES)
    yield {
        **base,
        "event": "unknown",
        "original_type": record.get("type"),
        "raw": raw,
    }


# Per-record-type dispatch. Mirrors capture._DISPATCH. Anything not in this
# table falls through to _translate_unknown — graceful schema-drift handling.
_RECORD_TRANSLATORS: dict[
    str, Callable[[dict[str, Any], dict[str, Any]], Iterator[dict[str, Any]]]
] = {
    "system": _translate_system,
    "assistant": _translate_assistant,
    "user": _translate_user,
    "result": _translate_result,
}


def _translate(
    records: Iterable[dict[str, Any]],
    *,
    run_id: str,
    abs_path: str,
    now: datetime,
) -> Iterator[dict[str, Any]]:
    """Yield event-dict records ready for _append_event.

    Pure: no I/O, no side effects. Caller injects now and abs_path for
    deterministic tests. Per-record dispatch via _RECORD_TRANSLATORS.
    """
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": run_id,
        "source": SOURCE_SDK,
        "sdk_source_file": abs_path,
        "timestamp": _isoformat(now),
    }
    for record in records:
        translator = _RECORD_TRANSLATORS.get(str(record.get("type") or ""), _translate_unknown)
        yield from translator(record, base)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _is_already_ingested(run_dir: Path) -> bool:
    return (run_dir / "events.jsonl").exists()


# ---------------------------------------------------------------------------
# Per-file processor
# ---------------------------------------------------------------------------


def _process_one(
    path: Path,
    *,
    run_id: str | None,
    source_name: str | None,
    dry_run: bool,
    force: bool,
) -> int:
    root = _data_root()
    derived_id, _ = _derive_run_id(path, run_id, root)
    run_dir = _session_dir(root, derived_id)

    if _is_already_ingested(run_dir):
        msg = f"already ingested {path} → {run_dir}; use --force to re-ingest"
        if dry_run:
            print(msg)
            return 0
        if force:
            for fname in ("state.json", "events.jsonl", "cost.json"):
                (run_dir / fname).unlink(missing_ok=True)
        else:
            print(msg)
            return 0

    now = datetime.now(UTC)

    try:
        started_at = _isoformat(datetime.fromtimestamp(path.stat().st_mtime, tz=UTC))
    except OSError:
        started_at = _isoformat(now)

    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": derived_id,
        "source": SOURCE_SDK,
        "source_file": str(path),
        "source_name": source_name or path.name,
        "started_at": started_at,
        "ended_at": None,
        "cwd": None,
        "model": None,
        "event_count": 0,
        "session_failed": False,
        "truncated": False,
        "summary": None,
        "parent_session_id": None,
    }
    cost_totals: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    event_count = 0
    dry_counts: dict[str, int] = {}

    try:
        with path.open(encoding="utf-8") as fh:
            lineno = 0
            for raw_line in fh:
                lineno += 1
                stripped = raw_line.strip()
                if not stripped:
                    continue

                try:
                    obj: Any = json.loads(stripped)
                    if not isinstance(obj, dict):
                        raise json.JSONDecodeError("expected object", stripped, 0)
                    record: dict[str, Any] = obj
                except json.JSONDecodeError:
                    state["truncated"] = True
                    err_event: dict[str, Any] = {
                        "schema_version": SCHEMA_VERSION,
                        "event": "unknown",
                        "timestamp": _isoformat(now),
                        "session_id": derived_id,
                        "source": SOURCE_SDK,
                        "sdk_source_file": str(path),
                        "original_type": "parse_error",
                        "raw": f"unparseable line {lineno}",
                    }
                    event_count += 1
                    if dry_run:
                        dry_counts["unknown"] = dry_counts.get("unknown", 0) + 1
                    else:
                        _append_event(run_dir, err_event)
                    continue

                for event in _translate([record], run_id=derived_id, abs_path=str(path), now=now):
                    event_count += 1
                    ev_kind = str(event.get("event", "unknown"))
                    if dry_run:
                        dry_counts[ev_kind] = dry_counts.get(ev_kind, 0) + 1
                    else:
                        _append_event(run_dir, event)
                        if ev_kind == "session_start":
                            state["cwd"] = event.get("cwd")
                            state["model"] = event.get("model")
                        elif ev_kind == "stop":
                            usage = event.get("usage") or {}
                            for key in cost_totals:
                                cost_totals[key] += int(usage.get(key) or 0)
                            if event.get("is_error"):
                                state["session_failed"] = True

    except OSError as exc:
        print(f"agentlog tail: error reading {path}: {exc}", file=sys.stderr)
        return 1

    if dry_run:
        total = sum(dry_counts.values())
        if dry_counts:
            parts = ", ".join(f"{k}:{v}" for k, v in sorted(dry_counts.items()))
            print(f"would write: {derived_id} ({total} events: {parts})")
        else:
            print(f"would write: {derived_id} (0 events)")
        return 0

    state["event_count"] = event_count
    state["ended_at"] = _isoformat(datetime.now(UTC))

    # Ensure events.jsonl exists for idempotency detection (even if empty).
    run_dir.mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        events_path.touch()

    _write_state(run_dir, state)
    _write_cost(
        run_dir,
        {
            "schema_version": SCHEMA_VERSION,
            "session_id": derived_id,
            "totals": cost_totals,
            "phases": {},
        },
    )

    print(f"{path} → {run_dir} ({event_count} events)")
    return 0


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def run_tail(
    path: Path,
    *,
    run_id: str | None,
    source_name: str | None,
    dry_run: bool,
    force: bool,
) -> int:
    """Ingest cc_raw_output.jsonl file(s) into the unified runs/<id>/ schema.

    Returns 0 on success (including already-ingested and no-files-found),
    2 for user errors (missing path, --run-id with multi-file directory),
    1 for unexpected I/O failures.
    """
    abs_path = path.expanduser().resolve()

    if not abs_path.exists():
        print(f"agentlog tail: {path}: no such file or directory", file=sys.stderr)
        return 2

    if abs_path.is_dir():
        files = sorted(
            p
            for p in abs_path.rglob("cc_raw_output.jsonl")
            if len(p.relative_to(abs_path).parts) <= _MAX_DEPTH
        )
        if not files:
            print(f"no cc_raw_output.jsonl files found under {abs_path}")
            return 0
        if run_id is not None and len(files) > 1:
            print(
                f"agentlog tail: --run-id cannot be used with a directory"
                f" containing multiple files ({len(files)} found)",
                file=sys.stderr,
            )
            return 2
        worst_rc = 0
        for f in files:
            rc = _process_one(
                f, run_id=run_id, source_name=source_name, dry_run=dry_run, force=force
            )
            if rc > worst_rc:
                worst_rc = rc
        return worst_rc

    return _process_one(
        abs_path, run_id=run_id, source_name=source_name, dry_run=dry_run, force=force
    )
