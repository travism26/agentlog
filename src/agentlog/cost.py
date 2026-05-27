"""Token-to-dollar rollup for agentlog runs.

Six invariants pinned for future contributors:

1. Read-only with respect to ``runs/`` AND the SQLite index from item #4.
   ``cost`` walks ``runs/`` directly with ``Path.iterdir()``; it MUST NOT
   read or write ``index.sqlite3`` — it is independent of ``ls`` by design.
2. Fail-loud user CLI: rc=2 for user error (bad run id, missing pricing
   file, mutual-exclusion violation), rc=1 for unexpected I/O failures,
   rc=0 for success and gracefully-handled missing data.
3. Stdlib-only.  No new runtime dependencies.  ``pyproject.toml
   dependencies = []`` stays empty.
4. Local-first.  No network calls.  Pricing comes from a static built-in
   table or a user-supplied file — never fetched from anthropic.com.
5. Per-phase breakdown deferred to v0.2+.  ``cost.json::phases`` is
   tolerated but ignored here.
6. Built-in pricing is dated 2026-05-27.  Accuracy is the user's
   responsibility.  The staleness footer in plain output and the inline
   comment below make this explicit.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agentlog._constants import (
    DEFAULT_DATA_ROOT_NAME,
    PRICING_FILE_NAME,
    RUNS_DIR_NAME,
    SCHEMA_VERSION,
    SELF_LOG_NAME,
)
from agentlog.ls import _format_duration, _started_display

# ---------------------------------------------------------------------------
# Helpers (duplicated from ls.py — inverted failure contract)
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
# Built-in pricing table
# ---------------------------------------------------------------------------

# $ per million tokens. Source: anthropic.com/pricing as of 2026-05-27.
# Override with --pricing <path>, $AGENTLOG_PRICING, or $AGENTLOG_HOME/pricing.json.
BUILTIN_PRICING_PER_MILLION: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "cache_creation": 18.75,
    },
    "claude-opus-4-6": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,
        "cache_creation": 18.75,
    },
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_creation": 3.75,
    },
    "claude-sonnet-4-5": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_creation": 3.75,
    },
    "claude-haiku-4-5": {
        "input": 0.80,
        "output": 4.00,
        "cache_read": 0.08,
        "cache_creation": 1.00,
    },
}

_PRICING_STALENESS_FOOTER = (
    "pricing snapshot: built-in (2026-05-27). "
    "Override with --pricing <file> or $AGENTLOG_PRICING."
)

_TOKEN_KINDS = ("input", "output", "cache_read", "cache_creation")

_TOKEN_KIND_LABELS = {
    "input": "Input",
    "output": "Output",
    "cache_read": "Cache read",
    "cache_creation": "Cache create",
}

_TOKEN_FIELD_MAP = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_tokens",
    "cache_creation": "cache_creation_tokens",
}


# ---------------------------------------------------------------------------
# Pricing resolution
# ---------------------------------------------------------------------------


class _PricingError(Exception):
    """User-visible pricing error (rc=2)."""


def _load_pricing_file(path: Path, root: Path) -> dict[str, dict[str, float]]:
    """Parse a user pricing JSON file. Validates gently: missing/bad kinds
    default to 0.0 and log to _self.log. Returns the (possibly partial) table."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _PricingError(f"pricing file not found: {path}") from exc

    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _PricingError(f"invalid JSON in pricing file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise _PricingError(f"invalid JSON in pricing file {path}: expected an object")

    result: dict[str, dict[str, float]] = {}
    for model_id, entry in raw.items():
        if not isinstance(entry, dict):
            _log_self(root, f"cost: pricing file {path}: model {model_id!r}: entry is not an object; skipped")
            continue
        row: dict[str, float] = {}
        for kind in _TOKEN_KINDS:
            raw_val = entry.get(kind)
            if raw_val is None:
                _log_self(root, f"cost: pricing file {path}: model {model_id!r}: missing kind {kind!r}; defaulting to 0.0")
                row[kind] = 0.0
            else:
                try:
                    val = float(raw_val)
                except (TypeError, ValueError):
                    _log_self(root, f"cost: pricing file {path}: model {model_id!r}: kind {kind!r} is not numeric; defaulting to 0.0")
                    val = 0.0
                if val < 0:
                    _log_self(root, f"cost: pricing file {path}: model {model_id!r}: kind {kind!r} is negative ({val}); defaulting to 0.0")
                    val = 0.0
                row[kind] = val
        result[model_id] = row
    return result


def _merge_pricing(
    builtin: dict[str, dict[str, float]],
    user: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """Deep-merge user onto builtin. User keys win on collision at the model
    level (a user entry replaces the entire model row). Models in builtin
    absent from user are preserved."""
    merged = dict(builtin)
    merged.update(user)
    return merged


def _resolve_pricing(
    pricing_flag: Path | None,
    root: Path,
) -> tuple[dict[str, dict[str, float]], str]:
    """Resolve the pricing table from (in priority order):
    1. --pricing PATH flag
    2. $AGENTLOG_PRICING env var
    3. $AGENTLOG_HOME/pricing.json
    4. BUILTIN_PRICING_PER_MILLION (fallback)

    Returns (merged_table, source_tag) where source_tag is "builtin" if no
    user file was found, or "file:<absolute-path>" if one was used.

    Raises _PricingError on hard failures (missing --pricing path, invalid JSON).
    Env-var and home-file paths that don't exist are silently treated as absent.
    """
    user_path: Path | None = None

    if pricing_flag is not None:
        if not pricing_flag.exists():
            raise _PricingError(f"pricing file not found: {pricing_flag}")
        user_path = pricing_flag

    if user_path is None:
        env_val = os.environ.get("AGENTLOG_PRICING")
        if env_val:
            candidate = Path(env_val)
            if candidate.exists():
                user_path = candidate

    if user_path is None:
        home_candidate = root / PRICING_FILE_NAME
        if home_candidate.exists():
            user_path = home_candidate

    if user_path is None:
        return dict(BUILTIN_PRICING_PER_MILLION), "builtin"

    user_table = _load_pricing_file(user_path, root)
    merged = _merge_pricing(BUILTIN_PRICING_PER_MILLION, user_table)
    return merged, f"file:{user_path.resolve()}"


# ---------------------------------------------------------------------------
# Per-run computation
# ---------------------------------------------------------------------------


def _read_json_safe(path: Path, root: Path, label: str) -> dict[str, Any]:
    """Read a JSON file. Returns {} on missing file or parse failure (logged)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        _log_self(root, f"cost: failed to parse {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        _log_self(root, f"cost: {label}: {path} is not a JSON object; ignored")
        return {}
    return data


def _compute_run_cost(
    run_dir: Path,
    pricing: dict[str, dict[str, float]],
    pricing_source_tag: str,
    no_cache_cost: bool,
    root: Path,
) -> dict[str, Any]:
    """Read state.json + cost.json for run_dir and compute per-kind costs.

    Returned dict shape (all fields present, nullable as documented):
      run_id: str
      session_id: str | None
      source: str | None
      model: str | None
      started_at: str | None
      ended_at: str | None
      duration_seconds: int | None
      tokens: dict[str, int]   # input/output/cache_read/cache_creation/total
      rates_per_million_usd: dict[str, float] | None   # None if unknown model
      costs_usd: dict[str, float] | None               # None if unknown model
      cost_usd: float | None
      pricing_source: str   # "builtin" | "file:<path>" | "missing"
      cost_unknown_reason: str | None
    """
    run_id = run_dir.name

    state_path = run_dir / "state.json"
    state = _read_json_safe(state_path, root, f"run {run_id} state.json")

    schema_ver = state.get("schema_version")
    if schema_ver is not None and schema_ver != SCHEMA_VERSION:
        _log_self(root, f"cost: run {run_id}: state.json schema_version={schema_ver} != {SCHEMA_VERSION}; continuing with available fields")

    session_id: str | None = state.get("session_id")
    source: str | None = state.get("source")
    model: str | None = state.get("model") or None
    started_at: str | None = state.get("started_at")
    ended_at: str | None = state.get("ended_at")

    duration_seconds: int | None = None
    if started_at and ended_at:
        try:
            start_dt = datetime.fromisoformat(started_at)
            end_dt = datetime.fromisoformat(ended_at)
            duration_seconds = max(0, int((end_dt - start_dt).total_seconds()))
        except ValueError:
            pass

    cost_path = run_dir / "cost.json"
    cost_data = _read_json_safe(cost_path, root, f"run {run_id} cost.json")

    cost_schema_ver = cost_data.get("schema_version")
    if cost_schema_ver is not None and cost_schema_ver != SCHEMA_VERSION:
        _log_self(root, f"cost: run {run_id}: cost.json schema_version={cost_schema_ver} != {SCHEMA_VERSION}; continuing with available fields")

    raw_totals: dict[str, Any] = cost_data.get("totals") or {}
    tokens: dict[str, int] = {}
    for kind, field in _TOKEN_FIELD_MAP.items():
        tokens[kind] = int(raw_totals.get(field) or 0)
    tokens["total"] = sum(tokens.values())

    if not model or model not in pricing:
        return {
            "run_id": run_id,
            "session_id": session_id,
            "source": source,
            "model": model,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": duration_seconds,
            "tokens": tokens,
            "rates_per_million_usd": None,
            "costs_usd": None,
            "cost_usd": None,
            "pricing_source": "missing",
            "cost_unknown_reason": "model not in pricing table",
        }

    model_rates = pricing[model]
    rates: dict[str, float] = {k: model_rates.get(k, 0.0) for k in _TOKEN_KINDS}

    costs: dict[str, float] = {}
    for kind in _TOKEN_KINDS:
        token_count = tokens[kind]
        rate = rates[kind]
        if no_cache_cost and kind == "cache_creation":
            costs[kind] = 0.0
        else:
            costs[kind] = token_count * rate / 1_000_000.0

    cost_usd = sum(costs.values())

    return {
        "run_id": run_id,
        "session_id": session_id,
        "source": source,
        "model": model,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "tokens": tokens,
        "rates_per_million_usd": rates,
        "costs_usd": costs,
        "cost_usd": cost_usd,
        "pricing_source": pricing_source_tag,
        "cost_unknown_reason": None,
    }


# ---------------------------------------------------------------------------
# Plain-text formatters
# ---------------------------------------------------------------------------

_KIND_DISPLAY_ORDER = ("input", "output", "cache_read", "cache_creation")


def _format_single_plain(record: dict[str, Any]) -> str:
    """Format a single-run cost record as a plain-text table."""
    lines: list[str] = []

    run_id: str = record["run_id"]
    source: str = record["source"] or "-"
    model: str = record["model"] or "-"
    started: str = _started_display(record["started_at"])
    duration: str = _format_duration(record["started_at"], record["ended_at"])

    lines.append(f"Run:      {run_id}")
    lines.append(f"Source:   {source}")
    lines.append(f"Model:    {model}")
    lines.append(f"Started:  {started}")
    lines.append(f"Duration: {duration}")
    lines.append("")

    tokens = record["tokens"]
    rates = record["rates_per_million_usd"]
    costs = record["costs_usd"]
    unknown_model = record["cost_usd"] is None

    row_label_col_w = max(len(_TOKEN_KIND_LABELS[k]) for k in _KIND_DISPLAY_ORDER)
    row_label_col_w = max(row_label_col_w, len("Total"))

    token_header = "Tokens"
    rate_header = "Rate (per 1M)"
    cost_header = "Cost"

    token_col_w = len(token_header)
    rate_col_w = len(rate_header)
    cost_col_w = len(cost_header)

    def fmt_tokens(n: int) -> str:
        return f"{n:,}"

    def fmt_rate(r: float) -> str:
        return f"${r:.2f}/1M"

    def fmt_cost(c: float) -> str:
        return f"${c:.4f}"

    kind_rows: list[tuple[str, str, str, str]] = []
    for kind in _KIND_DISPLAY_ORDER:
        label = _TOKEN_KIND_LABELS[kind]
        tok_str = fmt_tokens(tokens[kind])
        if unknown_model:
            rate_str = "??"
            cost_str = "??"
        else:
            rate_str = fmt_rate(rates[kind])
            cost_str = fmt_cost(costs[kind])
        kind_rows.append((label, tok_str, rate_str, cost_str))

    total_tokens_str = fmt_tokens(tokens["total"])
    total_cost_str = "??" if unknown_model else fmt_cost(record["cost_usd"])

    for _label, tok_str, rate_str, cost_str in kind_rows:
        token_col_w = max(token_col_w, len(tok_str))
        rate_col_w = max(rate_col_w, len(rate_str))
        cost_col_w = max(cost_col_w, len(cost_str))
    token_col_w = max(token_col_w, len(total_tokens_str))
    cost_col_w = max(cost_col_w, len(total_cost_str))

    def pad_row(label: str, tok: str, rate: str, cost: str) -> str:
        return (
            label.ljust(row_label_col_w)
            + "  "
            + tok.rjust(token_col_w)
            + "  "
            + rate.rjust(rate_col_w)
            + "  "
            + cost.rjust(cost_col_w)
        )

    header_line = pad_row("", token_header, rate_header, cost_header)
    sep_line = pad_row(
        "-" * row_label_col_w,
        "-" * token_col_w,
        "-" * rate_col_w,
        "-" * cost_col_w,
    )

    lines.append(header_line)
    lines.append(sep_line)
    for label, tok_str, rate_str, cost_str in kind_rows:
        lines.append(pad_row(label, tok_str, rate_str, cost_str))
    lines.append(sep_line)
    lines.append(pad_row("Total", total_tokens_str, "", total_cost_str))

    if unknown_model:
        lines.append("")
        lines.append(
            f"note: model {model!r} not in pricing table; cost cannot be computed. "
            "Set $AGENTLOG_PRICING or pass --pricing."
        )
    elif record["pricing_source"] == "builtin":
        lines.append("")
        lines.append(_PRICING_STALENESS_FOOTER)

    return "\n".join(lines)


def _format_all_plain(
    records: list[dict[str, Any]],
    runs_root: Path,
    pricing_source_tag: str,
) -> str:
    """Format a cross-run cost rollup as a plain-text table."""
    if not records:
        return f"no runs found at {runs_root}"

    def sort_key(r: dict[str, Any]) -> tuple[float, float]:
        # Primary: cost desc. Unknown-cost rows sort LAST (+inf in ascending).
        # Tiebreaker: started_at desc (newer wins). Convert ISO → UNIX seconds
        # and negate so newer becomes more negative (sorts first under asc).
        cost = r["cost_usd"]
        primary = float("inf") if cost is None else -float(cost)
        started = r["started_at"] or ""
        try:
            started_ts = datetime.fromisoformat(started).timestamp() if started else 0.0
        except ValueError:
            started_ts = 0.0
        return (primary, -started_ts)

    sorted_records = sorted(records, key=sort_key)

    run_id_header = "RUN ID"
    model_header = "MODEL"
    tokens_header = "TOKENS"
    cost_header = "COST"

    id_col_w = len(run_id_header)
    model_col_w = len(model_header)
    tokens_col_w = len(tokens_header)
    cost_col_w = len(cost_header)

    def fmt_tokens(n: int) -> str:
        return f"{n:,}"

    def fmt_cost(c: float | None) -> str:
        if c is None:
            return "??"
        return f"${c:.4f}"

    row_data: list[tuple[str, str, str, str]] = []
    for r in sorted_records:
        run_id = str(r["run_id"])
        model = str(r["model"] or "-")
        tok_str = fmt_tokens(r["tokens"]["total"])
        cost_str = fmt_cost(r["cost_usd"])
        row_data.append((run_id, model, tok_str, cost_str))
        id_col_w = max(id_col_w, len(run_id))
        model_col_w = max(model_col_w, len(model))
        tokens_col_w = max(tokens_col_w, len(tok_str))
        cost_col_w = max(cost_col_w, len(cost_str))

    total_tokens = sum(r["tokens"]["total"] for r in records)
    known_cost_records = [r for r in records if r["cost_usd"] is not None]
    total_cost: float = sum(r["cost_usd"] for r in known_cost_records)
    unknown_count = len(records) - len(known_cost_records)

    total_tok_str = fmt_tokens(total_tokens)
    total_cost_str = fmt_cost(total_cost) if known_cost_records else "??"

    total_label = f"TOTAL  ({len(records)} runs)"
    id_col_w = max(id_col_w, len(total_label))
    tokens_col_w = max(tokens_col_w, len(total_tok_str))
    cost_col_w = max(cost_col_w, len(total_cost_str))

    def pad_row(run_id: str, model: str, tok: str, cost: str) -> str:
        return (
            run_id.ljust(id_col_w)
            + "  "
            + model.ljust(model_col_w)
            + "  "
            + tok.rjust(tokens_col_w)
            + "  "
            + cost.rjust(cost_col_w)
        )

    sep_line = pad_row(
        "-" * id_col_w, "-" * model_col_w, "-" * tokens_col_w, "-" * cost_col_w
    )

    lines: list[str] = []
    lines.append(pad_row(run_id_header, model_header, tokens_header, cost_header))
    lines.append(sep_line)
    for run_id, model, tok_str, cost_str in row_data:
        lines.append(pad_row(run_id, model, tok_str, cost_str))
    lines.append(sep_line)

    summary_line = pad_row(total_label, "", total_tok_str, total_cost_str)
    if unknown_count > 0:
        summary_line += f"  (unknown cost: {unknown_count} runs)"
    lines.append(summary_line)

    lines.append("")
    if pricing_source_tag.startswith("file:"):
        lines.append(f"pricing source: {pricing_source_tag[5:]}")
    else:
        lines.append(_PRICING_STALENESS_FOOTER)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON formatters
# ---------------------------------------------------------------------------


def _format_single_json(record: dict[str, Any]) -> str:
    """Format a single-run cost record as JSON."""
    payload: dict[str, Any] = {
        "run_id": record["run_id"],
        "session_id": record["session_id"],
        "source": record["source"],
        "model": record["model"],
        "started_at": record["started_at"],
        "ended_at": record["ended_at"],
        "duration_seconds": record["duration_seconds"],
        "tokens": record["tokens"],
        "pricing_source": record["pricing_source"],
    }

    if record["cost_usd"] is None:
        payload["cost_unknown_reason"] = record["cost_unknown_reason"]
    else:
        payload["rates_per_million_usd"] = record["rates_per_million_usd"]
        payload["costs_usd"] = record["costs_usd"]
        payload["cost_usd"] = record["cost_usd"]

    return json.dumps(payload, indent=2)


def _format_all_json(records: list[dict[str, Any]]) -> str:
    """Format a cross-run cost rollup as JSON."""
    run_list: list[dict[str, Any]] = []
    for r in records:
        row: dict[str, Any] = {
            "run_id": r["run_id"],
            "session_id": r["session_id"],
            "source": r["source"],
            "model": r["model"],
            "started_at": r["started_at"],
            "ended_at": r["ended_at"],
            "duration_seconds": r["duration_seconds"],
            "tokens": r["tokens"],
            "pricing_source": r["pricing_source"],
        }
        if r["cost_usd"] is None:
            row["cost_unknown_reason"] = r["cost_unknown_reason"]
        else:
            row["rates_per_million_usd"] = r["rates_per_million_usd"]
            row["costs_usd"] = r["costs_usd"]
            row["cost_usd"] = r["cost_usd"]
        run_list.append(row)

    total_tokens = sum(r["tokens"]["total"] for r in records)
    known_cost_records = [r for r in records if r["cost_usd"] is not None]
    total_cost: float = sum(r["cost_usd"] for r in known_cost_records)
    unknown_count = len(records) - len(known_cost_records)

    summary: dict[str, Any] = {
        "run_count": len(records),
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost if known_cost_records else None,
    }
    if unknown_count > 0:
        summary["unknown_cost_runs"] = unknown_count

    return json.dumps({"runs": run_list, "summary": summary}, indent=2)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def run_cost(
    *,
    run_id: str | None,
    all_: bool,
    source: str,
    since: timedelta | None,
    pricing_path: Path | None,
    as_json: bool,
    no_cache_cost: bool,
) -> int:
    """Entry point for ``agentlog cost``.

    Returns an integer exit code: 0=success, 1=I/O error, 2=user error.
    """
    root = _data_root()
    runs_root = root / RUNS_DIR_NAME

    # Validate flag combinations before any I/O.
    if run_id is not None and all_:
        print(
            f"agentlog cost: error: '{run_id}' and --all are mutually exclusive",
            file=sys.stderr,
        )
        return 2
    if run_id is None and not all_:
        print(
            "agentlog cost: error: provide a run id or --all",
            file=sys.stderr,
        )
        return 2
    if not all_ and since is not None:
        print(
            "agentlog cost: error: --since is only valid with --all",
            file=sys.stderr,
        )
        return 2
    if not all_ and source != "all":
        print(
            "agentlog cost: error: --source is only valid with --all",
            file=sys.stderr,
        )
        return 2

    # Resolve pricing before walking runs.
    try:
        pricing, pricing_source_tag = _resolve_pricing(pricing_path, root)
    except _PricingError as exc:
        print(f"agentlog cost: error: {exc}", file=sys.stderr)
        return 2

    try:
        if run_id is not None:
            # Single-run path.
            run_dir = runs_root / run_id
            state_path = run_dir / "state.json"
            if not state_path.exists():
                print(
                    f"agentlog cost: error: run id '{run_id}' not found at {run_dir}",
                    file=sys.stderr,
                )
                return 2
            record = _compute_run_cost(run_dir, pricing, pricing_source_tag, no_cache_cost, root)
            if as_json:
                print(_format_single_json(record))
            else:
                print(_format_single_plain(record))
            return 0

        # --all path.
        if not runs_root.exists():
            if as_json:
                print(_format_all_json([]))
            else:
                print(f"no runs found at {runs_root}")
            return 0

        now = datetime.now(UTC)
        records: list[dict[str, Any]] = []

        for entry in sorted(runs_root.iterdir()):
            if not entry.is_dir():
                continue
            state_path = entry / "state.json"
            if not state_path.exists():
                continue

            # Apply --source filter before reading full state.
            if source != "all":
                try:
                    raw_state: Any = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(raw_state, dict):
                    continue
                entry_source = raw_state.get("source")
                if entry_source != source:
                    continue

            # Apply --since filter.
            if since is not None:
                try:
                    raw_state2: Any = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(raw_state2, dict):
                    continue
                started_at_str: str | None = raw_state2.get("started_at")
                if not started_at_str:
                    continue
                try:
                    started_dt = datetime.fromisoformat(started_at_str)
                    if started_dt.tzinfo is None:
                        started_dt = started_dt.replace(tzinfo=UTC)
                except ValueError:
                    continue
                cutoff = now - since
                if started_dt < cutoff:
                    continue

            record = _compute_run_cost(entry, pricing, pricing_source_tag, no_cache_cost, root)
            records.append(record)

        if not records and (source != "all" or since is not None):
            if as_json:
                print(_format_all_json([]))
            else:
                print("no runs match the filter")
            return 0

        if as_json:
            print(_format_all_json(records))
        else:
            print(_format_all_plain(records, runs_root, pricing_source_tag))
        return 0

    except (OSError, ValueError) as exc:
        _log_self(root, f"cost: unexpected error: {exc!r}")
        print(f"agentlog cost: error: {exc}", file=sys.stderr)
        return 1
