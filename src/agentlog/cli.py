"""agentlog CLI entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agentlog import __version__, capture, hooks_install, tail

SUBCOMMANDS = ("init", "uninstall", "tail", "ls", "cost", "view")

_STUB_SUBCOMMANDS = frozenset({"ls", "cost", "view"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentlog",
        description="Local-first observability for AI coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"agentlog {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    for name in SUBCOMMANDS:
        if name in _STUB_SUBCOMMANDS:
            sp = sub.add_parser(name, help=f"{name} (not yet implemented)")
            sp.set_defaults(func=_not_implemented)
        elif name == "init":
            sp = sub.add_parser("init", help="register agentlog hooks in Claude Code settings.json")
            sp.add_argument(
                "--project",
                action="store_true",
                help="Write to ./.claude/settings.json (project scope) instead of ~/.claude/settings.json",
            )
            sp.add_argument(
                "--dry-run",
                action="store_true",
                help="Print what would change; write nothing",
            )
            sp.set_defaults(func=_run_init)
        elif name == "uninstall":
            sp = sub.add_parser(
                "uninstall", help="remove agentlog hooks from Claude Code settings.json"
            )
            sp.add_argument(
                "--project",
                action="store_true",
                help="Write to ./.claude/settings.json (project scope) instead of ~/.claude/settings.json",
            )
            sp.add_argument(
                "--dry-run",
                action="store_true",
                help="Print what would change; write nothing",
            )
            sp.set_defaults(func=_run_uninstall)
        elif name == "tail":
            sp = sub.add_parser(
                "tail", help="ingest cc_raw_output.jsonl from SDK runs into the unified schema"
            )
            sp.add_argument(
                "path",
                help="file or directory containing cc_raw_output.jsonl",
            )
            sp.add_argument(
                "--run-id",
                dest="run_id",
                default=None,
                help="explicit run id (only valid for single-file ingestion)",
            )
            sp.add_argument(
                "--source-name",
                dest="source_name",
                default=None,
                help="human label written into state.json (default: basename of <path>)",
            )
            sp.add_argument(
                "--dry-run",
                action="store_true",
                help="parse and report; write nothing",
            )
            sp.add_argument(
                "--force",
                action="store_true",
                help="re-ingest even if events already exist",
            )
            sp.set_defaults(func=_run_tail)

    # Routes `agentlog _hook <Event>` to capture.run_hook (the fail-open
    # boundary; never raises, always exits 0). Kept hidden from --help — this
    # is the install-time wiring target for settings.json, not a user command.
    hook_sp = sub.add_parser("_hook", help=argparse.SUPPRESS)
    hook_sp.add_argument("event")
    hook_sp.set_defaults(func=_run_hook)
    # Python 3.11 doesn't honour help=SUPPRESS for subparsers in the listing;
    # remove the pseudo-action so _hook stays functional but absent from --help.
    sub._choices_actions = [a for a in sub._choices_actions if a.dest != "_hook"]

    return parser


def _run_tail(args: argparse.Namespace) -> int:
    return tail.run_tail(
        Path(args.path),
        run_id=args.run_id,
        source_name=args.source_name,
        dry_run=args.dry_run,
        force=args.force,
    )


def _run_hook(args: argparse.Namespace) -> int:
    return capture.run_hook(args.event)


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"agentlog {args.command}: not yet implemented", file=sys.stderr)
    return 2


def _run_init(args: argparse.Namespace) -> int:
    return hooks_install.run_init(project=args.project, dry_run=args.dry_run)


def _run_uninstall(args: argparse.Namespace) -> int:
    return hooks_install.run_uninstall(project=args.project, dry_run=args.dry_run)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return int(args.func(args))
