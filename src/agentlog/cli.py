"""agentlog CLI entry point.

This is a scaffold. Subcommands (init, uninstall, tail, ls, cost, view) land in
follow-up work driven by the ADW pipeline; see DESIGN.md "v0.1 ship scope".
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from agentlog import __version__

SUBCOMMANDS = ("init", "uninstall", "tail", "ls", "cost", "view")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentlog",
        description="Local-first observability for AI coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"agentlog {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name in SUBCOMMANDS:
        sp = sub.add_parser(name, help=f"{name} (not yet implemented)")
        sp.set_defaults(func=_not_implemented)
    return parser


def _not_implemented(args: argparse.Namespace) -> int:
    print(f"agentlog {args.command}: not yet implemented", file=sys.stderr)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    return int(args.func(args))
