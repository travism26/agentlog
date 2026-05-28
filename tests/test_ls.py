"""Tests for src/agentlog/ls.py."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agentlog import capture, ls, tail
from agentlog._constants import INDEX_FILE_NAME, RUNS_DIR_NAME
from agentlog.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"
_SDK_MINIMAL = _FIXTURES / "sdk_minimal.jsonl"


def _seed_sdk_run(
    tmp_path: Path,
    run_id: str,
    *,
    session_id: str | None = None,
) -> None:
    """Ingest sdk_minimal.jsonl with an explicit run_id into tmp_path."""
    if session_id is not None:
        # Rewrite the fixture with a different session_id so run dirs don't collide.
        fixture = tmp_path / f"cc_{run_id}.jsonl"
        lines = _SDK_MINIMAL.read_text().splitlines()
        first = {**json.loads(lines[0]), "session_id": session_id}
        out = [json.dumps(first)] + [
            line.replace('"session_id":"abc-123"', f'"session_id":"{session_id}"')
            for line in lines[1:]
        ]
        fixture.write_text("\n".join(out) + "\n")
        tail.run_tail(fixture, run_id=run_id, source_name=None, dry_run=False, force=True)
    else:
        tail.run_tail(
            _SDK_MINIMAL, run_id=run_id, source_name=None, dry_run=False, force=True
        )


def _seed_hooks_run(
    tmp_path: Path,
    session_id: str,
    *,
    started_offset_sec: int = 0,
) -> None:
    """Create a minimal hooks-mode run using capture.dispatch."""
    now = datetime.now(UTC) - timedelta(seconds=started_offset_sec)
    payload: dict[str, Any] = {"session_id": session_id, "cwd": "/tmp", "model": "claude-test"}
    capture.dispatch("SessionStart", payload, now=now)
    capture.dispatch(
        "Stop",
        {
            "session_id": session_id,
            "usage": {
                "input_tokens": 5,
                "output_tokens": 5,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            },
        },
        now=now + timedelta(seconds=5),
    )
    capture.dispatch(
        "SessionEnd",
        {"session_id": session_id, "summary": "done"},
        now=now + timedelta(seconds=6),
    )


# ---------------------------------------------------------------------------
# 1. Empty runs dir prints message and exits zero
# ---------------------------------------------------------------------------


def test_empty_runs_dir_prints_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "no runs found" in out
    assert not (tmp_path / INDEX_FILE_NAME).exists()


# ---------------------------------------------------------------------------
# 2. Populated dir — default sort started desc
# ---------------------------------------------------------------------------


def test_populated_dir_default_sort_started_desc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "run-a")
    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "run-a" in out
    assert "sdk" in out


# ---------------------------------------------------------------------------
# 3. --sort tokens --reverse puts lowest-token run first
# ---------------------------------------------------------------------------


def test_sort_tokens_reverse_puts_lowest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "run-high")
    _seed_hooks_run(tmp_path, "hooks-low")
    capsys.readouterr()  # consume tail/capture output before ls

    rc = ls.run_ls(
        source="all", since=None, sort_key="tokens", reverse=True, limit=50,
        as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    # hooks-low has 10 tokens; sdk run has 36 — hooks-low should appear first
    hooks_pos = out.find("hooks-low")
    sdk_pos = out.find("run-high")
    assert hooks_pos < sdk_pos


# ---------------------------------------------------------------------------
# 4. --source sdk shows only SDK runs
# ---------------------------------------------------------------------------


def test_source_filter_sdk_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "sdk-run-1")
    _seed_hooks_run(tmp_path, "hooks-run-1")

    rc = ls.run_ls(
        source="sdk", since=None, sort_key="started", reverse=False, limit=50,
        as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "sdk-run-1" in out
    assert "hooks-run-1" not in out


# ---------------------------------------------------------------------------
# 5. --source hooks shows only hooks runs
# ---------------------------------------------------------------------------


def test_source_filter_hooks_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "sdk-run-2")
    _seed_hooks_run(tmp_path, "hooks-run-2")
    capsys.readouterr()

    rc = ls.run_ls(
        source="hooks", since=None, sort_key="started", reverse=False, limit=50,
        as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "hooks-run-2" in out
    assert "sdk-run-2" not in out


# ---------------------------------------------------------------------------
# 6. --source all (default) shows both
# ---------------------------------------------------------------------------


def test_source_filter_all_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "sdk-run-all")
    _seed_hooks_run(tmp_path, "hooks-run-all")

    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "sdk-run-all" in out
    assert "hooks-run-all" in out


# ---------------------------------------------------------------------------
# 7. --since 1h filters to recent runs
# ---------------------------------------------------------------------------


def test_since_filter_1h_only_recent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    # Seed a recent run (now) and an old run (started 2h ago)
    _seed_sdk_run(tmp_path, "recent-run")

    # Manually write an old run with started_at 2 hours ago
    old_run_dir = tmp_path / RUNS_DIR_NAME / "old-run"
    old_run_dir.mkdir(parents=True)
    old_started = datetime.now(UTC) - timedelta(hours=2)
    state: dict[str, Any] = {
        "schema_version": 1,
        "session_id": "old-run",
        "source": "sdk",
        "started_at": old_started.isoformat(),
        "ended_at": old_started.isoformat(),
        "event_count": 1,
        "model": "test",
    }
    (old_run_dir / "state.json").write_text(json.dumps(state))

    rc = ls.run_ls(
        source="all", since=timedelta(hours=1), sort_key="started", reverse=False,
        limit=50, as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "recent-run" in out
    assert "old-run" not in out


# ---------------------------------------------------------------------------
# 8. --since 7d includes week-old runs
# ---------------------------------------------------------------------------


def test_since_filter_7d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "week-run")

    rc = ls.run_ls(
        source="all", since=timedelta(days=7), sort_key="started", reverse=False,
        limit=50, as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "week-run" in out


# ---------------------------------------------------------------------------
# 9. --since invalid exits 2
# ---------------------------------------------------------------------------


def test_since_invalid_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        main(["ls", "--since", "garbage"])
    assert exc.value.code == 2


# ---------------------------------------------------------------------------
# 10. --json output round-trips
# ---------------------------------------------------------------------------


def test_json_output_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "json-run")
    capsys.readouterr()

    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    row = parsed[0]
    assert row["run_id"] == "json-run"
    assert "duration" in row
    assert "started_at" in row


# ---------------------------------------------------------------------------
# 11. --reindex rebuilds table
# ---------------------------------------------------------------------------


def test_reindex_rebuilds_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "reindex-run")

    # First ls populates the index
    ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=False, reindex=False,
    )
    capsys.readouterr()

    # Corrupt the row directly via sqlite3
    index_path = tmp_path / INDEX_FILE_NAME
    conn = sqlite3.connect(str(index_path))
    conn.execute("UPDATE runs SET event_count = 9999 WHERE run_id = 'reindex-run'")
    conn.commit()
    conn.close()

    # --reindex should restore the correct value
    ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=True,
    )
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert len(parsed) == 1
    assert parsed[0]["event_count"] != 9999


# ---------------------------------------------------------------------------
# 12. --limit caps rows
# ---------------------------------------------------------------------------


def test_limit_caps_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    for i in range(5):
        _seed_sdk_run(tmp_path, f"limit-run-{i}", session_id=f"sess-{i}")
    capsys.readouterr()

    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=3,
        as_json=True, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert len(parsed) == 3


# ---------------------------------------------------------------------------
# 13. Idempotent refresh does not re-index unchanged runs
# ---------------------------------------------------------------------------


def test_idempotent_refresh_does_not_reindex_unchanged_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "idempotent-run")

    call_count = 0
    real_index_run = ls._index_run

    def spy_index_run(*args: Any, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        return real_index_run(*args, **kwargs)

    with patch.object(ls, "_index_run", side_effect=spy_index_run):
        # First call should index the run
        ls.run_ls(
            source="all", since=None, sort_key="started", reverse=False, limit=50,
            as_json=False, reindex=False,
        )
        count_after_first = call_count

        # Second call with unchanged files should not call _index_run again
        ls.run_ls(
            source="all", since=None, sort_key="started", reverse=False, limit=50,
            as_json=False, reindex=False,
        )
        count_after_second = call_count

    assert count_after_first >= 1
    assert count_after_second == count_after_first


# ---------------------------------------------------------------------------
# 14. Index file created at expected path
# ---------------------------------------------------------------------------


def test_index_file_created_at_expected_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "path-test")
    ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=False, reindex=False,
    )
    assert (tmp_path / INDEX_FILE_NAME).exists()


# ---------------------------------------------------------------------------
# 15. Malformed state.json skipped with warning
# ---------------------------------------------------------------------------


def test_malformed_state_json_skipped_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "good-run")

    # Write a run dir with malformed state.json
    bad_dir = tmp_path / RUNS_DIR_NAME / "bad-run"
    bad_dir.mkdir(parents=True)
    (bad_dir / "state.json").write_text("{ not valid json !!!")

    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=False,
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "warning" in err.lower() or "skipped" in err.lower() or "bad-run" in err
    # good-run should still be listed
    rc2 = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=False,
    )
    assert rc2 == 0
    parsed = json.loads(capsys.readouterr().out)
    run_ids = [r["run_id"] for r in parsed]
    assert "good-run" in run_ids
    assert "bad-run" not in run_ids


# ---------------------------------------------------------------------------
# 16. Missing cost.json yields zero tokens
# ---------------------------------------------------------------------------


def test_missing_cost_json_yields_zero_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    # Write a run dir with only state.json, no cost.json
    run_dir = tmp_path / RUNS_DIR_NAME / "no-cost-run"
    run_dir.mkdir(parents=True)
    state: dict[str, Any] = {
        "schema_version": 1,
        "session_id": "no-cost-run",
        "source": "sdk",
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
        "event_count": 2,
        "model": "test",
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=False,
    )
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    row = next(r for r in parsed if r["run_id"] == "no-cost-run")
    assert row["total_tokens"] == 0


# ---------------------------------------------------------------------------
# 17. Future schema version drops and rebuilds runs table
# ---------------------------------------------------------------------------


def test_future_schema_version_drops_and_rebuilds_runs_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "schema-test")

    # First invocation creates the index normally
    ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=False, reindex=False,
    )
    capsys.readouterr()

    # Manually set schema_version to a future value
    index_path = tmp_path / INDEX_FILE_NAME
    conn = sqlite3.connect(str(index_path))
    conn.execute("UPDATE schema_version SET version = 999")
    # Add a sentinel table that should NOT be dropped
    conn.execute("CREATE TABLE IF NOT EXISTS future_feature (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO future_feature VALUES (42)")
    conn.commit()
    conn.close()

    # ls should detect the mismatch, drop runs + version row, and rebuild cleanly
    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    run_ids = [r["run_id"] for r in parsed]
    assert "schema-test" in run_ids

    # Sentinel table should still exist
    conn2 = sqlite3.connect(str(index_path))
    row = conn2.execute("SELECT id FROM future_feature LIMIT 1").fetchone()
    conn2.close()
    assert row is not None
    assert row[0] == 42


# ---------------------------------------------------------------------------
# 18. Missing runs dir does not create index file
# ---------------------------------------------------------------------------


def test_missing_runs_dir_does_not_create_index_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    # No runs dir seeded
    ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=False, reindex=False,
    )
    assert not (tmp_path / INDEX_FILE_NAME).exists()


# ---------------------------------------------------------------------------
# 19. Duration format human-readable unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start, end, expected",
    [
        (None, "2026-01-01T00:00:00+00:00", "-"),
        ("2026-01-01T00:00:00+00:00", None, "-"),
        ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:05+00:00", "5s"),
        ("2026-01-01T00:00:00+00:00", "2026-01-01T00:08:41+00:00", "8m41s"),
        ("2026-01-01T00:00:00+00:00", "2026-01-01T03:14:00+00:00", "3h14m"),
        ("2026-01-01T00:00:00+00:00", "2026-01-04T02:00:00+00:00", "3d2h"),
        ("2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00", "1m"),
    ],
)
def test_duration_format_human_readable(
    start: str | None, end: str | None, expected: str
) -> None:
    assert ls._format_duration(start, end) == expected


# ---------------------------------------------------------------------------
# 20. Parse duration accepts valid suffixes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected_seconds",
    [
        ("30s", 30),
        ("30S", 30),
        ("5m", 300),
        ("2h", 7200),
        ("1d", 86400),
        ("1w", 604800),
        ("7d", 7 * 86400),
    ],
)
def test_parse_duration_accepts_valid_suffixes(text: str, expected_seconds: int) -> None:
    td = ls._parse_duration(text)
    assert td.total_seconds() == expected_seconds


@pytest.mark.parametrize(
    "text",
    ["garbage", "0m", "-1h", "1x", "1.5h", "", "m", "10"],
)
def test_parse_duration_rejects_invalid(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):  # noqa: F821
        ls._parse_duration(text)


# ---------------------------------------------------------------------------
# 21. SORT_COLUMN_MAP keys match argparse choices
# ---------------------------------------------------------------------------


def test_sort_column_map_matches_argparse_choices() -> None:
    from agentlog.cli import build_parser

    parser = build_parser()
    ls_parser = parser._subparsers._group_actions[0].choices["ls"]  # type: ignore[union-attr,index]
    sort_action = next(a for a in ls_parser._actions if getattr(a, "dest", None) == "sort_key")
    argparse_choices = set(sort_action.choices)
    map_keys = set(ls.SORT_COLUMN_MAP.keys())
    assert argparse_choices == map_keys


# ---------------------------------------------------------------------------
# 22. CLI integration smoke — ls subparser wired correctly
# ---------------------------------------------------------------------------


def test_cli_ls_empty_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = main(["ls"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no runs found" in out


def test_cli_ls_populated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "cli-smoke-run")
    rc = main(["ls"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cli-smoke-run" in out


def test_cli_ls_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["ls", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--source" in out
    assert "--since" in out
    assert "--sort" in out
    assert "--json" in out


def test_cli_ls_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "cli-json-run")
    capsys.readouterr()
    rc = main(["ls", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed[0]["run_id"] == "cli-json-run"


# ---------------------------------------------------------------------------
# 23. Two-source mixed: 2 SDK + 2 hooks, default ls shows all 4
# ---------------------------------------------------------------------------


def test_two_source_mixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "mix-sdk-1", session_id="mix-s1")
    _seed_sdk_run(tmp_path, "mix-sdk-2", session_id="mix-s2")
    _seed_hooks_run(tmp_path, "mix-hooks-1")
    _seed_hooks_run(tmp_path, "mix-hooks-2")
    capsys.readouterr()

    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=False,
    )
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    run_ids = {r["run_id"] for r in parsed}
    assert {"mix-sdk-1", "mix-sdk-2", "mix-hooks-1", "mix-hooks-2"}.issubset(run_ids)

    # --source sdk shows only 2
    rc2 = ls.run_ls(
        source="sdk", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=False,
    )
    assert rc2 == 0
    sdk_parsed = json.loads(capsys.readouterr().out)
    assert all(r["source"] == "sdk" for r in sdk_parsed)
    sdk_ids = {r["run_id"] for r in sdk_parsed}
    assert "mix-sdk-1" in sdk_ids
    assert "mix-sdk-2" in sdk_ids

    # --source hooks shows only 2
    rc3 = ls.run_ls(
        source="hooks", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=False,
    )
    assert rc3 == 0
    hooks_parsed = json.loads(capsys.readouterr().out)
    assert all(r["source"] == "hooks" for r in hooks_parsed)
    hooks_ids = {r["run_id"] for r in hooks_parsed}
    assert "mix-hooks-1" in hooks_ids
    assert "mix-hooks-2" in hooks_ids


# ---------------------------------------------------------------------------
# 24. Run dir missing state.json silently skipped
# ---------------------------------------------------------------------------


def test_run_dir_missing_state_json_silently_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    # Create a dir without state.json (simulates crashed run)
    incomplete = tmp_path / RUNS_DIR_NAME / "incomplete-run"
    incomplete.mkdir(parents=True)
    (incomplete / "events.jsonl").touch()

    _seed_sdk_run(tmp_path, "complete-run")
    capsys.readouterr()

    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False, limit=50,
        as_json=True, reindex=False,
    )
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    run_ids = [r["run_id"] for r in parsed]
    assert "complete-run" in run_ids
    assert "incomplete-run" not in run_ids


# ---------------------------------------------------------------------------
# 25. Plain formatter tokens shows 0 not dash, run_id never truncated
# ---------------------------------------------------------------------------


def test_format_plain_tokens_zero_and_run_id_present() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE runs (
            run_id TEXT, source TEXT, session_id TEXT, parent_session_id TEXT,
            started_at TEXT, ended_at TEXT, cwd TEXT, model TEXT,
            event_count INTEGER, total_tokens INTEGER, state_mtime REAL,
            cost_mtime REAL, indexed_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "a-very-long-run-id-that-should-not-be-truncated",
            "sdk", None, None,
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00",
            None, None, 0, 0, 0.0, 0.0, None,
        ),
    )
    rows = conn.execute("SELECT * FROM runs").fetchall()
    output = ls._format_plain(rows)
    assert "a-very-long-run-id-that-should-not-be-truncated" in output
    assert "0" in output  # zero tokens shown, not dash
    conn.close()


# ---------------------------------------------------------------------------
# Issue #2 regression: live sessions show real event_count
# ---------------------------------------------------------------------------


def _seed_live_run(runs_root: Path, run_id: str, event_count_on_disk: int) -> Path:
    """Seed a run with state.json::ended_at=null and N lines in events.jsonl.
    Simulates a session that's been active but hasn't fired SessionEnd yet."""
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps({
        "schema_version": 1,
        "session_id": run_id,
        "source": "hooks",
        "started_at": "2026-05-28T10:00:00+00:00",
        "ended_at": None,
        "cwd": "/tmp",
        "model": "claude-opus-4-7",
        # event_count is the LIES value — 0 in real Claude Code captures until
        # SessionEnd fires. The test asserts ls overrides this with the
        # actual on-disk count.
        "event_count": 0,
    }))
    (run_dir / "events.jsonl").write_text(
        "".join(
            json.dumps({"event": "x", "i": i}) + "\n" for i in range(event_count_on_disk)
        )
    )
    return run_dir


def test_ls_overrides_event_count_from_events_jsonl_for_live_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The headline behaviour change for issue #2: a live run (ended_at NULL)
    whose state.json says `event_count: 0` but whose events.jsonl has 5 lines
    must be displayed as 5, not 0."""
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    runs_root = tmp_path / "runs"
    _seed_live_run(runs_root, "live-session-001", event_count_on_disk=5)

    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False,
        limit=10, as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    # Find the live-session row; the events column must read 5, not 0.
    matching = [line for line in out.splitlines() if "live-session-001" in line]
    assert matching, f"live-session row missing from output:\n{out}"
    assert " 5 " in matching[0] or matching[0].rstrip().endswith(" 5"), (
        f"expected event count 5 in row, got: {matching[0]!r}"
    )


def test_ls_uses_state_event_count_for_finalised_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inverse check: for a FINALISED run (ended_at present), state.json's
    event_count is authoritative — don't second-guess it by re-counting
    events.jsonl, since SessionEnd already did the math."""
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "finalised-session-002"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "schema_version": 1, "session_id": "finalised-session-002", "source": "hooks",
        "started_at": "2026-05-28T10:00:00+00:00",
        "ended_at": "2026-05-28T10:10:00+00:00",  # finalised
        "cwd": "/tmp", "model": "claude-opus-4-7",
        "event_count": 42,  # the authoritative number
    }))
    # Seed events.jsonl with a DIFFERENT count — state.json must win.
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps({"i": i}) + "\n" for i in range(7))
    )
    rc = ls.run_ls(
        source="all", since=None, sort_key="started", reverse=False,
        limit=10, as_json=False, reindex=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    matching = [line for line in out.splitlines() if "finalised-session-002" in line]
    assert matching
    assert " 42 " in matching[0] or matching[0].rstrip().endswith(" 42")


def test_ls_re_runs_index_for_live_session_when_events_grow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Skip-on-mtime-match must NOT apply to live runs. After ls indexes a
    live run, appending to events.jsonl (without touching state.json) must
    cause the NEXT ls invocation to refresh the count, not return the cached
    stale value. Regression for the mtime-skip bug in issue #2."""
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    runs_root = tmp_path / "runs"
    run_dir = _seed_live_run(runs_root, "growing-session-003", event_count_on_disk=2)

    # First ls — should show 2.
    ls.run_ls(source="all", since=None, sort_key="started", reverse=False,
              limit=10, as_json=False, reindex=False)
    capsys.readouterr()  # discard

    # Append 3 more events; do NOT touch state.json (simulating live capture).
    with (run_dir / "events.jsonl").open("a") as f:
        for i in range(3):
            f.write(json.dumps({"event": "x", "i": 100 + i}) + "\n")

    # Second ls — must reflect the new total (5), not the cached 2.
    rc = ls.run_ls(source="all", since=None, sort_key="started", reverse=False,
                   limit=10, as_json=False, reindex=False)
    assert rc == 0
    out = capsys.readouterr().out
    matching = [line for line in out.splitlines() if "growing-session-003" in line]
    assert matching
    assert " 5 " in matching[0] or matching[0].rstrip().endswith(" 5"), (
        f"expected refreshed count 5, got: {matching[0]!r}"
    )
