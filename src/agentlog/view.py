"""agentlog view — static three-panel TUI for a single captured run.

Four invariants pinned for future contributors:

1. Read-only with respect to ``runs/``. This module MUST NOT mutate
   ``state.json``, ``events.jsonl``, ``cost.json``, or the SQLite index.
2. Fail-loud user CLI: rc=2 for user errors (missing run id, bad flags),
   rc=1 for unexpected I/O failures or missing ``rich``, rc=0 for success
   (including gracefully-handled missing events or cost data).
3. ``rich`` is gated inside ``run_view``, after the ``--json`` branch returns.
   The module MUST import cleanly without ``rich`` installed.
4. Local-first: no network calls; no side effects outside rendering to stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentlog._constants import (
    DEFAULT_DATA_ROOT_NAME,
    RUNS_DIR_NAME,
    SCHEMA_VERSION,
    SELF_LOG_NAME,
)
from agentlog.cost import (
    _KIND_DISPLAY_ORDER,
    _PRICING_STALENESS_FOOTER,
    _TOKEN_KIND_LABELS,
    _compute_run_cost,
    _resolve_pricing,
)
from agentlog.ls import _format_duration, _started_display

# ---------------------------------------------------------------------------
# Helpers (duplicated from cost.py — inverted failure contract)
# Duplicated helpers; shared _io.py deferred to v0.2+ (precedent: cost.py:41, tail.py:14).
# ---------------------------------------------------------------------------


def _data_root() -> Path:
    env = os.environ.get("AGENTLOG_HOME")
    if env:
        return Path(env)
    return Path.home() / DEFAULT_DATA_ROOT_NAME


def _log_self(root: Path, message: str) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        log_path = root / SELF_LOG_NAME
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{ts} {message}\n")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Display constants
# ---------------------------------------------------------------------------

_DISPLAY_CAP_TEXT: int = 80
_DISPLAY_CAP_TOOL: int = 60
_TOOL_NAME_PAD: int = 8

# Event kind → rich style string.
_EVENT_KIND_STYLES: dict[str, str] = {
    "session_start": "bold magenta",
    "session_end": "bold magenta",
    "prompt": "cyan",
    "assistant_text": "green",
    "tool_use": "yellow",
    "stop": "bold blue",
    "unknown": "dim",
}

# "assistant_text" is 14 chars — longest known kind.
_EVENT_KIND_PAD: int = max(len(k) for k in _EVENT_KIND_STYLES)


# ---------------------------------------------------------------------------
# Per-tool summarizer dispatch table (lesson #9).
# Keys MUST match {"Read", "Edit", "Write", "Grep", "Bash", "Glob"}.
# ---------------------------------------------------------------------------


def _default_summarizer(params: dict[str, Any]) -> str:
    return json.dumps(params)


_TOOL_SUMMARIZERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "Read": lambda p: str(p.get("file_path") or "?"),
    "Edit": lambda p: str(p.get("file_path") or "?"),
    "Write": lambda p: str(p.get("file_path") or "?"),
    "Grep": lambda p: f"{p.get('pattern', '?')!r} in {p.get('path', '.')}",
    "Bash": lambda p: str(p.get("command") or "?"),
    "Glob": lambda p: str(p.get("pattern") or "?"),
}


# ---------------------------------------------------------------------------
# ANSI escape stripper
# ---------------------------------------------------------------------------

_ANSI_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


# ---------------------------------------------------------------------------
# Read-path helpers
# ---------------------------------------------------------------------------


def _load_state(run_dir: Path, root: Path) -> dict[str, Any] | None:
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return None
    try:
        text = state_path.read_text(encoding="utf-8")
        data: Any = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        _log_self(root, f"view: failed to read state.json for {run_dir.name}: {exc!r}")
        return None
    if not isinstance(data, dict):
        _log_self(root, f"view: state.json for {run_dir.name} is not a JSON object")
        return None
    schema_ver = data.get("schema_version")
    if schema_ver is not None and schema_ver != SCHEMA_VERSION:
        _log_self(
            root,
            f"view: run {run_dir.name}: state.json schema_version={schema_ver} != "
            f"{SCHEMA_VERSION}; continuing with available fields",
        )
    return data


def _load_events(run_dir: Path, root: Path) -> list[dict[str, Any]]:
    events_path = run_dir / "events.jsonl"
    if not events_path.exists():
        return []
    try:
        text = events_path.read_text(encoding="utf-8")
    except OSError as exc:
        _log_self(root, f"view: failed to read events.jsonl for {run_dir.name}: {exc!r}")
        return []
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            _log_self(
                root,
                f"view: run {run_dir.name}: events.jsonl line {lineno}: "
                f"malformed JSON: {exc!r}; skipped",
            )
            continue
        if not isinstance(rec, dict):
            _log_self(
                root,
                f"view: run {run_dir.name}: events.jsonl line {lineno}: "
                "not a JSON object; skipped",
            )
            continue
        records.append(rec)

    # Sort ascending by timestamp (lesson #1).
    # Missing/malformed timestamps fall to front (datetime.min UTC).
    _min_dt = datetime.min.replace(tzinfo=UTC)

    def _ts_key(r: dict[str, Any]) -> datetime:
        ts = r.get("timestamp")
        if not ts:
            return _min_dt
        try:
            dt = datetime.fromisoformat(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return _min_dt

    records.sort(key=_ts_key)
    return records


def _load_cost(run_dir: Path, root: Path) -> dict[str, Any] | None:
    cost_path = run_dir / "cost.json"
    if not cost_path.exists():
        return None
    try:
        text = cost_path.read_text(encoding="utf-8")
        data: Any = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        _log_self(root, f"view: failed to read cost.json for {run_dir.name}: {exc!r}")
        return None
    if not isinstance(data, dict):
        return None
    schema_ver = data.get("schema_version")
    if schema_ver is not None and schema_ver != SCHEMA_VERSION:
        _log_self(
            root,
            f"view: run {run_dir.name}: cost.json schema_version={schema_ver} != "
            f"{SCHEMA_VERSION}; continuing with available fields",
        )
    return data


# ---------------------------------------------------------------------------
# Per-event summary helpers
# ---------------------------------------------------------------------------


def _summarize_tool_use(record: dict[str, Any], *, cap: int | None) -> str:
    tool_name = record.get("tool") or "?"
    raw = record.get("params_summary", "")
    try:
        params: Any = json.loads(raw) if raw else {}
        if not isinstance(params, dict):
            params = {}
    except (json.JSONDecodeError, TypeError):
        raw_str = str(raw) if raw else ""
        if cap and len(raw_str) > cap:
            raw_str = raw_str[:cap] + "…"
        return raw_str

    summarizer = _TOOL_SUMMARIZERS.get(str(tool_name), _default_summarizer)
    summary = summarizer(params)
    if cap and len(summary) > cap:
        summary = summary[:cap] + "…"
    return summary


def _format_duration_ms(ms: int | None) -> str:
    if ms is None:
        return "-"
    total_seconds = int(ms / 1000)
    if total_seconds < 0:
        return "-"
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return "".join(parts)


def _total_tokens(usage: Any) -> str:
    if not isinstance(usage, dict):
        return "?"
    total = (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("output_tokens") or 0)
        + int(usage.get("cache_read_tokens") or 0)
        + int(usage.get("cache_creation_tokens") or 0)
    )
    return f"{total:,}"


def _raw_size(record: dict[str, Any]) -> str:
    try:
        return str(len(json.dumps(record)))
    except Exception:  # noqa: BLE001
        return "?"


def _summarize_event(record: dict[str, Any], *, no_truncate: bool) -> str:
    kind = record.get("event", "unknown")
    cap_text = None if no_truncate else _DISPLAY_CAP_TEXT
    cap_tool = None if no_truncate else _DISPLAY_CAP_TOOL

    if kind == "session_start":
        return f"cwd={record.get('cwd') or '?'}"
    elif kind in ("prompt", "assistant_text"):
        text = _strip_ansi(str(record.get("text") or ""))
        text = text.replace("\n", " ")
        if cap_text and len(text) > cap_text:
            text = text[:cap_text] + "…"
        return text
    elif kind == "tool_use":
        tool = record.get("tool") or "?"
        tool_str = f"{tool!s:<{_TOOL_NAME_PAD}}"
        summary = _summarize_tool_use(record, cap=cap_tool)
        return f"{tool_str}  {summary}"
    elif kind == "stop":
        dur = _format_duration_ms(record.get("duration_ms"))
        tokens = _total_tokens(record.get("usage"))
        return f"{dur} elapsed | {tokens} tokens"
    elif kind == "session_end":
        return str(record.get("summary") or "")
    else:
        original = record.get("original_type") or record.get("original_event") or "?"
        return f"original_type={original} (raw size: {_raw_size(record)})"


# ---------------------------------------------------------------------------
# Timestamp formatter for timeline rows
# ---------------------------------------------------------------------------


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "??:??:??Z"
    try:
        dt = datetime.fromisoformat(str(ts))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.strftime("%H:%M:%SZ")
    except ValueError:
        return (ts[:9] if len(ts) >= 9 else ts)


# ---------------------------------------------------------------------------
# Rich renderers (all import rich locally; only called after the import gate)
# ---------------------------------------------------------------------------


def _render_header_rich(
    console: Any,
    state: dict[str, Any],
    run_id: str,
    event_count: int,
    cost_total_str: str,
) -> None:
    import rich.box
    from rich.panel import Panel
    from rich.text import Text

    source = state.get("source") or "-"
    model = state.get("model") or "-"
    cwd = state.get("cwd") or "-"
    started = _started_display(state.get("started_at"))
    duration = _format_duration(state.get("started_at"), state.get("ended_at"))

    lines = [
        f"Source:    {source}",
        f"Model:     {model}",
        f"Cwd:       {cwd}",
        f"Started:   {started}",
        f"Duration:  {duration}",
        f"Events:    {event_count}",
        f"Cost:      {cost_total_str}",
    ]

    body = Text("\n".join(lines))
    panel = Panel(body, title=run_id, box=rich.box.HEAVY)
    console.print(panel)


def _render_timeline_rich(
    console: Any,
    events: list[dict[str, Any]],
    *,
    limit: int,
    no_truncate: bool,
) -> None:
    from rich.text import Text

    console.print(Text("TIMELINE", style="bold"))

    if not events:
        console.print("  (no events recorded)")
        return

    hidden_count = 0
    display_events = events
    if limit > 0 and len(events) > limit:
        hidden_count = len(events) - limit
        display_events = events[:limit]

    total = len(display_events)
    for i, record in enumerate(display_events):
        kind = str(record.get("event") or "unknown")
        ts = _fmt_ts(record.get("timestamp"))
        summary = _summarize_event(record, no_truncate=no_truncate)

        if total == 1:
            rail = "─"
        elif i == 0:
            rail = "┌"
        elif i == total - 1:
            rail = "└"
        else:
            rail = "│"

        kind_padded = kind.ljust(_EVENT_KIND_PAD)
        style = _EVENT_KIND_STYLES.get(kind, "dim")

        line = Text(no_wrap=False)
        line.append(f"{rail} {ts}  ")
        line.append(kind_padded, style=style)
        line.append(f"  {summary}")
        console.print(line)

    if hidden_count:
        console.print(f"… ({hidden_count} more events; use --limit 0 to see all)")


def _render_cost_footer_rich(
    console: Any,
    cost_record: dict[str, Any] | None,
) -> None:
    from rich.text import Text

    console.print(Text("COST", style="bold"))

    if cost_record is None:
        console.print("  (no cost data recorded)")
        return

    tokens = cost_record.get("tokens") or {}
    if not tokens:
        console.print("  (no cost data recorded)")
        return

    rates: dict[str, float] | None = cost_record.get("rates_per_million_usd")
    costs: dict[str, float] | None = cost_record.get("costs_usd")
    unknown_model = cost_record.get("cost_usd") is None

    row_label_col_w = max(len(_TOKEN_KIND_LABELS[k]) for k in _KIND_DISPLAY_ORDER)
    row_label_col_w = max(row_label_col_w, len("Total"))

    def fmt_tokens(n: int) -> str:
        return f"{n:,}"

    def fmt_rate(r: float) -> str:
        return f"${r:.2f}/1M"

    def fmt_cost(c: float) -> str:
        return f"${c:.4f}"

    kind_rows: list[tuple[str, str, str, str]] = []
    for kind in _KIND_DISPLAY_ORDER:
        label = _TOKEN_KIND_LABELS[kind]
        tok_str = fmt_tokens(int(tokens.get(kind) or 0))
        if unknown_model:
            rate_str = "??"
            cost_str = "??"
        else:
            rate_str = fmt_rate(rates[kind]) if rates else "??"
            cost_str = fmt_cost(costs[kind]) if costs else "??"
        kind_rows.append((label, tok_str, rate_str, cost_str))

    total_tokens_str = fmt_tokens(int(tokens.get("total") or 0))
    total_cost_str = "??" if unknown_model else fmt_cost(cost_record.get("cost_usd") or 0.0)

    token_header = "Tokens"
    rate_header = "Rate (per 1M)"
    cost_header = "Cost"
    token_col_w = len(token_header)
    rate_col_w = len(rate_header)
    cost_col_w = len(cost_header)

    for _label, tok_str, rate_str, cost_str in kind_rows:
        token_col_w = max(token_col_w, len(tok_str))
        rate_col_w = max(rate_col_w, len(rate_str))
        cost_col_w = max(cost_col_w, len(cost_str))
    token_col_w = max(token_col_w, len(total_tokens_str))
    cost_col_w = max(cost_col_w, len(total_cost_str))

    def pad_row(label: str, tok: str, rate: str, cost_v: str) -> str:
        return (
            label.ljust(row_label_col_w)
            + "  "
            + tok.rjust(token_col_w)
            + "  "
            + rate.rjust(rate_col_w)
            + "  "
            + cost_v.rjust(cost_col_w)
        )

    header_line = pad_row("", token_header, rate_header, cost_header)
    sep_line = pad_row(
        "-" * row_label_col_w,
        "-" * token_col_w,
        "-" * rate_col_w,
        "-" * cost_col_w,
    )

    console.print(header_line)
    console.print(sep_line)
    for label, tok_str, rate_str, cost_str in kind_rows:
        console.print(pad_row(label, tok_str, rate_str, cost_str))
    console.print(sep_line)
    console.print(pad_row("Total", total_tokens_str, "", total_cost_str))

    model = cost_record.get("model")
    if unknown_model:
        console.print(
            f"\nnote: model {model!r} not in pricing table; cost cannot be computed."
        )
    elif cost_record.get("pricing_source") == "builtin":
        console.print(f"\n{_PRICING_STALENESS_FOOTER}")


# ---------------------------------------------------------------------------
# JSON renderer (stdlib only; must NOT import rich)
# ---------------------------------------------------------------------------


def _render_json(
    run_id: str,
    state: dict[str, Any],
    events: list[dict[str, Any]],
    cost_record: dict[str, Any] | None,
    cost_data: dict[str, Any] | None,
) -> str:
    cost_payload: dict[str, Any] = {
        "totals": (cost_data or {}).get("totals") or {},
        "pricing_source": (cost_record or {}).get("pricing_source") or "missing",
    }
    if cost_record is not None and cost_record.get("costs_usd") is not None:
        cost_payload["computed"] = cost_record["costs_usd"]
        cost_payload["cost_usd"] = cost_record["cost_usd"]
    else:
        cost_payload["computed"] = None

    return json.dumps(
        {
            "run_id": run_id,
            "state": state,
            "cost": cost_payload,
            "events": events,
        },
        indent=2,
        default=str,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_view(
    *,
    run_id: str,
    limit: int,
    events_only: bool,
    no_truncate: bool,
    as_json: bool,
) -> int:
    """Entry point for ``agentlog view``.

    Returns an integer exit code: 0=success, 1=I/O or missing-rich error, 2=user error.
    """
    root = _data_root()
    run_dir = root / RUNS_DIR_NAME / run_id

    state_path = run_dir / "state.json"
    if not state_path.exists():
        print(
            f"agentlog view: error: run id '{run_id}' not found at {run_dir}",
            file=sys.stderr,
        )
        return 2

    if limit < 0:
        print("agentlog view: error: --limit must be >= 0", file=sys.stderr)
        return 2

    try:
        state = _load_state(run_dir, root) or {}
        events = _load_events(run_dir, root)
        cost_data = _load_cost(run_dir, root)

        pricing, pricing_source_tag = _resolve_pricing(None, root)
        cost_record: dict[str, Any] | None = None
        if cost_data is not None:
            cost_record = _compute_run_cost(run_dir, pricing, pricing_source_tag, False, root)

        # --json mode: bypass rich entirely.
        # Gated import: --json mode must work without rich installed.
        if as_json:
            print(_render_json(run_id, state, events, cost_record, cost_data))
            return 0

        try:
            import rich.box  # noqa: F401
            from rich.console import Console
        except ImportError:
            print(
                "agentlog view requires the 'rich' library. Install with:\n"
                "    pip install 'agentlog[tui]'\n"
                "    # or:\n"
                "    uv pip install 'agentlog[tui]'",
                file=sys.stderr,
            )
            return 1

        if cost_record is None:
            cost_total_str = "-"
        elif cost_record.get("cost_usd") is None:
            cost_total_str = "??"
        else:
            cost_total_str = f"${cost_record['cost_usd']:.4f}"

        console = Console(highlight=False)

        if not events_only:
            _render_header_rich(console, state, run_id, len(events), cost_total_str)

        _render_timeline_rich(console, events, limit=limit, no_truncate=no_truncate)

        if not events_only:
            _render_cost_footer_rich(console, cost_record)

        return 0

    except (OSError, ValueError) as exc:
        _log_self(root, f"view: unexpected error: {exc!r}")
        print(f"agentlog view: error: {exc}", file=sys.stderr)
        return 1
