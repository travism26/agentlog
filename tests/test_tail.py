"""Tests for src/agentlog/tail.py."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentlog import tail
from agentlog._constants import SCHEMA_VERSION, SOURCE_SDK
from agentlog.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"
_SDK_MINIMAL = _FIXTURES / "sdk_minimal.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_json(path: Path) -> dict[str, Any]:
    data: Any = json.loads(path.read_text())
    assert isinstance(data, dict)
    return data


def _sha1_id(path: Path) -> str:
    digest = hashlib.sha1(
        str(path.resolve()).encode(), usedforsecurity=False
    ).hexdigest()[:12]
    return f"sdk-{digest}"


# ---------------------------------------------------------------------------
# 1. Happy path — unified schema
# ---------------------------------------------------------------------------


def test_happy_path_writes_unified_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0

    run_dirs = list((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    assert (run_dir / "state.json").exists()
    assert (run_dir / "events.jsonl").exists()
    assert (run_dir / "cost.json").exists()

    events = _read_jsonl(run_dir / "events.jsonl")
    assert len(events) > 0
    for ev in events:
        assert ev["schema_version"] == SCHEMA_VERSION
        assert ev["source"] == SOURCE_SDK
        assert "sdk_source_file" in ev
        assert isinstance(ev["sdk_source_file"], str)

    state = _read_json(run_dir / "state.json")
    assert state["schema_version"] == SCHEMA_VERSION
    assert state["source"] == SOURCE_SDK


# ---------------------------------------------------------------------------
# 2. Run-id derived with sdk- prefix
# ---------------------------------------------------------------------------


def test_run_id_derived_with_sdk_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0
    assert (tmp_path / "runs" / "sdk-abc-123").is_dir()


# ---------------------------------------------------------------------------
# 3. Run-id fallback when no system/init record
# ---------------------------------------------------------------------------


def test_run_id_fallback_when_no_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    fixture = tmp_path / "cc_raw_output.jsonl"
    fixture.write_text(
        '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]},"session_id":"xyz"}\n'
    )

    rc = tail.run_tail(fixture, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0

    expected_id = _sha1_id(fixture)
    assert (tmp_path / "runs" / expected_id).is_dir()

    self_log = tmp_path / "_self.log"
    assert self_log.exists()
    assert "no init record" in self_log.read_text()


# ---------------------------------------------------------------------------
# 4. Explicit run-id is used verbatim (no sdk- prefix)
# ---------------------------------------------------------------------------


def test_explicit_run_id_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = tail.run_tail(_SDK_MINIMAL, run_id="foo", source_name=None, dry_run=False, force=False)
    assert rc == 0
    assert (tmp_path / "runs" / "foo").is_dir()
    assert not (tmp_path / "runs" / "sdk-foo").exists()


# ---------------------------------------------------------------------------
# 5. Idempotent re-ingest
# ---------------------------------------------------------------------------


def test_idempotent_re_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    rc1 = tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc1 == 0

    run_dir = tmp_path / "runs" / "sdk-abc-123"
    events_path = run_dir / "events.jsonl"
    mtime_before = events_path.stat().st_mtime

    capsys.readouterr()  # clear
    rc2 = tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc2 == 0

    out = capsys.readouterr().out
    assert "already ingested" in out

    assert events_path.stat().st_mtime == mtime_before


# ---------------------------------------------------------------------------
# 6. --force re-ingests
# ---------------------------------------------------------------------------


def test_force_re_ingests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    fixture = tmp_path / "cc_raw_output.jsonl"
    shutil.copy(_SDK_MINIMAL, fixture)

    rc1 = tail.run_tail(fixture, run_id="force-test", source_name=None, dry_run=False, force=False)
    assert rc1 == 0

    events_path = tmp_path / "runs" / "force-test" / "events.jsonl"
    original_count = len(_read_jsonl(events_path))

    # Append an extra user prompt record to the fixture
    with fixture.open("a") as fh:
        fh.write(
            '{"type":"user","message":{"content":[{"type":"text","text":"extra"}]},"session_id":"abc-123"}\n'
        )

    rc2 = tail.run_tail(fixture, run_id="force-test", source_name=None, dry_run=False, force=True)
    assert rc2 == 0

    new_count = len(_read_jsonl(events_path))
    assert new_count == original_count + 1


# ---------------------------------------------------------------------------
# 7. --dry-run writes nothing
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=True, force=False)
    assert rc == 0

    runs_dir = tmp_path / "runs"
    assert not runs_dir.exists()

    out = capsys.readouterr().out
    assert "would write" in out


# ---------------------------------------------------------------------------
# 8. --dry-run against already-ingested shows "already ingested"
# ---------------------------------------------------------------------------


def test_dry_run_against_already_ingested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=False, force=False)
    capsys.readouterr()

    rc = tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=True, force=False)
    assert rc == 0

    out = capsys.readouterr().out
    assert "already ingested" in out
    assert "would write" not in out


# ---------------------------------------------------------------------------
# 9. Missing file returns rc=2
# ---------------------------------------------------------------------------


def test_missing_file_returns_rc2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    missing = tmp_path / "nonexistent" / "cc_raw_output.jsonl"

    rc = tail.run_tail(missing, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 2

    err = capsys.readouterr().err
    assert "no such file" in err.lower() or "nonexistent" in err


# ---------------------------------------------------------------------------
# 10. Empty file uses path-hash run-id
# ---------------------------------------------------------------------------


def test_empty_file_logs_and_uses_path_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    fixture = tmp_path / "cc_raw_output.jsonl"
    fixture.touch()

    rc = tail.run_tail(fixture, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0

    expected_id = _sha1_id(fixture)
    run_dir = tmp_path / "runs" / expected_id
    assert run_dir.is_dir()

    # events.jsonl must exist (for idempotency detection) but be empty
    events_path = run_dir / "events.jsonl"
    assert events_path.exists()
    assert events_path.read_text().strip() == ""

    assert (run_dir / "state.json").exists()


# ---------------------------------------------------------------------------
# 11. Directory walk processes all matching files
# ---------------------------------------------------------------------------


def test_directory_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    src = tmp_path / "sources"
    for i, subdir in enumerate(["a/run1", "b/run2", "c/d/run3"]):
        dest = src / subdir
        dest.mkdir(parents=True)
        p = dest / "cc_raw_output.jsonl"
        shutil.copy(_SDK_MINIMAL, p)
        # Patch session_id to be unique per file
        lines = p.read_text().splitlines()
        lines[0] = json.dumps({
            **json.loads(lines[0]),
            "session_id": f"walk-{i}",
        })
        p.write_text("\n".join(lines) + "\n")

    rc = tail.run_tail(src, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0

    runs = list((tmp_path / "runs").iterdir())
    assert len(runs) == 3

    out = capsys.readouterr().out
    assert out.count("→") == 3


# ---------------------------------------------------------------------------
# 12. Empty directory prints message
# ---------------------------------------------------------------------------


def test_empty_directory_prints_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    rc = tail.run_tail(empty_dir, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0

    out = capsys.readouterr().out
    assert "no cc_raw_output.jsonl files found" in out


# ---------------------------------------------------------------------------
# 13. --run-id with multi-file directory returns rc=2
# ---------------------------------------------------------------------------


def test_multi_file_with_run_id_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    src = tmp_path / "multi"
    for subdir in ["run1", "run2"]:
        dest = src / subdir
        dest.mkdir(parents=True)
        shutil.copy(_SDK_MINIMAL, dest / "cc_raw_output.jsonl")

    rc = tail.run_tail(src, run_id="conflict", source_name=None, dry_run=False, force=False)
    assert rc == 2

    err = capsys.readouterr().err
    assert "--run-id" in err


# ---------------------------------------------------------------------------
# 14. Unknown record type emitted as event: "unknown"
# ---------------------------------------------------------------------------


def test_unknown_record_type_emitted_as_event_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    rc = tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0

    run_dir = tmp_path / "runs" / "sdk-abc-123"
    events = _read_jsonl(run_dir / "events.jsonl")
    unknown_events = [ev for ev in events if ev.get("event") == "unknown"]
    assert len(unknown_events) >= 1
    # The api_retry record in sdk_minimal.jsonl must land here
    api_retry = [ev for ev in unknown_events if ev.get("original_type") == "system"]
    assert len(api_retry) >= 1


# ---------------------------------------------------------------------------
# 15. Truncated mid-stream (corrupt last line)
# ---------------------------------------------------------------------------


def test_truncated_mid_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    fixture = tmp_path / "cc_raw_output.jsonl"
    good_lines = _SDK_MINIMAL.read_text().rstrip("\n").splitlines()
    # Replace last line with corrupt content
    corrupt_lines = good_lines[:-1] + ["{ this is not valid json !!!"]
    fixture.write_text("\n".join(corrupt_lines) + "\n")

    rc = tail.run_tail(fixture, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0

    run_dir = tmp_path / "runs" / "sdk-abc-123"
    state = _read_json(run_dir / "state.json")
    assert state["truncated"] is True

    # Parseable events (session_start at minimum) were captured
    events = _read_jsonl(run_dir / "events.jsonl")
    event_kinds = [ev["event"] for ev in events]
    assert "session_start" in event_kinds
    # The corrupt line becomes an unknown event
    assert "unknown" in event_kinds


# ---------------------------------------------------------------------------
# 16. No runtime dependency on .adw/ modules
# ---------------------------------------------------------------------------


def test_no_runtime_dep_on_dot_adw() -> None:
    # Remove any adw_modules that might have been loaded by other tests
    to_remove = [k for k in sys.modules if "adw_modules" in k]
    for key in to_remove:
        del sys.modules[key]

    # Re-importing tail must not pull in adw_modules
    import importlib

    importlib.reload(tail)
    assert not any("adw_modules" in m for m in sys.modules)


# ---------------------------------------------------------------------------
# Additional unit-level tests for _derive_run_id and _translate
# ---------------------------------------------------------------------------


def test_derive_run_id_explicit_verbatim(tmp_path: Path) -> None:
    fixture = tmp_path / "cc_raw_output.jsonl"
    fixture.write_text("")
    run_id, used_fallback = tail._derive_run_id(fixture, "my-id", tmp_path)
    assert run_id == "my-id"
    assert used_fallback is False


def test_derive_run_id_from_init_record(tmp_path: Path) -> None:
    fixture = tmp_path / "cc_raw_output.jsonl"
    fixture.write_text(
        '{"type":"system","subtype":"init","session_id":"s99","cwd":"/","model":"m"}\n'
    )
    run_id, used_fallback = tail._derive_run_id(fixture, None, tmp_path)
    assert run_id == "sdk-s99"
    assert used_fallback is False


def test_derive_run_id_fallback_on_missing_init(tmp_path: Path) -> None:
    fixture = tmp_path / "cc_raw_output.jsonl"
    fixture.write_text('{"type":"assistant","session_id":"x"}\n')
    run_id, used_fallback = tail._derive_run_id(fixture, None, tmp_path)
    assert run_id == _sha1_id(fixture)
    assert used_fallback is True


def test_translate_session_start() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    records = [
        {"type": "system", "subtype": "init", "session_id": "s1", "cwd": "/tmp", "model": "m"}
    ]
    events = list(tail._translate(records, run_id="sdk-s1", abs_path="/f.jsonl", now=now))
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "session_start"
    assert ev["cwd"] == "/tmp"
    assert ev["model"] == "m"
    assert ev["schema_version"] == SCHEMA_VERSION
    assert ev["source"] == SOURCE_SDK
    assert ev["sdk_source_file"] == "/f.jsonl"


def test_translate_assistant_text_and_tool_use() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    records = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_use", "id": "t1", "name": "Read", "input": {"f": "x"}},
                    {"type": "thinking", "thinking": "internal"},
                ]
            },
        }
    ]
    events = list(tail._translate(records, run_id="sdk-x", abs_path="/f", now=now))
    assert len(events) == 2
    kinds = [e["event"] for e in events]
    assert "assistant_text" in kinds
    assert "tool_use" in kinds
    # thinking block skipped
    assert all(e["event"] != "thinking" for e in events)

    tool_ev = next(e for e in events if e["event"] == "tool_use")
    assert tool_ev["result_summary"] is None
    assert tool_ev["duration_ms"] is None


def test_translate_user_text_prompt() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    records = [
        {"type": "user", "message": {"content": [{"type": "text", "text": "fix it"}]}}
    ]
    events = list(tail._translate(records, run_id="sdk-x", abs_path="/f", now=now))
    assert len(events) == 1
    assert events[0]["event"] == "prompt"
    assert events[0]["text"] == "fix it"


def test_translate_user_tool_result_only_skipped() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    records = [
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "data"}]
            },
        }
    ]
    events = list(tail._translate(records, run_id="sdk-x", abs_path="/f", now=now))
    assert len(events) == 0


def test_translate_result_stop_event() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    records = [
        {
            "type": "result",
            "subtype": "success",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_read_input_tokens": 5,
                "cache_creation_input_tokens": 1,
            },
            "duration_ms": 1234,
            "total_cost_usd": 0.001,
            "is_error": False,
        }
    ]
    events = list(tail._translate(records, run_id="sdk-x", abs_path="/f", now=now))
    assert len(events) == 1
    ev = events[0]
    assert ev["event"] == "stop"
    assert ev["usage"]["input_tokens"] == 10
    assert ev["usage"]["output_tokens"] == 20
    assert ev["usage"]["cache_read_tokens"] == 5
    assert ev["usage"]["cache_creation_tokens"] == 1
    assert ev["is_error"] is False


def test_translate_unknown_record() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    records = [{"type": "rate_limit_event", "info": "some data"}]
    events = list(tail._translate(records, run_id="sdk-x", abs_path="/f", now=now))
    assert len(events) == 1
    assert events[0]["event"] == "unknown"
    assert "raw" in events[0]


def test_truncate_behavior_in_tail() -> None:
    clipped, dropped = tail._truncate("hello", 10)
    assert clipped == "hello"
    assert dropped == 0

    clipped2, dropped2 = tail._truncate("hello", 3)
    assert dropped2 > 0
    assert len(clipped2.encode("utf-8")) <= 3


# ---------------------------------------------------------------------------
# CLI integration smoke
# ---------------------------------------------------------------------------


def test_cli_tail_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = main(["tail", str(_SDK_MINIMAL)])
    assert rc == 0
    assert (tmp_path / "runs" / "sdk-abc-123").is_dir()


def test_cli_tail_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = main(["tail", "--dry-run", str(_SDK_MINIMAL)])
    assert rc == 0
    assert not (tmp_path / "runs").exists()
    assert "would write" in capsys.readouterr().out


def test_cli_tail_missing_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = main(["tail", str(tmp_path / "no_such_file.jsonl")])
    assert rc == 2


def test_schema_version_on_every_sdk_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=False, force=False)

    run_dir = tmp_path / "runs" / "sdk-abc-123"
    events = _read_jsonl(run_dir / "events.jsonl")
    for ev in events:
        assert ev.get("schema_version") == SCHEMA_VERSION, f"missing schema_version in {ev}"


def test_cost_json_accumulates_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=False, force=False)

    run_dir = tmp_path / "runs" / "sdk-abc-123"
    cost = _read_json(run_dir / "cost.json")
    assert cost["schema_version"] == SCHEMA_VERSION
    totals = cost["totals"]
    assert totals["input_tokens"] == 10
    assert totals["output_tokens"] == 20
    assert totals["cache_read_tokens"] == 5
    assert totals["cache_creation_tokens"] == 1


def test_run_id_with_single_file_in_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    src = tmp_path / "single"
    src.mkdir()
    shutil.copy(_SDK_MINIMAL, src / "cc_raw_output.jsonl")

    rc = tail.run_tail(src, run_id="custom", source_name=None, dry_run=False, force=False)
    assert rc == 0
    assert (tmp_path / "runs" / "custom").is_dir()


def _write_fixture(path: Path, session_id: str) -> None:
    """Write a minimal sdk fixture with the given session_id."""
    lines = _SDK_MINIMAL.read_text().splitlines()
    first = {**json.loads(lines[0]), "session_id": session_id}
    out = [json.dumps(first)] + [
        line.replace('"session_id":"abc-123"', f'"session_id":"{session_id}"')
        for line in lines[1:]
    ]
    path.write_text("\n".join(out) + "\n")


def test_is_already_ingested_false_before_ingest(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "test-id"
    assert tail._is_already_ingested(run_dir) is False


def test_is_already_ingested_true_after_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    tail.run_tail(_SDK_MINIMAL, run_id=None, source_name=None, dry_run=False, force=False)
    run_dir = tmp_path / "runs" / "sdk-abc-123"
    assert tail._is_already_ingested(run_dir) is True


def _make_iter(records: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    yield from records


# ---------------------------------------------------------------------------
# Regression: timestamps are derived from session window, NOT ingestion-now
# (per Lesson #1 — sort-key direction. The timeline must be monotonic AND
# anchored to the real session, not the wall-clock at tail time.)
# ---------------------------------------------------------------------------


def test_tail_timestamps_anchored_to_session_window_not_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """state.ended_at should come from file mtime (when session actually
    ended), not from datetime.now() at tail invocation time. state.started_at
    should be back-derived from end - duration_ms (1234 ms in the fixture).
    Every event timestamp must lie within [started_at, ended_at]."""
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    # Move the fixture into a known-mtime location.
    target = tmp_path / "fixture.jsonl"
    target.write_text(_SDK_MINIMAL.read_text())
    # Set mtime to a specific moment far in the past.
    import os as _os
    fixed_end = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    _os.utime(target, (fixed_end.timestamp(), fixed_end.timestamp()))

    rc = tail.run_tail(target, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0

    run_dir = tmp_path / "runs" / "sdk-abc-123"
    state = json.loads((run_dir / "state.json").read_text())

    # End should be the fixture's mtime, not 'now'.
    ended = datetime.fromisoformat(state["ended_at"])
    assert ended == fixed_end, f"expected ended_at={fixed_end}, got {ended}"

    # Start should be back-derived from duration_ms=1234.
    started = datetime.fromisoformat(state["started_at"])
    expected_start = fixed_end - timedelta(milliseconds=1234)
    assert started == expected_start, f"expected started_at={expected_start}, got {started}"

    # All event timestamps must fall within [started, ended] and be monotonic.
    events = [
        json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines() if line.strip()
    ]
    prev_ts = started - timedelta(seconds=1)
    for ev in events:
        ts = datetime.fromisoformat(ev["timestamp"])
        assert started <= ts <= ended, f"event ts {ts} outside window [{started}, {ended}]"
        assert ts >= prev_ts, "events out of monotonic order"
        prev_ts = ts


def test_tail_timestamps_monotonic_without_result_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the stream is truncated (no result record), we don't have an
    authoritative duration_ms. Fall back to a synthetic 1-second-per-event
    window so timestamps are still distinguishable and monotonic — NOT all
    stamped with the same value."""
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    truncated = tmp_path / "truncated.jsonl"
    # init + 3 tool_use events; no result record
    truncated.write_text(
        '{"type":"system","subtype":"init","session_id":"trunc-1","cwd":"/tmp","model":"claude-opus-4-7"}\n'
        '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"/a"}}]},"session_id":"trunc-1"}\n'
        '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t2","name":"Read","input":{"file_path":"/b"}}]},"session_id":"trunc-1"}\n'
        '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t3","name":"Read","input":{"file_path":"/c"}}]},"session_id":"trunc-1"}\n'
    )
    rc = tail.run_tail(truncated, run_id=None, source_name=None, dry_run=False, force=False)
    assert rc == 0

    run_dir = tmp_path / "runs" / "sdk-trunc-1"
    events = [
        json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines() if line.strip()
    ]
    timestamps = [datetime.fromisoformat(e["timestamp"]) for e in events]
    # All distinct (not the same wall-clock moment).
    assert len(set(timestamps)) == len(timestamps), "timestamps collapsed to single value"
    # Strictly monotonic.
    for a, b in zip(timestamps, timestamps[1:], strict=False):
        assert a < b, f"non-monotonic: {a} >= {b}"
