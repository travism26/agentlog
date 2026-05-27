"""agentlog CLI entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from agentlog import __version__, capture, cost, hooks_install, ls, tail

SUBCOMMANDS = ("init", "uninstall", "tail", "ls", "cost", "view")

_STUB_SUBCOMMANDS = frozenset({"view"})


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
        elif name == "ls":
            sp = sub.add_parser("ls", help="list captured runs across hooks and SDK sources")
            sp.add_argument(
                "--source",
                choices=["hooks", "sdk", "all"],
                default="all",
                help="filter by data source (default: all)",
            )
            sp.add_argument(
                "--since",
                type=ls._parse_duration,
                default=None,
                metavar="DURATION",
                help="only runs started within DURATION (e.g. 30m, 24h, 7d)",
            )
            sp.add_argument(
                "--sort",
                dest="sort_key",
                choices=["started", "ended", "duration", "events", "tokens", "cost"],
                default="started",
                help="sort column (default: started); cost is an alias for tokens in v0.1",
            )
            sp.add_argument(
                "--reverse",
                action="store_true",
                help="ascending order (default is descending / newest-first)",
            )
            sp.add_argument(
                "--limit",
                type=int,
                default=50,
                metavar="N",
                help="max rows to show; 0 = unlimited (default: 50)",
            )
            sp.add_argument(
                "--json",
                dest="as_json",
                action="store_true",
                help="machine-readable JSON output",
            )
            sp.add_argument(
                "--reindex",
                action="store_true",
                help="force a full SQLite index rebuild before listing",
            )
            sp.set_defaults(func=_run_ls)
        elif name == "cost":
            sp = sub.add_parser("cost", help="show token and dollar cost for one run or all runs")
            sp.add_argument(
                "run_id",
                nargs="?",
                default=None,
                help="run id to show cost for (mutually exclusive with --all)",
            )
            sp.add_argument(
                "--all",
                dest="all_",
                action="store_true",
                help="show cost rollup for all runs",
            )
            sp.add_argument(
                "--source",
                choices=["hooks", "sdk", "all"],
                default="all",
                help="filter by data source (only valid with --all; default: all)",
            )
            sp.add_argument(
                "--since",
                type=ls._parse_duration,
                default=None,
                metavar="DURATION",
                help="only runs started within DURATION (only valid with --all; e.g. 30m, 24h, 7d)",
            )
            sp.add_argument(
                "--pricing",
                type=Path,
                dest="pricing_path",
                default=None,
                metavar="PATH",
                help="path to a JSON pricing-override file (merged onto built-in)",
            )
            sp.add_argument(
                "--json",
                dest="as_json",
                action="store_true",
                help="machine-readable JSON output",
            )
            sp.add_argument(
                "--no-cache-cost",
                dest="no_cache_cost",
                action="store_true",
                help="exclude cache_creation cost (NOT cache_read) from the total; useful for debugging",
            )
            sp.set_defaults(func=_run_cost)

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


def _run_ls(args: argparse.Namespace) -> int:
    return ls.run_ls(
        source=args.source,
        since=args.since,
        sort_key=args.sort_key,
        reverse=args.reverse,
        limit=args.limit,
        as_json=args.as_json,
        reindex=args.reindex,
    )


def _run_cost(args: argparse.Namespace) -> int:
    return cost.run_cost(
        run_id=args.run_id,
        all_=args.all_,
        source=args.source,
        since=args.since,
        pricing_path=args.pricing_path,
        as_json=args.as_json,
        no_cache_cost=args.no_cache_cost,
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
