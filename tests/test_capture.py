"""Tests for src/agentlog/capture.py."""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentlog import _constants, capture
from agentlog._constants import (
    EVENTS,
    HOOK_COMMAND_PREFIX,
    MAX_INLINE_BYTES,
    SCHEMA_VERSION,
    SOURCE_HOOKS,
)
from agentlog.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
_FIXED_TS = "2026-05-27T12:00:00.000000+00:00"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_json(path: Path) -> dict[str, Any]:
    data: Any = json.loads(path.read_text())
    assert isinstance(data, dict)
    return data


# ---------------------------------------------------------------------------
# _constants smoke
# ---------------------------------------------------------------------------


def test_constants_module_exports_events_and_prefix() -> None:
    assert EVENTS == ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd")
    assert HOOK_COMMAND_PREFIX == "agentlog _hook"


def test_hooks_install_events_identity() -> None:
    from agentlog import hooks_install

    assert hooks_install.EVENTS is _constants.EVENTS


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_isoformat_is_utc_and_sortable() -> None:
    ts = capture._isoformat(_FIXED_NOW)
    assert ts == _FIXED_TS
    assert ts.endswith("+00:00")


@pytest.mark.parametrize(
    "value,limit,expected_dropped",
    [
        ("hello", 10, 0),
        ("hello", 5, 0),
        ("hello", 4, 1),
        ("🎉🎉", 4, 4),
    ],
)
def test_truncate(value: str, limit: int, expected_dropped: int) -> None:
    clipped, dropped = capture._truncate(value, limit)
    assert dropped == expected_dropped
    assert clipped.encode("utf-8") == clipped.encode("utf-8")  # valid utf-8
    assert len(clipped.encode("utf-8")) <= limit


def test_truncate_emoji_boundary() -> None:
    # 4-byte emoji repeated; last one must not be split
    emoji = "🎉" * (MAX_INLINE_BYTES // 4 + 1)
    clipped, dropped = capture._truncate(emoji, MAX_INLINE_BYTES)
    assert dropped > 0
    assert len(clipped.encode("utf-8")) <= MAX_INLINE_BYTES
    clipped.encode("utf-8")  # must not raise UnicodeEncodeError


def test_data_root_uses_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    assert capture._data_root() == tmp_path


def test_data_root_defaults_to_home_dotdir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTLOG_HOME", raising=False)
    root = capture._data_root()
    assert root.name == ".agentlog"


# ---------------------------------------------------------------------------
# Per-event recorder tests
# ---------------------------------------------------------------------------


def test_dispatch_session_start_writes_state_and_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    payload = {"session_id": "s1", "cwd": "/home/user", "model": "claude-sonnet"}
    capture.dispatch("SessionStart", payload, now=_FIXED_NOW)

    session_dir = tmp_path / "runs" / "s1"
    assert session_dir.is_dir()

    state = _read_json(session_dir / "state.json")
    assert state["schema_version"] == SCHEMA_VERSION
    assert state["session_id"] == "s1"
    assert state["source"] == SOURCE_HOOKS
    assert state["started_at"] == _FIXED_TS
    assert state["ended_at"] is None
    assert state["cwd"] == "/home/user"
    assert state["model"] == "claude-sonnet"

    events = _read_jsonl(session_dir / "events.jsonl")
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "session_start"
    assert ev["schema_version"] == SCHEMA_VERSION
    assert ev["source"] == SOURCE_HOOKS
    assert ev["timestamp"] == _FIXED_TS
    assert ev["session_id"] == "s1"


def test_dispatch_user_prompt_submit_appends_prompt_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    text = "hello world"
    payload = {"session_id": "s2", "prompt": text}
    capture.dispatch("UserPromptSubmit", payload, now=_FIXED_NOW)

    session_dir = tmp_path / "runs" / "s2"
    events = _read_jsonl(session_dir / "events.jsonl")
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "prompt"
    assert ev["text"] == text
    assert ev["text_bytes"] == len(text.encode("utf-8"))
    assert ev["truncated_bytes"] == 0


def test_dispatch_user_prompt_submit_fallback_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    payload = {"session_id": "s3", "text": "via text key"}
    capture.dispatch("UserPromptSubmit", payload, now=_FIXED_NOW)
    events = _read_jsonl(tmp_path / "runs" / "s3" / "events.jsonl")
    assert events[0]["text"] == "via text key"


def test_dispatch_post_tool_use_records_tool_and_summaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    payload = {
        "session_id": "s4",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": {"output": "file.txt"},
        "duration_ms": 42,
    }
    capture.dispatch("PostToolUse", payload, now=_FIXED_NOW)

    events = _read_jsonl(tmp_path / "runs" / "s4" / "events.jsonl")
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "tool_use"
    assert ev["tool"] == "Bash"
    assert ev["duration_ms"] == 42
    assert ev["truncated_bytes"] == 0
    assert '{"command":"ls"}' in ev["params_summary"]


def test_dispatch_post_tool_use_truncates_large_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    large_output = "x" * (MAX_INLINE_BYTES + 100)
    payload = {
        "session_id": "s5",
        "tool_name": "Read",
        "tool_input": {},
        "tool_response": {"output": large_output},
    }
    capture.dispatch("PostToolUse", payload, now=_FIXED_NOW)

    events = _read_jsonl(tmp_path / "runs" / "s5" / "events.jsonl")
    ev = events[0]
    assert ev["truncated_bytes"] > 0
    assert len(ev["result_summary"].encode("utf-8")) <= MAX_INLINE_BYTES


def test_dispatch_stop_updates_cost_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    payload = {
        "session_id": "s6",
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "cache_creation_tokens": 5,
        },
    }
    capture.dispatch("Stop", payload, now=_FIXED_NOW)

    session_dir = tmp_path / "runs" / "s6"
    events = _read_jsonl(session_dir / "events.jsonl")
    assert events[0]["event"] == "stop"
    assert events[0]["usage"]["input_tokens"] == 100

    cost = _read_json(session_dir / "cost.json")
    assert cost["schema_version"] == SCHEMA_VERSION
    assert cost["totals"]["input_tokens"] == 100
    assert cost["totals"]["output_tokens"] == 50
    assert cost["totals"]["cache_read_tokens"] == 10
    assert cost["totals"]["cache_creation_tokens"] == 5


def test_dispatch_stop_accumulates_across_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    base = {"session_id": "s7", "usage": {"input_tokens": 10, "output_tokens": 5,
                                           "cache_read_tokens": 0, "cache_creation_tokens": 0}}
    capture.dispatch("Stop", base, now=_FIXED_NOW)
    capture.dispatch("Stop", base, now=_FIXED_NOW)

    cost = _read_json(tmp_path / "runs" / "s7" / "cost.json")
    assert cost["totals"]["input_tokens"] == 20
    assert cost["totals"]["output_tokens"] == 10


def test_dispatch_session_end_finalises_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    # Prime with a SessionStart so state.json and some events exist
    start_payload = {"session_id": "s8", "cwd": "/tmp", "model": "m"}
    capture.dispatch("SessionStart", start_payload, now=_FIXED_NOW)
    capture.dispatch("UserPromptSubmit", {"session_id": "s8", "prompt": "hi"}, now=_FIXED_NOW)

    end_payload = {"session_id": "s8", "summary": "done"}
    capture.dispatch("SessionEnd", end_payload, now=_FIXED_NOW)

    session_dir = tmp_path / "runs" / "s8"
    state = _read_json(session_dir / "state.json")
    assert state["ended_at"] == _FIXED_TS
    assert state["summary"] == "done"
    # 3 events: session_start + prompt + session_end
    assert state["event_count"] == 3

    events = _read_jsonl(session_dir / "events.jsonl")
    assert events[-1]["event"] == "session_end"


def test_dispatch_unknown_event_writes_generic_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    payload = {"session_id": "s9", "extra": "data"}
    rc = capture.dispatch("FutureUnknownEvent", payload, now=_FIXED_NOW)
    assert rc == 0

    events = _read_jsonl(tmp_path / "runs" / "s9" / "events.jsonl")
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "unknown"
    assert ev["original_event"] == "FutureUnknownEvent"
    assert ev["schema_version"] == SCHEMA_VERSION
    assert ev["source"] == SOURCE_HOOKS


# ---------------------------------------------------------------------------
# run_hook fail-open tests
# ---------------------------------------------------------------------------


def test_run_hook_malformed_stdin_logs_and_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    rc = capture.run_hook("SessionStart")
    assert rc == 0
    assert not (tmp_path / "runs").exists()
    self_log = (tmp_path / "_self.log").read_text()
    assert "malformed JSON" in self_log


def test_run_hook_empty_stdin_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO("   "))
    rc = capture.run_hook("SessionStart")
    assert rc == 0
    self_log = (tmp_path / "_self.log").read_text()
    assert "empty stdin" in self_log


def test_run_hook_non_object_payload_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    for bad in ('[1,2,3]', '"a string"', '42'):
        monkeypatch.setattr(sys, "stdin", io.StringIO(bad))
        rc = capture.run_hook("SessionStart")
        assert rc == 0
    self_log = (tmp_path / "_self.log").read_text()
    assert "non-object payload" in self_log


def test_run_hook_missing_session_id_uses_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"cwd": "/tmp"}'))
    rc = capture.run_hook("SessionStart")
    assert rc == 0
    runs = list((tmp_path / "runs").iterdir())
    assert len(runs) == 1
    assert runs[0].name.startswith("unknown_session")


@pytest.mark.skipif(sys.platform == "win32", reason="chmod semantics differ on Windows")
def test_run_hook_read_only_root_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    tmp_path.chmod(0o500)
    try:
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id":"ro"}'))
        rc = capture.run_hook("SessionStart")
        assert rc == 0
    finally:
        tmp_path.chmod(0o700)


def test_session_end_before_session_start_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    payload = {"session_id": "orphan"}
    rc = capture.dispatch("SessionEnd", payload, now=_FIXED_NOW)
    assert rc == 0

    session_dir = tmp_path / "runs" / "orphan"
    assert session_dir.is_dir()
    state = _read_json(session_dir / "state.json")
    assert state["ended_at"] == _FIXED_TS
    assert state["started_at"] is None


def test_truncation_records_truncated_bytes_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    big_text = "a" * (MAX_INLINE_BYTES + 1024)
    payload = {"session_id": "trunc", "prompt": big_text}
    capture.dispatch("UserPromptSubmit", payload, now=_FIXED_NOW)

    events = _read_jsonl(tmp_path / "runs" / "trunc" / "events.jsonl")
    ev = events[0]
    assert ev["truncated_bytes"] > 0
    assert len(ev["text"].encode("utf-8")) <= MAX_INLINE_BYTES
    assert ev["text_bytes"] == len(big_text.encode("utf-8"))


@pytest.mark.parametrize(
    "event,payload",
    [
        ("SessionStart", {"session_id": "sv1", "cwd": "/", "model": "m"}),
        ("UserPromptSubmit", {"session_id": "sv2", "prompt": "hi"}),
        ("PostToolUse", {"session_id": "sv3", "tool_name": "Bash"}),
        ("Stop", {"session_id": "sv4", "usage": {}}),
        ("SessionEnd", {"session_id": "sv5"}),
        ("UnknownEvent", {"session_id": "sv6"}),
    ],
)
def test_schema_version_is_one_on_every_record(
    event: str,
    payload: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    capture.dispatch(event, payload, now=_FIXED_NOW)
    sid = payload["session_id"]
    session_dir = tmp_path / "runs" / sid
    events = _read_jsonl(session_dir / "events.jsonl")
    for ev in events:
        assert ev["schema_version"] == SCHEMA_VERSION, f"bad schema_version in {ev}"


def test_agentlog_home_env_var_redirects_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom_home"
    monkeypatch.setenv("AGENTLOG_HOME", str(custom))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id":"env_test"}'))
    rc = capture.run_hook("SessionStart")
    assert rc == 0
    assert (custom / "runs" / "env_test" / "state.json").exists()
    assert not (Path.home() / ".agentlog" / "runs" / "env_test").exists()


def test_self_log_write_failure_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    # Force _log_self to fail by making root unwritable, then also fail dispatch
    tmp_path.chmod(0o500)
    try:
        monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
        rc = capture.run_hook("SessionStart")
        assert rc == 0
    finally:
        tmp_path.chmod(0o700)


# ---------------------------------------------------------------------------
# Integration: full lifecycle
# ---------------------------------------------------------------------------


def test_run_hook_end_to_end_session_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    sid = "lifecycle"
    stop_usage = {"input_tokens": 200, "output_tokens": 80,
                  "cache_read_tokens": 20, "cache_creation_tokens": 0}

    def hook(event: str, payload: dict[str, Any]) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert capture.run_hook(event) == 0

    hook("SessionStart", {"session_id": sid, "cwd": "/proj", "model": "sonnet"})
    hook("UserPromptSubmit", {"session_id": sid, "prompt": "Fix the bug"})
    hook("PostToolUse", {"session_id": sid, "tool_name": "Read", "tool_input": {"path": "f.py"}})
    hook("PostToolUse", {"session_id": sid, "tool_name": "Edit", "tool_input": {}})
    hook("PostToolUse", {"session_id": sid, "tool_name": "Bash", "tool_input": {"cmd": "test"}})
    hook("Stop", {"session_id": sid, "usage": stop_usage})
    hook("SessionEnd", {"session_id": sid, "summary": "done"})

    session_dir = tmp_path / "runs" / sid
    events = _read_jsonl(session_dir / "events.jsonl")
    assert len(events) == 7

    event_names = [e["event"] for e in events]
    assert event_names == [
        "session_start", "prompt", "tool_use", "tool_use", "tool_use", "stop", "session_end"
    ]

    state = _read_json(session_dir / "state.json")
    assert state["event_count"] == 7
    assert state["ended_at"] is not None

    cost = _read_json(session_dir / "cost.json")
    assert cost["totals"]["input_tokens"] == 200
    assert cost["totals"]["output_tokens"] == 80


def test_cli_invokes_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"session_id":"x"}'))
    rc = main(["_hook", "SessionStart"])
    assert rc == 0
    assert (tmp_path / "runs" / "x" / "state.json").exists()


def test_hook_noop_subparser_exits_zero_with_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_hook returning 0 on empty stdin satisfies the existing hook test."""
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    # Empty stdin: run_hook logs "empty stdin" and returns 0
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = main(["_hook", "SessionStart"])
    assert rc == 0


# ---------------------------------------------------------------------------
# Invariants previously enforced by module-level asserts
# ---------------------------------------------------------------------------


def test_dispatch_table_matches_events() -> None:
    """Replaces the module-level `assert set(_DISPATCH) == set(EVENTS)`.

    A module-level assert would be stripped under `python -O` (silent drift)
    AND would raise from every handler invocation if drift occurred — which
    itself violates the fail-open contract (CLAUDE.md hard rule #2). Keep
    the invariant in tests where drift surfaces in CI, not in production.
    """
    assert set(capture._DISPATCH.keys()) == set(EVENTS)


# ---------------------------------------------------------------------------
# _read_json fault tolerance — corrupted state.json / cost.json
# ---------------------------------------------------------------------------


def test_read_json_logs_to_self_log_on_malformed(
    tmp_path: Path,
) -> None:
    """If state.json or cost.json is corrupted (e.g., crashed mid-write),
    _read_json must still return {} but ALSO leave a trace in _self.log so
    the operator can diagnose later. Silent recovery without diagnostics is
    a debugging dead-end (review issue fabf1d0d #3)."""
    target = tmp_path / "cost.json"
    target.write_text("{ this is not valid json")
    result = capture._read_json(target, tmp_path)
    assert result == {}
    self_log = tmp_path / _constants.SELF_LOG_NAME
    assert self_log.exists()
    assert "malformed JSON" in self_log.read_text()
    assert str(target) in self_log.read_text()


def test_read_json_missing_file_does_not_log(tmp_path: Path) -> None:
    """A missing file is the normal first-run case, NOT an error. Make sure
    we don't pollute _self.log with FileNotFoundError noise."""
    result = capture._read_json(tmp_path / "nonexistent.json", tmp_path)
    assert result == {}
    assert not (tmp_path / _constants.SELF_LOG_NAME).exists()
