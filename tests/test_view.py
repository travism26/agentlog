"""Tests for src/agentlog/view.py."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentlog._constants import RUNS_DIR_NAME
from agentlog.cli import main
from agentlog.view import (
    _TOOL_SUMMARIZERS,
    _format_duration_ms,
    _strip_ansi,
    _summarize_tool_use,
    run_view,
)

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_BASE_TS = "2026-05-27T08:00:00+00:00"
_END_TS = "2026-05-27T08:05:00+00:00"


def _seed_run_dir(
    tmp_path: Path,
    run_id: str,
    *,
    model: str = "claude-sonnet-4-6",
    events: list[dict[str, Any]] | None = None,
    include_cost: bool = True,
    include_events: bool = True,
    include_state: bool = True,
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> Path:
    """Write a minimal runs/<run_id>/ directory under tmp_path."""
    run_dir = tmp_path / RUNS_DIR_NAME / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if include_state:
        state: dict[str, Any] = {
            "schema_version": 1,
            "session_id": run_id,
            "source": "hooks",
            "model": model,
            "started_at": _BASE_TS,
            "ended_at": _END_TS,
            "event_count": len(events) if events else 3,
            "cwd": "/tmp/test",
        }
        (run_dir / "state.json").write_text(json.dumps(state))

    if include_events:
        if events is None:
            events = [
                {
                    "schema_version": 1,
                    "event": "session_start",
                    "timestamp": "2026-05-27T08:00:00+00:00",
                    "cwd": "/tmp/test",
                    "session_id": run_id,
                },
                {
                    "schema_version": 1,
                    "event": "prompt",
                    "timestamp": "2026-05-27T08:01:00+00:00",
                    "text": "hello world",
                    "session_id": run_id,
                },
                {
                    "schema_version": 1,
                    "event": "stop",
                    "timestamp": "2026-05-27T08:05:00+00:00",
                    "duration_ms": 300000,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_tokens": 0,
                        "cache_creation_tokens": 0,
                    },
                    "session_id": run_id,
                },
            ]
        lines = [json.dumps(e) for e in events]
        (run_dir / "events.jsonl").write_text("\n".join(lines) + "\n")

    if include_cost:
        cost_data: dict[str, Any] = {
            "schema_version": 1,
            "session_id": run_id,
            "totals": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            },
            "phases": {},
        }
        (run_dir / "cost.json").write_text(json.dumps(cost_data))

    return run_dir


# ---------------------------------------------------------------------------
# Unit tests: dispatch table invariant
# ---------------------------------------------------------------------------


def test_view_tool_summarizers_table_keys_match_documented_set() -> None:
    assert set(_TOOL_SUMMARIZERS) == {"Read", "Edit", "Write", "Grep", "Bash", "Glob"}


# ---------------------------------------------------------------------------
# Unit tests: per-tool summarizers
# ---------------------------------------------------------------------------


def test_view_summarize_tool_use_Read_returns_file_path() -> None:
    record = {
        "event": "tool_use",
        "tool": "Read",
        "params_summary": json.dumps({"file_path": "/some/file.py"}),
    }
    result = _summarize_tool_use(record, cap=None)
    assert "/some/file.py" in result


def test_view_summarize_tool_use_Edit_returns_file_path() -> None:
    record = {
        "event": "tool_use",
        "tool": "Edit",
        "params_summary": json.dumps({"file_path": "/src/foo.py"}),
    }
    result = _summarize_tool_use(record, cap=None)
    assert "/src/foo.py" in result


def test_view_summarize_tool_use_Write_returns_file_path() -> None:
    record = {
        "event": "tool_use",
        "tool": "Write",
        "params_summary": json.dumps({"file_path": "/out/bar.py"}),
    }
    result = _summarize_tool_use(record, cap=None)
    assert "/out/bar.py" in result


def test_view_summarize_tool_use_Grep_returns_pattern_in_path() -> None:
    record = {
        "event": "tool_use",
        "tool": "Grep",
        "params_summary": json.dumps({"pattern": "def foo", "path": "/src"}),
    }
    result = _summarize_tool_use(record, cap=None)
    assert "def foo" in result
    assert "/src" in result


def test_view_summarize_tool_use_Bash_truncates_long_commands_at_60_chars() -> None:
    long_cmd = "echo " + "x" * 200
    record = {
        "event": "tool_use",
        "tool": "Bash",
        "params_summary": json.dumps({"command": long_cmd}),
    }
    result = _summarize_tool_use(record, cap=60)
    assert result.endswith("…")
    assert len(result) <= 62  # 60 chars + ellipsis (1 char, but multibyte)


def test_view_summarize_tool_use_Glob_returns_pattern() -> None:
    record = {
        "event": "tool_use",
        "tool": "Glob",
        "params_summary": json.dumps({"pattern": "**/*.py"}),
    }
    result = _summarize_tool_use(record, cap=None)
    assert "**/*.py" in result


def test_view_summarize_tool_use_unknown_tool_falls_back_to_raw_params() -> None:
    record = {
        "event": "tool_use",
        "tool": "UnknownTool",
        "params_summary": json.dumps({"key": "value"}),
    }
    result = _summarize_tool_use(record, cap=60)
    assert "value" in result or "key" in result


def test_view_summarize_tool_use_malformed_params_summary_returns_truncated_raw_string() -> None:
    record = {
        "event": "tool_use",
        "tool": "Read",
        "params_summary": "{not valid json",
    }
    result = _summarize_tool_use(record, cap=60)
    assert isinstance(result, str)
    assert len(result) <= 62


def test_view_strip_ansi_removes_escape_sequences() -> None:
    s = "\x1b[2J\x1b[Hhello\x1b[31mworld\x1b[0m"
    assert _strip_ansi(s) == "helloworld"


def test_view_format_duration_ms_handles_none() -> None:
    assert _format_duration_ms(None) == "-"


def test_view_format_duration_ms_renders_hours_minutes_seconds() -> None:
    # 3 hours, 56 minutes, 24 seconds = 3*3600 + 56*60 + 24 = 10800 + 3360 + 24 = 14184 seconds
    # 14184 * 1000 = 14184000 ms
    result = _format_duration_ms(14184000)
    assert "3h" in result
    assert "56m" in result
    assert "24s" in result


# ---------------------------------------------------------------------------
# Integration tests: full run_view call
# ---------------------------------------------------------------------------


def test_view_happy_path_renders_three_panels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "happy-run-001")

    rc = run_view(run_id="happy-run-001", limit=100, events_only=False, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "happy-run-001" in out
    assert "session_start" in out
    assert "prompt" in out
    assert "stop" in out
    assert "COST" in out
    assert "Input" in out
    assert "Output" in out
    assert "Total" in out


def test_view_events_only_skips_header_and_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "events-only-run")

    rc = run_view(run_id="events-only-run", limit=100, events_only=True, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    # Header title and Cost footer should be absent
    assert "events-only-run" not in out
    assert "COST" not in out
    assert "Total" not in out
    # Timeline should still appear
    assert "TIMELINE" in out


def test_view_no_truncate_shows_full_assistant_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    long_text = "a" * 200
    events = [
        {
            "schema_version": 1,
            "event": "assistant_text",
            "timestamp": "2026-05-27T08:01:00+00:00",
            "text": long_text,
            "session_id": "notrunc-run",
        }
    ]
    _seed_run_dir(tmp_path, "notrunc-run", events=events)

    rc = run_view(run_id="notrunc-run", limit=100, events_only=True, no_truncate=True, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    # Our truncation code must not have fired: no ellipsis in output
    assert "…" not in out
    # All 200 chars should appear in output (may be wrapped by rich across lines)
    assert out.count("a") >= 200


def test_view_default_truncates_assistant_text_at_80_chars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    long_text = "b" * 200
    events = [
        {
            "schema_version": 1,
            "event": "assistant_text",
            "timestamp": "2026-05-27T08:01:00+00:00",
            "text": long_text,
            "session_id": "trunc-run",
        }
    ]
    _seed_run_dir(tmp_path, "trunc-run", events=events)

    rc = run_view(run_id="trunc-run", limit=100, events_only=True, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    # Our truncation code must have fired: ellipsis present
    assert "…" in out
    # The full 200-char string must not appear (was capped at 80)
    assert "b" * 200 not in out


def test_view_limit_5_shows_more_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    events = [
        {
            "schema_version": 1,
            "event": "prompt",
            "timestamp": f"2026-05-27T08:{i:02d}:00+00:00",
            "text": f"prompt {i}",
            "session_id": "limit-run",
        }
        for i in range(10)
    ]
    _seed_run_dir(tmp_path, "limit-run", events=events)

    rc = run_view(run_id="limit-run", limit=5, events_only=True, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "5 more events" in out
    assert "--limit 0 to see all" in out
    # First 5 timestamps should appear
    assert "08:00:00Z" in out
    assert "08:04:00Z" in out
    # 6th and beyond should not appear
    assert "08:05:00Z" not in out


def test_view_limit_0_shows_all_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    events = [
        {
            "schema_version": 1,
            "event": "prompt",
            "timestamp": f"2026-05-27T08:{i:02d}:00+00:00",
            "text": f"prompt {i}",
            "session_id": "all-run",
        }
        for i in range(10)
    ]
    _seed_run_dir(tmp_path, "all-run", events=events)

    rc = run_view(run_id="all-run", limit=0, events_only=True, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "more events" not in out
    for i in range(10):
        assert f"08:{i:02d}:00Z" in out


def test_view_json_mode_emits_combined_object_without_rich(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "json-run-001")

    # Block rich imports to prove --json works without it
    for mod in ["rich", "rich.box", "rich.console", "rich.panel", "rich.text"]:
        monkeypatch.setitem(sys.modules, mod, None)

    rc = run_view(run_id="json-run-001", limit=100, events_only=False, no_truncate=False, as_json=True)
    out = capsys.readouterr().out

    assert rc == 0
    data = json.loads(out)
    assert set(data.keys()) >= {"run_id", "state", "cost", "events"}
    assert data["run_id"] == "json-run-001"
    assert isinstance(data["events"], list)


def test_view_json_cost_includes_pricing_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "json-pricing-run")

    rc = run_view(run_id="json-pricing-run", limit=100, events_only=False, no_truncate=False, as_json=True)
    out = capsys.readouterr().out

    assert rc == 0
    data = json.loads(out)
    pricing_source = data["cost"]["pricing_source"]
    assert pricing_source in {"builtin", "missing"} or pricing_source.startswith("file:")


def test_view_unknown_model_renders_double_question_marks_in_footer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "unknown-model-run", model="claude-future-1-0")

    rc = run_view(run_id="unknown-model-run", limit=100, events_only=False, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "??" in out


def test_view_missing_state_returns_rc_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    # Seed only events.jsonl, no state.json
    _seed_run_dir(tmp_path, "no-state-run", include_state=False)

    rc = run_view(run_id="no-state-run", limit=100, events_only=False, no_truncate=False, as_json=False)
    err = capsys.readouterr().err

    assert rc == 2
    assert "not found" in err


def test_view_missing_events_renders_header_and_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "no-events-run", include_events=False)

    rc = run_view(run_id="no-events-run", limit=100, events_only=False, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "no-events-run" in out
    assert "no events recorded" in out


def test_view_missing_cost_renders_header_and_timeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "no-cost-run", include_cost=False)

    rc = run_view(run_id="no-cost-run", limit=100, events_only=False, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "no-cost-run" in out
    assert "no cost data recorded" in out


def test_view_zero_events_renders_empty_timeline_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "zero-events-run", events=[])

    rc = run_view(run_id="zero-events-run", limit=100, events_only=False, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "TIMELINE" in out
    assert "no events recorded" in out


def test_view_nonexistent_run_id_returns_rc_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    rc = run_view(run_id="does-not-exist", limit=100, events_only=False, no_truncate=False, as_json=False)
    err = capsys.readouterr().err

    assert rc == 2
    assert "does-not-exist" in err
    assert "not found" in err


def test_view_json_against_nonexistent_run_returns_rc_2_with_no_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    rc = run_view(run_id="missing-json-run", limit=100, events_only=False, no_truncate=False, as_json=True)
    captured = capsys.readouterr()

    assert rc == 2
    assert captured.out == ""  # no partial JSON on stdout
    assert "not found" in captured.err


def test_view_missing_rich_returns_rc_1_with_install_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "no-rich-run")

    # Block all rich submodules
    for mod in ["rich", "rich.box", "rich.console", "rich.panel", "rich.text"]:
        monkeypatch.setitem(sys.modules, mod, None)

    rc = run_view(run_id="no-rich-run", limit=100, events_only=False, no_truncate=False, as_json=False)
    err = capsys.readouterr().err

    assert rc == 1
    assert "pip install 'agentlog[tui]'" in err


def test_view_pipes_to_less_R_without_terminal_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    # Seed an event with ANSI clear-screen sequence in text
    events = [
        {
            "schema_version": 1,
            "event": "assistant_text",
            "timestamp": "2026-05-27T08:01:00+00:00",
            "text": "\x1b[2J\x1b[Hhello world",
            "session_id": "ansi-run",
        }
    ]
    _seed_run_dir(tmp_path, "ansi-run", events=events)

    rc = run_view(run_id="ansi-run", limit=100, events_only=True, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    # Clear-screen and cursor-positioning sequences must be stripped
    assert "\x1b[2J" not in out
    assert "\x1b[?" not in out


def test_view_cli_subcommand_help_lists_all_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["view", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--limit" in out
    assert "--events-only" in out
    assert "--no-truncate" in out
    assert "--json" in out


# ---------------------------------------------------------------------------
# Edge case: lesson #1 sort-key regression (REQUIRED)
# ---------------------------------------------------------------------------


def test_view_timeline_renders_older_event_above_newer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Seed two events with deliberately reversed file order: write the
    # NEWER one to events.jsonl first. View MUST sort by `timestamp`
    # ascending, so OLDER must appear above NEWER in output.
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    events = [
        {
            "schema_version": 1,
            "event": "prompt",
            "timestamp": "2026-05-27T09:00:00+00:00",  # NEWER written first
            "text": "newer prompt",
            "session_id": "sort-run",
        },
        {
            "schema_version": 1,
            "event": "prompt",
            "timestamp": "2026-05-27T08:00:00+00:00",  # OLDER written second
            "text": "older prompt",
            "session_id": "sort-run",
        },
    ]
    _seed_run_dir(tmp_path, "sort-run", events=events)

    rc = run_view(run_id="sort-run", limit=0, events_only=True, no_truncate=True, as_json=False)
    out = capsys.readouterr().out

    pos_older = out.find("08:00:00Z")
    pos_newer = out.find("09:00:00Z")
    assert pos_older < pos_newer, "expected older event above newer under timeline sort"
    assert rc == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_view_ansi_in_assistant_text_stripped_before_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    events = [
        {
            "schema_version": 1,
            "event": "assistant_text",
            "timestamp": "2026-05-27T08:01:00+00:00",
            "text": "\x1b[2J\x1b[Hattack payload",
            "session_id": "ansi-strip-run",
        }
    ]
    _seed_run_dir(tmp_path, "ansi-strip-run", events=events)

    rc = run_view(run_id="ansi-strip-run", limit=100, events_only=True, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    assert "\x1b[2J" not in out


def test_view_malformed_events_jsonl_line_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    run_dir = tmp_path / RUNS_DIR_NAME / "malformed-run"
    run_dir.mkdir(parents=True)
    state: dict[str, Any] = {
        "schema_version": 1,
        "session_id": "malformed-run",
        "source": "hooks",
        "model": "claude-sonnet-4-6",
        "started_at": _BASE_TS,
        "ended_at": _END_TS,
        "event_count": 2,
        "cwd": "/tmp",
    }
    (run_dir / "state.json").write_text(json.dumps(state))
    # Mix valid and malformed JSON lines
    (run_dir / "events.jsonl").write_text(
        '{"event": "prompt", "timestamp": "2026-05-27T08:01:00+00:00", "text": "ok"}\n'
        "{not valid json\n"
        '{"event": "stop", "timestamp": "2026-05-27T08:05:00+00:00"}\n'
    )

    rc = run_view(run_id="malformed-run", limit=100, events_only=True, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    # Valid events still rendered
    assert "prompt" in out
    assert "stop" in out


def test_view_state_json_schema_version_2_continues_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    run_dir = tmp_path / RUNS_DIR_NAME / "v2-state-run"
    run_dir.mkdir(parents=True)
    state: dict[str, Any] = {
        "schema_version": 2,
        "session_id": "v2-state-run",
        "source": "hooks",
        "model": "claude-sonnet-4-6",
        "started_at": _BASE_TS,
        "ended_at": _END_TS,
        "event_count": 1,
        "cwd": "/tmp",
    }
    (run_dir / "state.json").write_text(json.dumps(state))
    (run_dir / "events.jsonl").write_text(
        '{"event": "stop", "timestamp": "2026-05-27T08:05:00+00:00"}\n'
    )

    rc = run_view(run_id="v2-state-run", limit=100, events_only=True, no_truncate=False, as_json=False)
    assert rc == 0


def test_view_tool_use_empty_tool_name_renders_gracefully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    events = [
        {
            "schema_version": 1,
            "event": "tool_use",
            "timestamp": "2026-05-27T08:01:00+00:00",
            "tool": "",
            "params_summary": "{}",
            "session_id": "empty-tool-run",
        }
    ]
    _seed_run_dir(tmp_path, "empty-tool-run", events=events)

    rc = run_view(run_id="empty-tool-run", limit=100, events_only=True, no_truncate=False, as_json=False)
    assert rc == 0


def test_view_model_null_in_state_shows_dashes_and_question_marks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "null-model-run", model="")

    rc = run_view(run_id="null-model-run", limit=100, events_only=False, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    # Cost footer should show ?? for unknown model
    assert "??" in out


def test_view_negative_limit_returns_rc_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_dir(tmp_path, "neg-limit-run")

    rc = run_view(run_id="neg-limit-run", limit=-1, events_only=False, no_truncate=False, as_json=False)
    err = capsys.readouterr().err

    assert rc == 2
    assert "--limit" in err


def test_view_all_event_kinds_rendered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    base = datetime(2026, 5, 27, 8, 0, 0, tzinfo=UTC)
    events = [
        {
            "schema_version": 1,
            "event": "session_start",
            "timestamp": (base + timedelta(minutes=0)).isoformat(),
            "cwd": "/tmp",
            "session_id": "all-kinds-run",
        },
        {
            "schema_version": 1,
            "event": "prompt",
            "timestamp": (base + timedelta(minutes=1)).isoformat(),
            "text": "user prompt",
            "session_id": "all-kinds-run",
        },
        {
            "schema_version": 1,
            "event": "assistant_text",
            "timestamp": (base + timedelta(minutes=2)).isoformat(),
            "text": "assistant reply",
            "session_id": "all-kinds-run",
        },
        {
            "schema_version": 1,
            "event": "tool_use",
            "timestamp": (base + timedelta(minutes=3)).isoformat(),
            "tool": "Read",
            "params_summary": json.dumps({"file_path": "/tmp/x.py"}),
            "session_id": "all-kinds-run",
        },
        {
            "schema_version": 1,
            "event": "stop",
            "timestamp": (base + timedelta(minutes=4)).isoformat(),
            "duration_ms": 60000,
            "usage": {"input_tokens": 5, "output_tokens": 5, "cache_read_tokens": 0, "cache_creation_tokens": 0},
            "session_id": "all-kinds-run",
        },
        {
            "schema_version": 1,
            "event": "session_end",
            "timestamp": (base + timedelta(minutes=5)).isoformat(),
            "summary": "done",
            "session_id": "all-kinds-run",
        },
    ]
    _seed_run_dir(tmp_path, "all-kinds-run", events=events)

    rc = run_view(run_id="all-kinds-run", limit=100, events_only=True, no_truncate=False, as_json=False)
    out = capsys.readouterr().out

    assert rc == 0
    for kind in ("session_start", "prompt", "assistant_text", "tool_use", "stop", "session_end"):
        assert kind in out


def test_view_json_events_sorted_ascending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    events = [
        {
            "schema_version": 1,
            "event": "prompt",
            "timestamp": "2026-05-27T09:00:00+00:00",
            "text": "newer",
            "session_id": "json-sort-run",
        },
        {
            "schema_version": 1,
            "event": "prompt",
            "timestamp": "2026-05-27T08:00:00+00:00",
            "text": "older",
            "session_id": "json-sort-run",
        },
    ]
    _seed_run_dir(tmp_path, "json-sort-run", events=events)

    rc = run_view(run_id="json-sort-run", limit=0, events_only=False, no_truncate=False, as_json=True)
    out = capsys.readouterr().out

    assert rc == 0
    data = json.loads(out)
    ts_list = [e["timestamp"] for e in data["events"]]
    assert ts_list == sorted(ts_list), "events must be sorted ascending by timestamp in JSON output"
