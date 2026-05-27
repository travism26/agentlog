"""Tests for src/agentlog/hooks_install.py and the init/uninstall CLI handlers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentlog.cli import main
from agentlog.hooks_install import (
    EVENTS,
    HOOK_COMMAND_PREFIX,
    MalformedSettingsError,
    SettingsIOError,
    agentlog_command,
    diff,
    load_settings,
    plan_install,
    plan_uninstall,
    write_atomic,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FOREIGN_HOOK: dict[str, Any] = {
    "hooks": [{"type": "command", "command": "other-tool"}]
}

FOREIGN_POSTTOOLUSE: dict[str, Any] = {
    "matcher": "Edit",
    "hooks": [{"type": "command", "command": "other-tool"}],
}


def _seed(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# Pure unit tests
# ---------------------------------------------------------------------------


def test_agentlog_command_format() -> None:
    assert agentlog_command("SessionStart") == "agentlog _hook SessionStart"


def test_plan_install_fresh_adds_all_five_events() -> None:
    result = plan_install({})
    assert "hooks" in result
    for event in EVENTS:
        assert event in result["hooks"]
        groups = result["hooks"][event]
        assert any(
            h.get("command", "").startswith(HOOK_COMMAND_PREFIX)
            for g in groups
            for h in g.get("hooks", [])
        )
    assert "PreToolUse" not in result["hooks"]


def test_plan_install_is_idempotent() -> None:
    first = plan_install({})
    second = plan_install(first)
    assert first == second


def test_plan_install_preserves_foreign_hooks() -> None:
    existing: dict[str, Any] = {"hooks": {"PostToolUse": [FOREIGN_POSTTOOLUSE]}}
    result = plan_install(existing)
    post = result["hooks"]["PostToolUse"]
    foreign_commands = [
        h["command"]
        for g in post
        for h in g.get("hooks", [])
        if not h.get("command", "").startswith(HOOK_COMMAND_PREFIX)
    ]
    assert "other-tool" in foreign_commands


def test_plan_install_posttooluse_group_has_matcher() -> None:
    result = plan_install({})
    post_groups = result["hooks"]["PostToolUse"]
    agentlog_groups = [
        g for g in post_groups
        if any(h.get("command", "").startswith(HOOK_COMMAND_PREFIX) for h in g.get("hooks", []))
    ]
    assert agentlog_groups
    assert agentlog_groups[0].get("matcher") == "*"


def test_plan_uninstall_removes_only_sentinel_entries() -> None:
    existing: dict[str, Any] = {
        "hooks": {
            "PostToolUse": [
                FOREIGN_POSTTOOLUSE,
                {"matcher": "*", "hooks": [{"type": "command", "command": "agentlog _hook PostToolUse"}]},
            ]
        }
    }
    result = plan_uninstall(existing)
    assert "PostToolUse" in result["hooks"]
    post = result["hooks"]["PostToolUse"]
    all_commands = [h["command"] for g in post for h in g.get("hooks", [])]
    assert "other-tool" in all_commands
    assert not any(c.startswith(HOOK_COMMAND_PREFIX) for c in all_commands)


def test_plan_uninstall_strips_agentlog_from_mixed_single_group() -> None:
    """A single group whose `hooks[]` list mixes agentlog + foreign entries:
    plan_uninstall must drop ONLY the agentlog entry, preserve the group
    structure (matcher etc.), and keep the foreign entry intact."""
    existing: dict[str, Any] = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Edit",
                    "hooks": [
                        {"type": "command", "command": "agentlog _hook PostToolUse"},
                        {"type": "command", "command": "other-tool"},
                    ],
                }
            ]
        }
    }
    result = plan_uninstall(existing)
    post = result["hooks"]["PostToolUse"]
    assert len(post) == 1
    surviving_group = post[0]
    assert surviving_group["matcher"] == "Edit"
    assert surviving_group["hooks"] == [
        {"type": "command", "command": "other-tool"}
    ]


def test_plan_uninstall_empties_then_drops_keys() -> None:
    installed = plan_install({})
    result = plan_uninstall(installed)
    assert "hooks" not in result


def test_plan_uninstall_round_trips_original_settings() -> None:
    original: dict[str, Any] = {"hooks": {"PostToolUse": [FOREIGN_POSTTOOLUSE]}}
    installed = plan_install(original)
    restored = plan_uninstall(installed)
    assert restored == original


def test_plan_uninstall_preserves_pretooluse() -> None:
    pretooluse_entry: dict[str, Any] = {"hooks": [{"type": "command", "command": "some-guard"}]}
    existing: dict[str, Any] = {"hooks": {"PreToolUse": [pretooluse_entry]}}
    installed = plan_install(existing)
    assert "PreToolUse" in installed["hooks"]
    restored = plan_uninstall(installed)
    assert restored == existing


def test_plan_install_does_not_touch_pretooluse() -> None:
    pretooluse_entry: dict[str, Any] = {"hooks": [{"type": "command", "command": "some-guard"}]}
    existing: dict[str, Any] = {"hooks": {"PreToolUse": [pretooluse_entry]}}
    result = plan_install(existing)
    assert result["hooks"]["PreToolUse"] == [pretooluse_entry]
    assert "PreToolUse" not in EVENTS


def test_diff_empty_when_no_change() -> None:
    d = plan_install({})
    assert diff(d, d) == ""


def test_diff_has_unified_markers_on_change() -> None:
    before: dict[str, Any] = {}
    after = plan_install(before)
    output = diff(before, after)
    assert "---" in output
    assert "+++" in output


# ---------------------------------------------------------------------------
# Filesystem helper unit tests
# ---------------------------------------------------------------------------


def test_load_settings_returns_empty_dict_when_missing(tmp_path: Path) -> None:
    result = load_settings(tmp_path / "nonexistent.json")
    assert result == {}


def test_load_settings_raises_on_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "settings.json"
    bad.write_text("{ not valid json")
    with pytest.raises(MalformedSettingsError) as exc_info:
        load_settings(bad)
    assert exc_info.value.path == bad


def test_load_settings_raises_on_non_object_json(tmp_path: Path) -> None:
    """Valid JSON but not a top-level object (e.g., array, number) is rejected
    with a distinct reason — we cannot merge hooks into [1,2,3]."""
    bad = tmp_path / "settings.json"
    bad.write_text("[1, 2, 3]")
    with pytest.raises(MalformedSettingsError) as exc_info:
        load_settings(bad)
    assert exc_info.value.path == bad
    assert "list" in exc_info.value.reason


def test_load_settings_raises_on_permission_denied(tmp_path: Path) -> None:
    """Unreadable file (e.g., no read permission) surfaces as SettingsIOError,
    not as a raw traceback. Test relies on POSIX chmod; skip on Windows."""
    import os
    import sys as _sys

    if _sys.platform == "win32":
        pytest.skip("POSIX chmod required")
    if os.geteuid() == 0:
        pytest.skip("running as root; chmod 0 has no effect")
    target = tmp_path / "settings.json"
    target.write_text("{}")
    target.chmod(0o000)
    try:
        with pytest.raises(SettingsIOError) as exc_info:
            load_settings(target)
        assert exc_info.value.path == target
        assert isinstance(exc_info.value.cause, OSError)
    finally:
        target.chmod(0o644)


def test_write_atomic_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "settings.json"
    write_atomic(target, {"foo": "bar"})
    assert target.exists()
    assert json.loads(target.read_text()) == {"foo": "bar"}


def test_write_atomic_no_tmp_file_left_behind(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    write_atomic(target, {})
    tmp = target.with_suffix(".json.tmp")
    assert not tmp.exists()


# ---------------------------------------------------------------------------
# Integration tests — CLI via main()
# ---------------------------------------------------------------------------


def test_run_init_dry_run_prints_diff_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = main(["init", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "---" in out
    assert "+++" in out
    target = tmp_path / ".claude" / "settings.json"
    assert not target.exists()


def test_run_init_fresh_creates_settings_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = main(["init"])
    capsys.readouterr()
    assert rc == 0
    target = tmp_path / ".claude" / "settings.json"
    assert target.exists()
    data = json.loads(target.read_text())
    assert "hooks" in data
    for event in EVENTS:
        assert event in data["hooks"]


def test_run_init_is_idempotent_byte_for_byte(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".claude" / "settings.json"
    main(["init"])
    capsys.readouterr()
    bytes_after_first = target.read_bytes()
    rc = main(["init"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "already installed" in out
    assert target.read_bytes() == bytes_after_first


def test_run_init_project_scope_writes_to_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(tmp_path)
    rc = main(["init", "--project"])
    capsys.readouterr()
    assert rc == 0
    project_target = tmp_path / ".claude" / "settings.json"
    user_target = home_dir / ".claude" / "settings.json"
    assert project_target.exists()
    assert not user_target.exists()


def test_run_init_preserves_existing_user_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".claude" / "settings.json"
    foreign: dict[str, Any] = {"hooks": {"PostToolUse": [FOREIGN_POSTTOOLUSE]}}
    _seed(target, foreign)
    rc = main(["init"])
    capsys.readouterr()
    assert rc == 0
    data = json.loads(target.read_text())
    post = data["hooks"]["PostToolUse"]
    foreign_commands = [h["command"] for g in post for h in g.get("hooks", []) if not h.get("command", "").startswith(HOOK_COMMAND_PREFIX)]
    assert "other-tool" in foreign_commands


def test_run_uninstall_removes_only_agentlog_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".claude" / "settings.json"
    mixed: dict[str, Any] = {
        "hooks": {
            "PostToolUse": [
                FOREIGN_POSTTOOLUSE,
                {"matcher": "*", "hooks": [{"type": "command", "command": "agentlog _hook PostToolUse"}]},
            ]
        }
    }
    _seed(target, mixed)
    rc = main(["uninstall"])
    capsys.readouterr()
    assert rc == 0
    data = json.loads(target.read_text())
    assert "PostToolUse" in data["hooks"]
    all_commands = [h["command"] for g in data["hooks"]["PostToolUse"] for h in g.get("hooks", [])]
    assert "other-tool" in all_commands
    assert not any(c.startswith(HOOK_COMMAND_PREFIX) for c in all_commands)


def test_init_then_uninstall_yields_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".claude" / "settings.json"
    original: dict[str, Any] = {"hooks": {"PostToolUse": [FOREIGN_POSTTOOLUSE]}}
    _seed(target, original)
    main(["init"])
    capsys.readouterr()
    main(["uninstall"])
    capsys.readouterr()
    result = json.loads(target.read_text())
    assert result == original


def test_uninstall_with_no_settings_file_is_noop_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = main(["uninstall"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to uninstall" in out
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_malformed_settings_exits_nonzero_and_does_not_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    garbage = b"{ not valid json !!!"
    target.write_bytes(garbage)
    rc = main(["init"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "invalid JSON" in err
    assert str(target) in err
    assert target.read_bytes() == garbage


def test_uninstall_dry_run_prints_diff_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".claude" / "settings.json"
    main(["init"])
    capsys.readouterr()
    bytes_before = target.read_bytes()
    rc = main(["uninstall", "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "---" in out
    assert target.read_bytes() == bytes_before


def test_hook_noop_subparser_exits_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    rc = main(["_hook", "SessionStart"])
    capsys.readouterr()
    assert rc == 0
