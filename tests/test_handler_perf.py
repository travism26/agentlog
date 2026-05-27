"""Performance budget assertions for hook handler hot paths.

Budget failures are tech_debt-class (non-blocking). Gate on AGENTLOG_PERF=1 to
avoid noise on slow CI runners.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

import pytest

from agentlog import capture

pytestmark = pytest.mark.skipif(
    not os.environ.get("AGENTLOG_PERF"),
    reason="set AGENTLOG_PERF=1 to run perf tests",
)

_COLD_BUDGET_MS = 50.0
_STEADY_BUDGET_MS = 10.0


def test_cold_start_session_start_under_50ms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    payload = json.dumps({"session_id": "perf_cold", "cwd": "/tmp", "model": "m"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))

    t0 = time.perf_counter()
    rc = capture.run_hook("SessionStart")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert rc == 0
    assert elapsed_ms < _COLD_BUDGET_MS, f"cold start {elapsed_ms:.1f}ms > {_COLD_BUDGET_MS}ms"


def test_steady_state_post_tool_use_under_10ms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTLOG_HOME", str(tmp_path))
    # Warm up: create the session dir
    (tmp_path / "runs" / "perf_steady").mkdir(parents=True, exist_ok=True)

    payload = json.dumps({
        "session_id": "perf_steady",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
        "tool_response": {"output": "hi"},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))

    t0 = time.perf_counter()
    rc = capture.run_hook("PostToolUse")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert rc == 0
    assert elapsed_ms < _STEADY_BUDGET_MS, (
        f"steady state {elapsed_ms:.1f}ms > {_STEADY_BUDGET_MS}ms"
    )
