"""Shared constants for agentlog hook installation and capture.

Two notes pinned for future contributors:

1. ``HOOK_COMMAND_PREFIX`` is part of the *installed-file format*. Renaming it
   orphans every existing installation: ``uninstall`` will no longer recognise old
   entries, and ``init`` will add a second, parallel entry beside the old one.
   Any change here needs a migration plan, not just a refactor.
2. ``SCHEMA_VERSION`` must be bumped (to 2, 3, …) whenever a breaking change is
   made to the JSONL wire format. Downstream readers (``ls``, ``cost``, ``view``)
   gate on this integer.
"""

from __future__ import annotations

# PreToolUse intentionally omitted — deferred past v0.1 (CLAUDE.md rule #5).
EVENTS: tuple[str, ...] = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "Stop",
    "SessionEnd",
)

# Part of the installed-file format — do NOT rename without a migration plan.
HOOK_COMMAND_PREFIX: str = "agentlog _hook"

SCHEMA_VERSION: int = 1
SOURCE_HOOKS: str = "hooks"
MAX_INLINE_BYTES: int = 4096
DEFAULT_DATA_ROOT_NAME: str = ".agentlog"
SELF_LOG_NAME: str = "_self.log"
RUNS_DIR_NAME: str = "runs"
UNKNOWN_SESSION_PREFIX: str = "unknown_session"
