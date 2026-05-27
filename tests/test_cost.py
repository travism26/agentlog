"""Tests for src/agentlog/cost.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentlog import capture, cost, tail
from agentlog._constants import RUNS_DIR_NAME
from agentlog.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"
_SDK_MINIMAL = _FIXTURES / "sdk_minimal.jsonl"

# Tokens produced by sdk_minimal.jsonl after tail ingestion:
#   input=10, output=20, cache_read=5, cache_creation=1, total=36
# Model: claude-opus-4-7
_SDK_MODEL = "claude-opus-4-7"
_SDK_INPUT_TOKENS = 10
_SDK_OUTPUT_TOKENS = 20
_SDK_CACHE_READ_TOKENS = 5
_SDK_CACHE_CREATION_TOKENS = 1
_SDK_TOTAL_TOKENS = _SDK_INPUT_TOKENS + _SDK_OUTPUT_TOKENS + _SDK_CACHE_READ_TOKENS + _SDK_CACHE_CREATION_TOKENS


def _seed_sdk_run(
    tmp_path: Path,
    run_id: str,
    *,
    session_id: str | None = None,
) -> None:
    """Ingest sdk_minimal.jsonl with an explicit run_id into tmp_path."""
    if session_id is not None:
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
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 5,
    output_tokens: int = 5,
) -> None:
    """Create a minimal hooks-mode run using capture.dispatch."""
    now = datetime.now(UTC) - timedelta(seconds=started_offset_sec)
    payload: dict[str, Any] = {"session_id": session_id, "cwd": "/tmp", "model": model}
    capture.dispatch("SessionStart", payload, now=now)
    capture.dispatch(
        "Stop",
        {
            "session_id": session_id,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
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


def _seed_unknown_model_run(
    tmp_path: Path,
    run_id: str,
    *,
    model: str | None = "unknown-model-xyz",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> None:
    """Write a hand-rolled state.json + cost.json with a model not in the pricing table."""
    run_dir = tmp_path / RUNS_DIR_NAME / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {
        "schema_version": 1,
        "session_id": run_id,
        "source": "hooks",
        "model": model,
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
        "event_count": 2,
        "cwd": "/tmp",
    }
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
    (run_dir / "state.json").write_text(json.dumps(state))
    (run_dir / "cost.json").write_text(json.dumps(cost_data))


def _seed_run_without_cost_json(tmp_path: Path, run_id: str) -> None:
    """Write only a state.json (simulates a run that crashed before Stop fired)."""
    run_dir = tmp_path / RUNS_DIR_NAME / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {
        "schema_version": 1,
        "session_id": run_id,
        "source": "sdk",
        "model": _SDK_MODEL,
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": None,
        "event_count": 1,
        "cwd": "/tmp",
    }
    (run_dir / "state.json").write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# Pricing resolution tests
# ---------------------------------------------------------------------------


def test_pricing_flag_overrides_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "price-override-run")
    capsys.readouterr()

    custom_pricing = {
        _SDK_MODEL: {
            "input": 999.0,
            "output": 999.0,
            "cache_read": 999.0,
            "cache_creation": 999.0,
        }
    }
    pricing_file = tmp_path / "custom_pricing.json"
    pricing_file.write_text(json.dumps(custom_pricing))

    rc = cost.run_cost(
        run_id="price-override-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=pricing_file,
        as_json=True,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["rates_per_million_usd"]["input"] == 999.0
    assert data["cost_usd"] is not None
    assert data["cost_usd"] > 0


def test_pricing_merge_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    # User file overrides only one model; others should inherit from builtin.
    user_table = {
        "claude-haiku-4-5": {
            "input": 0.01,
            "output": 0.02,
            "cache_read": 0.001,
            "cache_creation": 0.003,
        }
    }
    pricing_file = tmp_path / "partial_pricing.json"
    pricing_file.write_text(json.dumps(user_table))

    root = tmp_path
    merged, tag = cost._resolve_pricing(pricing_file, root)

    # Haiku should use user values.
    assert merged["claude-haiku-4-5"]["input"] == 0.01
    # Opus should be inherited from builtin.
    assert merged[_SDK_MODEL]["input"] == cost.BUILTIN_PRICING_PER_MILLION[_SDK_MODEL]["input"]
    assert tag.startswith("file:")


def test_pricing_custom_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    user_table = {
        "my-custom-model": {
            "input": 1.00,
            "output": 5.00,
            "cache_read": 0.10,
            "cache_creation": 1.25,
        }
    }
    pricing_file = tmp_path / "custom.json"
    pricing_file.write_text(json.dumps(user_table))

    root = tmp_path
    merged, _ = cost._resolve_pricing(pricing_file, root)

    assert "my-custom-model" in merged
    assert merged["my-custom-model"]["input"] == 1.00
    # Builtin models still present.
    assert _SDK_MODEL in merged


def test_pricing_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    custom_pricing = {_SDK_MODEL: {"input": 777.0, "output": 777.0, "cache_read": 0.0, "cache_creation": 0.0}}
    pricing_file = tmp_path / "env_pricing.json"
    pricing_file.write_text(json.dumps(custom_pricing))
    monkeypatch.setenv("AGENTLOG_PRICING", str(pricing_file))

    _seed_sdk_run(tmp_path, "env-price-run")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id="env-price-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=True,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["rates_per_million_usd"]["input"] == 777.0
    assert data["pricing_source"].startswith("file:")


def test_pricing_home_file_autodiscovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    custom_pricing = {_SDK_MODEL: {"input": 555.0, "output": 555.0, "cache_read": 0.0, "cache_creation": 0.0}}
    home_pricing_file = tmp_path / "pricing.json"
    home_pricing_file.write_text(json.dumps(custom_pricing))

    _seed_sdk_run(tmp_path, "home-price-run")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id="home-price-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=True,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["rates_per_million_usd"]["input"] == 555.0
    assert data["pricing_source"].startswith("file:")


def test_pricing_missing_path_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "price-missing-run")
    capsys.readouterr()

    nonexistent = tmp_path / "nonexistent_pricing.json"
    rc = cost.run_cost(
        run_id="price-missing-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=nonexistent,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "pricing file not found" in err
    assert str(nonexistent) in err


def test_pricing_invalid_json_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "price-invalid-run")
    capsys.readouterr()

    bad_file = tmp_path / "bad_pricing.json"
    bad_file.write_text("{ not valid json !!!")

    rc = cost.run_cost(
        run_id="price-invalid-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=bad_file,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid JSON in pricing file" in err


def test_pricing_missing_kind_uses_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    # Only provide three of the four kinds.
    partial_model = {
        _SDK_MODEL: {
            "input": 1.0,
            "output": 2.0,
            "cache_read": 0.1,
            # "cache_creation" missing
        }
    }
    pricing_file = tmp_path / "partial_kinds.json"
    pricing_file.write_text(json.dumps(partial_model))

    root = tmp_path
    loaded = cost._load_pricing_file(pricing_file, root)
    assert loaded[_SDK_MODEL]["cache_creation"] == 0.0
    assert loaded[_SDK_MODEL]["input"] == 1.0

    # Check that a log entry was written.
    log_file = tmp_path / "_self.log"
    assert log_file.exists()
    log_content = log_file.read_text()
    assert "cache_creation" in log_content


def test_pricing_negative_uses_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    negative_model = {
        _SDK_MODEL: {
            "input": -5.0,
            "output": 2.0,
            "cache_read": 0.1,
            "cache_creation": 0.2,
        }
    }
    pricing_file = tmp_path / "negative_pricing.json"
    pricing_file.write_text(json.dumps(negative_model))

    root = tmp_path
    loaded = cost._load_pricing_file(pricing_file, root)
    assert loaded[_SDK_MODEL]["input"] == 0.0

    log_file = tmp_path / "_self.log"
    assert log_file.exists()
    log_content = log_file.read_text()
    assert "negative" in log_content.lower() or "input" in log_content


# ---------------------------------------------------------------------------
# Computation tests
# ---------------------------------------------------------------------------


def test_single_run_plain_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "plain-out-run")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id="plain-out-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out

    assert "Run:" in out
    assert "plain-out-run" in out
    assert "Source:" in out
    assert "Model:" in out
    assert _SDK_MODEL in out
    assert "Started:" in out
    assert "Duration:" in out
    assert "Tokens" in out
    assert "Rate" in out
    assert "Cost" in out
    assert "Input" in out
    assert "Output" in out
    assert "Cache read" in out
    assert "Cache create" in out
    assert "Total" in out
    assert "$" in out


def test_single_run_json_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "json-round-run")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id="json-round-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=True,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)

    assert data["run_id"] == "json-round-run"
    assert data["model"] == _SDK_MODEL
    assert data["source"] == "sdk"
    assert "started_at" in data
    assert "ended_at" in data
    assert "duration_seconds" in data
    assert "tokens" in data
    assert data["tokens"]["input"] == _SDK_INPUT_TOKENS
    assert data["tokens"]["output"] == _SDK_OUTPUT_TOKENS
    assert data["tokens"]["cache_read"] == _SDK_CACHE_READ_TOKENS
    assert data["tokens"]["cache_creation"] == _SDK_CACHE_CREATION_TOKENS
    assert data["tokens"]["total"] == _SDK_TOTAL_TOKENS
    assert "cost_usd" in data
    assert data["cost_usd"] is not None
    assert data["cost_usd"] > 0
    assert "rates_per_million_usd" in data
    assert "costs_usd" in data
    assert data["pricing_source"] == "builtin"


def test_zero_usage_zero_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    run_dir = tmp_path / RUNS_DIR_NAME / "zero-run"
    run_dir.mkdir(parents=True)
    state: dict[str, Any] = {
        "schema_version": 1,
        "session_id": "zero-run",
        "source": "sdk",
        "model": _SDK_MODEL,
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        "event_count": 0,
        "cwd": "/tmp",
    }
    cost_data: dict[str, Any] = {
        "schema_version": 1,
        "session_id": "zero-run",
        "totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
        "phases": {},
    }
    (run_dir / "state.json").write_text(json.dumps(state))
    (run_dir / "cost.json").write_text(json.dumps(cost_data))

    rc = cost.run_cost(
        run_id="zero-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "$0.0000" in out


def test_no_cache_cost_excludes_creation_keeps_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    run_dir = tmp_path / RUNS_DIR_NAME / "cache-cost-run"
    run_dir.mkdir(parents=True)
    state: dict[str, Any] = {
        "schema_version": 1,
        "session_id": "cache-cost-run",
        "source": "sdk",
        "model": _SDK_MODEL,
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        "event_count": 1,
        "cwd": "/tmp",
    }
    cost_data: dict[str, Any] = {
        "schema_version": 1,
        "session_id": "cache-cost-run",
        "totals": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 1_000_000,
            "cache_creation_tokens": 1_000_000,
        },
        "phases": {},
    }
    (run_dir / "state.json").write_text(json.dumps(state))
    (run_dir / "cost.json").write_text(json.dumps(cost_data))

    # Without --no-cache-cost
    rc = cost.run_cost(
        run_id="cache-cost-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=True,
        no_cache_cost=False,
    )
    assert rc == 0
    data_with_cache = json.loads(capsys.readouterr().out)
    cost_with = data_with_cache["cost_usd"]

    # With --no-cache-cost (excludes cache_creation)
    rc = cost.run_cost(
        run_id="cache-cost-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=True,
        no_cache_cost=True,
    )
    assert rc == 0
    data_no_cache = json.loads(capsys.readouterr().out)
    cost_without = data_no_cache["cost_usd"]

    # cost_without should be less (no cache_creation), but cache_read still counts
    assert cost_without < cost_with
    creation_rate = cost.BUILTIN_PRICING_PER_MILLION[_SDK_MODEL]["cache_creation"]
    creation_cost = 1_000_000 * creation_rate / 1_000_000.0
    assert abs(cost_with - cost_without - creation_cost) < 1e-9

    # cache_read should still be counted (not zero)
    read_rate = cost.BUILTIN_PRICING_PER_MILLION[_SDK_MODEL]["cache_read"]
    expected_read_cost = 1_000_000 * read_rate / 1_000_000.0
    assert abs(data_no_cache["costs_usd"]["cache_read"] - expected_read_cost) < 1e-9


# ---------------------------------------------------------------------------
# CLI surface tests
# ---------------------------------------------------------------------------


def test_run_id_and_all_mutual_exclusion_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = cost.run_cost(
        run_id="some-id",
        all_=True,
        source="all",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "mutually exclusive" in err


def test_neither_run_id_nor_all_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = cost.run_cost(
        run_id=None,
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "run id" in err.lower() or "--all" in err


def test_since_without_all_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "since-test-run")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id="since-test-run",
        all_=False,
        source="all",
        since=timedelta(hours=1),
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "--since" in err


def test_source_without_all_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "source-test-run")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id="source-test-run",
        all_=False,
        source="sdk",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "--source" in err


# ---------------------------------------------------------------------------
# End-to-end integration tests
# ---------------------------------------------------------------------------


def test_all_rollup_plain_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "rollup-1", session_id="r1")
    _seed_sdk_run(tmp_path, "rollup-2", session_id="r2")
    _seed_sdk_run(tmp_path, "rollup-3", session_id="r3")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id=None,
        all_=True,
        source="all",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out

    assert "rollup-1" in out
    assert "rollup-2" in out
    assert "rollup-3" in out
    assert "TOTAL" in out
    assert "3 runs" in out
    assert "$" in out


def test_all_default_sort_cost_desc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    # cheap-run: zero tokens (zero cost)
    run_dir_cheap = tmp_path / RUNS_DIR_NAME / "sort-cheap"
    run_dir_cheap.mkdir(parents=True)
    (run_dir_cheap / "state.json").write_text(json.dumps({
        "schema_version": 1, "session_id": "sort-cheap", "source": "sdk",
        "model": _SDK_MODEL, "started_at": datetime.now(UTC).isoformat(),
        "ended_at": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
        "event_count": 0, "cwd": "/tmp",
    }))
    (run_dir_cheap / "cost.json").write_text(json.dumps({
        "schema_version": 1, "session_id": "sort-cheap",
        "totals": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0},
        "phases": {},
    }))

    # expensive-run: lots of tokens
    run_dir_exp = tmp_path / RUNS_DIR_NAME / "sort-expensive"
    run_dir_exp.mkdir(parents=True)
    (run_dir_exp / "state.json").write_text(json.dumps({
        "schema_version": 1, "session_id": "sort-expensive", "source": "sdk",
        "model": _SDK_MODEL, "started_at": datetime.now(UTC).isoformat(),
        "ended_at": (datetime.now(UTC) + timedelta(seconds=10)).isoformat(),
        "event_count": 5, "cwd": "/tmp",
    }))
    (run_dir_exp / "cost.json").write_text(json.dumps({
        "schema_version": 1, "session_id": "sort-expensive",
        "totals": {"input_tokens": 100_000, "output_tokens": 50_000, "cache_read_tokens": 0, "cache_creation_tokens": 0},
        "phases": {},
    }))

    capsys.readouterr()
    rc = cost.run_cost(
        run_id=None, all_=True, source="all", since=None,
        pricing_path=None, as_json=False, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    expensive_pos = out.find("sort-expensive")
    cheap_pos = out.find("sort-cheap")
    assert expensive_pos < cheap_pos, "expensive run should appear before cheap run (cost desc)"


def test_all_filter_source_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "filter-sdk-run", session_id="fsdk")
    _seed_hooks_run(tmp_path, "filter-hooks-run")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id=None, all_=True, source="sdk", since=None,
        pricing_path=None, as_json=False, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "filter-sdk-run" in out
    assert "filter-hooks-run" not in out


def test_all_filter_source_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "filter2-sdk-run", session_id="fsdk2")
    _seed_hooks_run(tmp_path, "filter2-hooks-run")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id=None, all_=True, source="hooks", since=None,
        pricing_path=None, as_json=False, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "filter2-hooks-run" in out
    assert "filter2-sdk-run" not in out


def test_all_filter_since(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "recent-since-run", session_id="rsince")
    capsys.readouterr()

    # Write an old run manually (started 3 hours ago).
    old_run_dir = tmp_path / RUNS_DIR_NAME / "old-since-run"
    old_run_dir.mkdir(parents=True)
    old_started = datetime.now(UTC) - timedelta(hours=3)
    (old_run_dir / "state.json").write_text(json.dumps({
        "schema_version": 1, "session_id": "old-since-run", "source": "sdk",
        "model": _SDK_MODEL,
        "started_at": old_started.isoformat(),
        "ended_at": (old_started + timedelta(seconds=60)).isoformat(),
        "event_count": 1, "cwd": "/tmp",
    }))
    (old_run_dir / "cost.json").write_text(json.dumps({
        "schema_version": 1, "session_id": "old-since-run",
        "totals": {"input_tokens": 5, "output_tokens": 5, "cache_read_tokens": 0, "cache_creation_tokens": 0},
        "phases": {},
    }))

    rc = cost.run_cost(
        run_id=None, all_=True, source="all", since=timedelta(hours=1),
        pricing_path=None, as_json=False, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "recent-since-run" in out
    assert "old-since-run" not in out


def test_all_json_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "alljson-run", session_id="ajr")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id=None, all_=True, source="all", since=None,
        pricing_path=None, as_json=True, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)

    assert "runs" in data
    assert "summary" in data
    assert len(data["runs"]) == 1
    run = data["runs"][0]
    assert run["run_id"] == "alljson-run"
    assert run["model"] == _SDK_MODEL
    assert run["tokens"]["total"] == _SDK_TOTAL_TOKENS
    assert "cost_usd" in run
    summary = data["summary"]
    assert summary["run_count"] == 1
    assert summary["total_tokens"] == _SDK_TOTAL_TOKENS
    assert summary["total_cost_usd"] is not None


def test_all_output_includes_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "footer-run", session_id="frun")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id=None, all_=True, source="all", since=None,
        pricing_path=None, as_json=False, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "pricing snapshot" in out.lower() or "built-in" in out.lower()


def test_all_no_runs_match_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "no-match-run", session_id="nmrun")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id=None, all_=True, source="hooks", since=None,
        pricing_path=None, as_json=False, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "no runs match the filter" in out


def test_all_empty_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = cost.run_cost(
        run_id=None, all_=True, source="all", since=None,
        pricing_path=None, as_json=False, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "no runs found" in out


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


def test_missing_run_id_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = cost.run_cost(
        run_id="nonexistent-run-id",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "not found" in err
    assert "nonexistent-run-id" in err


def test_missing_cost_json_yields_zeros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_run_without_cost_json(tmp_path, "no-cost-file-run")

    rc = cost.run_cost(
        run_id="no-cost-file-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=True,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["tokens"]["total"] == 0
    assert data["cost_usd"] == 0.0


def test_unknown_model_path_exits_zero_with_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_unknown_model_run(tmp_path, "unknown-model-run")

    rc = cost.run_cost(
        run_id="unknown-model-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "??" in out


def test_null_model_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_unknown_model_run(tmp_path, "null-model-run", model=None)

    rc = cost.run_cost(
        run_id="null-model-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=True,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data.get("cost_usd") is None
    assert data["pricing_source"] == "missing"
    assert "cost_unknown_reason" in data


def test_unknown_model_output_includes_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_unknown_model_run(tmp_path, "unknown-footer-run")

    rc = cost.run_cost(
        run_id="unknown-footer-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "not in pricing table" in out


def test_json_output_has_no_footer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "no-footer-json-run", session_id="nfjr")
    capsys.readouterr()

    rc = cost.run_cost(
        run_id=None, all_=True, source="all", since=None,
        pricing_path=None, as_json=True, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    # Verify the output is valid JSON (footer would break this).
    data = json.loads(out)
    assert "runs" in data
    # Footer text should not appear in raw JSON output.
    assert "pricing snapshot" not in out.lower()


def test_pricing_source_tag_in_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    # Test "builtin" tag.
    _seed_sdk_run(tmp_path, "tag-builtin-run", session_id="tbrun")
    capsys.readouterr()
    rc = cost.run_cost(
        run_id="tag-builtin-run", all_=False, source="all", since=None,
        pricing_path=None, as_json=True, no_cache_cost=False,
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pricing_source"] == "builtin"

    # Test "file:" tag.
    custom_pricing = {_SDK_MODEL: {"input": 1.0, "output": 1.0, "cache_read": 0.1, "cache_creation": 0.2}}
    pricing_file = tmp_path / "tag_pricing.json"
    pricing_file.write_text(json.dumps(custom_pricing))

    _seed_sdk_run(tmp_path, "tag-file-run", session_id="tfrun")
    capsys.readouterr()
    rc = cost.run_cost(
        run_id="tag-file-run", all_=False, source="all", since=None,
        pricing_path=pricing_file, as_json=True, no_cache_cost=False,
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pricing_source"].startswith("file:")

    # Test "missing" tag for unknown model.
    _seed_unknown_model_run(tmp_path, "tag-missing-run")
    rc = cost.run_cost(
        run_id="tag-missing-run", all_=False, source="all", since=None,
        pricing_path=None, as_json=True, no_cache_cost=False,
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pricing_source"] == "missing"


def test_cli_cost_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["cost", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--all" in out
    assert "--source" in out
    assert "--since" in out
    assert "--pricing" in out
    assert "--json" in out
    assert "--no-cache-cost" in out


def test_cli_cost_no_args_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = main(["cost"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "error" in err.lower()


def test_cli_cost_all_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    rc = main(["cost", "--all"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no runs found" in out


def test_cli_cost_single_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "cli-single-run")
    capsys.readouterr()
    rc = main(["cost", "cli-single-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cli-single-run" in out
    assert _SDK_MODEL in out


def test_builtin_pricing_contains_expected_models() -> None:
    for model in ("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6",
                  "claude-sonnet-4-5", "claude-haiku-4-5"):
        assert model in cost.BUILTIN_PRICING_PER_MILLION
        row = cost.BUILTIN_PRICING_PER_MILLION[model]
        for kind in ("input", "output", "cache_read", "cache_creation"):
            assert kind in row
            assert row[kind] >= 0


def test_schema_version_mismatch_tolerated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    run_dir = tmp_path / RUNS_DIR_NAME / "schema-mismatch-run"
    run_dir.mkdir(parents=True)
    state: dict[str, Any] = {
        "schema_version": 999,
        "session_id": "schema-mismatch-run",
        "source": "sdk",
        "model": _SDK_MODEL,
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": (datetime.now(UTC) + timedelta(seconds=5)).isoformat(),
        "event_count": 1,
        "cwd": "/tmp",
    }
    cost_data: dict[str, Any] = {
        "schema_version": 999,
        "session_id": "schema-mismatch-run",
        "totals": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
        "phases": {},
    }
    (run_dir / "state.json").write_text(json.dumps(state))
    (run_dir / "cost.json").write_text(json.dumps(cost_data))

    rc = cost.run_cost(
        run_id="schema-mismatch-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=True,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["tokens"]["input"] == 100
    assert data["tokens"]["output"] == 50
    assert data["cost_usd"] is not None


def test_stray_file_in_runs_dir_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    _seed_sdk_run(tmp_path, "good-stray-run", session_id="gsr")

    # Write a stray file (not a dir) in the runs directory.
    runs_dir = tmp_path / RUNS_DIR_NAME
    stray_file = runs_dir / "stray_file.txt"
    stray_file.write_text("not a run dir")

    capsys.readouterr()
    rc = cost.run_cost(
        run_id=None, all_=True, source="all", since=None,
        pricing_path=None, as_json=True, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["summary"]["run_count"] == 1
    run_ids = [r["run_id"] for r in data["runs"]]
    assert "good-stray-run" in run_ids


def test_in_flight_run_null_ended_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))

    run_dir = tmp_path / RUNS_DIR_NAME / "inflight-run"
    run_dir.mkdir(parents=True)
    state: dict[str, Any] = {
        "schema_version": 1,
        "session_id": "inflight-run",
        "source": "hooks",
        "model": _SDK_MODEL,
        "started_at": datetime.now(UTC).isoformat(),
        "ended_at": None,
        "event_count": 1,
        "cwd": "/tmp",
    }
    (run_dir / "state.json").write_text(json.dumps(state))

    rc = cost.run_cost(
        run_id="inflight-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=False,
        no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Duration:  -" in out or "Duration: -" in out

    # JSON form should have duration_seconds: null.
    rc = cost.run_cost(
        run_id="inflight-run",
        all_=False,
        source="all",
        since=None,
        pricing_path=None,
        as_json=True,
        no_cache_cost=False,
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["duration_seconds"] is None


# ---------------------------------------------------------------------------
# Sort order (fixed in polish — locked in by regression tests)
# ---------------------------------------------------------------------------


def _seed_priced_run(
    runs_root: Path,
    run_id: str,
    model: str,
    input_tokens: int,
    started_at: str,
) -> None:
    """Minimal seed: state.json with model + started_at, cost.json with tokens."""
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "schema_version": 1,
            "session_id": run_id,
            "source": "sdk",
            "started_at": started_at,
            "ended_at": started_at,
            "model": model,
            "event_count": 1,
        })
    )
    (run_dir / "cost.json").write_text(
        json.dumps({
            "schema_version": 1,
            "session_id": run_id,
            "totals": {
                "input_tokens": input_tokens,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            },
        })
    )


def test_cost_all_unknown_model_runs_sort_LAST_not_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression for review issue #1: unknown-cost rows MUST appear at the
    bottom of the table (after every priced row), not at the top.
    Before the fix the sort key used -inf for unknown cost, sorting them first."""
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    runs_root = tmp_path / "runs"
    _seed_priced_run(runs_root, "priced-1", "claude-opus-4-7", 1000, "2026-05-27T10:00:00+00:00")
    _seed_priced_run(runs_root, "priced-2", "claude-opus-4-7", 500, "2026-05-27T10:00:00+00:00")
    _seed_priced_run(runs_root, "mystery", "claude-future-9-9", 100, "2026-05-27T10:00:00+00:00")

    rc = cost.run_cost(
        run_id=None, all_=True, source="all", since=None,
        pricing_path=None, as_json=False, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    # The priced rows must appear above the mystery row in the rendered table.
    priced_1_pos = out.find("priced-1")
    priced_2_pos = out.find("priced-2")
    mystery_pos = out.find("mystery")
    assert priced_1_pos != -1 and priced_2_pos != -1 and mystery_pos != -1
    assert priced_1_pos < mystery_pos, "unknown-cost row sorted before a priced row"
    assert priced_2_pos < mystery_pos, "unknown-cost row sorted before a priced row"


def test_cost_all_equal_cost_breaks_tie_with_NEWER_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression for review issue #2: when two runs cost the same, the NEWER
    one must come first (started_at desc tiebreaker). Before the fix the
    tiebreaker compared ISO strings ascending, putting OLDER runs first."""
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    runs_root = tmp_path / "runs"
    # Same model + token counts → identical cost. Differ only by started_at.
    _seed_priced_run(runs_root, "older", "claude-opus-4-7", 1000, "2026-05-27T08:00:00+00:00")
    _seed_priced_run(runs_root, "newer", "claude-opus-4-7", 1000, "2026-05-27T12:00:00+00:00")

    rc = cost.run_cost(
        run_id=None, all_=True, source="all", since=None,
        pricing_path=None, as_json=False, no_cache_cost=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    newer_pos = out.find("newer")
    older_pos = out.find("older")
    assert newer_pos != -1 and older_pos != -1
    assert newer_pos < older_pos, "tiebreaker put older run first; spec requires newer first"
