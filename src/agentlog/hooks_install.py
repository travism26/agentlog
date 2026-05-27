"""Hook registration lifecycle for Claude Code settings.json.

Pure functions (plan_install, plan_uninstall, diff) are unit-testable without
filesystem access. I/O helpers (load_settings, write_atomic) and orchestrators
(run_init, run_uninstall) sit on top.

Two facts worth pinning down for future contributors:

1. `HOOK_COMMAND_PREFIX` is part of the *installed-file format*, not an
   implementation detail. Renaming it orphans every existing installation:
   `uninstall` will no longer recognise old entries, and `init` will add a
   second, parallel entry beside the old one. Any change here needs a
   migration plan, not just a refactor.
2. `write_atomic` writes JSON with `sort_keys=True`, so a fresh `init` against
   a hand-curated, custom-ordered `settings.json` will reorder the keys on
   disk even when the agentlog hooks merge cleanly. This is deliberate —
   stable key order makes future diffs readable — but it WILL surprise a user
   who alphabetised their config on purpose. Document, do not silently
   "preserve" order, since that introduces a much more confusing failure mode
   (unstable diffs across machines).
"""

from __future__ import annotations

import copy
import difflib
import json
import os
import sys
from pathlib import Path
from typing import Any

from agentlog._constants import EVENTS as EVENTS  # noqa: F401
from agentlog._constants import HOOK_COMMAND_PREFIX as HOOK_COMMAND_PREFIX  # noqa: F401


def agentlog_command(event: str) -> str:
    return f"{HOOK_COMMAND_PREFIX} {event}"


class MalformedSettingsError(Exception):
    """Raised when settings.json exists but is not a JSON object we can merge into."""

    def __init__(self, path: Path, reason: str = "contains invalid JSON") -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path} {reason}")


class SettingsIOError(Exception):
    """Raised when settings.json exists but cannot be read (permissions, etc.)."""

    def __init__(self, path: Path, cause: OSError) -> None:
        self.path = path
        self.cause = cause
        super().__init__(f"{path}: {cause}")


# ---------------------------------------------------------------------------
# Pure plan functions — no filesystem access
# ---------------------------------------------------------------------------


def plan_install(existing: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = copy.deepcopy(existing)
    result.setdefault("hooks", {})
    for event in EVENTS:
        result["hooks"].setdefault(event, [])
        entries: list[dict[str, Any]] = result["hooks"][event]
        has_agentlog = any(
            h.get("command", "").startswith(HOOK_COMMAND_PREFIX)
            for group in entries
            for h in group.get("hooks", [])
        )
        if not has_agentlog:
            group: dict[str, Any] = {
                "hooks": [{"type": "command", "command": agentlog_command(event)}]
            }
            if event == "PostToolUse":
                group["matcher"] = "*"
            entries.append(group)
    return result


def plan_uninstall(existing: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = copy.deepcopy(existing)
    hooks: dict[str, Any] = result.get("hooks", {})
    for event in list(hooks.keys()):
        new_groups: list[dict[str, Any]] = []
        for group in hooks[event]:
            inner: list[dict[str, Any]] = group.get("hooks", [])
            filtered = [
                h for h in inner
                if not h.get("command", "").startswith(HOOK_COMMAND_PREFIX)
            ]
            if filtered:
                new_group = {**group, "hooks": filtered}
                new_groups.append(new_group)
            # else: group was all-agentlog or emptied — drop it
        if new_groups:
            hooks[event] = new_groups
        else:
            del hooks[event]
    if not hooks:
        result.pop("hooks", None)
    return result


def diff(before: dict[str, Any], after: dict[str, Any]) -> str:
    before_lines = json.dumps(before, indent=2, sort_keys=True).splitlines(keepends=True)
    after_lines = json.dumps(after, indent=2, sort_keys=True).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="settings.json (current)",
            tofile="settings.json (after)",
            n=3,
        )
    )


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def resolve_settings_path(project: bool) -> Path:
    if project:
        return Path.cwd() / ".claude" / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise MalformedSettingsError(path) from exc
    except OSError as exc:
        raise SettingsIOError(path, exc) from exc
    if not isinstance(data, dict):
        raise MalformedSettingsError(
            path, reason=f"top-level JSON value is {type(data).__name__}, not an object"
        )
    return data


def write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Orchestrators — called by CLI handlers
# ---------------------------------------------------------------------------


def run_init(*, project: bool, dry_run: bool) -> int:
    path = resolve_settings_path(project)
    try:
        existing = load_settings(path)
    except MalformedSettingsError as exc:
        print(
            f"error: {path} {exc.reason}; refusing to overwrite. "
            "Back up the file and re-run.",
            file=sys.stderr,
        )
        return 1
    except SettingsIOError as exc:
        print(f"error: cannot read {path}: {exc.cause}", file=sys.stderr)
        return 1
    after = plan_install(existing)
    if after == existing:
        print(f"agentlog hooks already installed at {path}")
        return 0
    d = diff(existing, after)
    if dry_run:
        print(d if d else "(no changes)")
        return 0
    write_atomic(path, after)
    print(f"installed agentlog hooks to {path}")
    return 0


def run_uninstall(*, project: bool, dry_run: bool) -> int:
    path = resolve_settings_path(project)
    if not path.exists():
        print(f"no settings.json at {path}; nothing to uninstall")
        return 0
    try:
        existing = load_settings(path)
    except MalformedSettingsError as exc:
        print(
            f"error: {path} {exc.reason}; refusing to overwrite. "
            "Back up the file and re-run.",
            file=sys.stderr,
        )
        return 1
    except SettingsIOError as exc:
        print(f"error: cannot read {path}: {exc.cause}", file=sys.stderr)
        return 1
    after = plan_uninstall(existing)
    if after == existing:
        print(f"no agentlog hooks found in {path}")
        return 0
    d = diff(existing, after)
    if dry_run:
        print(d if d else "(no changes)")
        return 0
    write_atomic(path, after)
    print(f"uninstalled agentlog hooks from {path}")
    return 0
