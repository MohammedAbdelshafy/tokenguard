"""Usage-event ingestion into a local append-only JSONL ledger."""

import json
import math
import os
from datetime import datetime, timezone


class LedgerError(Exception):
    """Raised when a usage or ledger file cannot be read."""


class PriceError(Exception):
    """Raised when a cost cannot be determined for an event."""


DEFAULT_LEDGER_DIR = os.path.join(os.path.expanduser("~"), ".tokenguard")
DEFAULT_LEDGER_PATH = os.path.join(DEFAULT_LEDGER_DIR, "ledger.jsonl")


def find_ledger(explicit=None):
    """Resolve the ledger path: explicit flag > env > ~/.tokenguard/ledger.jsonl."""
    if explicit:
        return explicit
    return os.environ.get("TOKENGUARD_LEDGER", DEFAULT_LEDGER_PATH)


def parse_timestamp(value):
    """Parse a timestamp into an aware UTC datetime.

    Accepts ISO-8601 strings (with 'Z' or numeric offset), or epoch
    int/float seconds. Naive datetimes are assumed to be UTC.
    """
    if isinstance(value, bool):
        raise LedgerError("invalid timestamp: %r" % (value,))
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise LedgerError("invalid timestamp: %r" % (value,))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise LedgerError("invalid timestamp: %r" % (value,))


def _as_number(value, what, source, lineno):
    """Coerce value to a finite float.

    Rejects booleans (float(True) == 1.0 would silently invent spend),
    non-numeric values, and NaN/inf (a NaN cost poisons every total it
    touches, and inf is not a real spend figure).
    """
    if isinstance(value, bool):
        raise PriceError(
            "%s line %d: %s must be a number, got %r"
            % (source, lineno, what, value)
        )
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PriceError(
            "%s line %d: %s must be a number, got %r"
            % (source, lineno, what, value)
        )
    if not math.isfinite(number):
        raise PriceError(
            "%s line %d: %s must be finite, got %r"
            % (source, lineno, what, value)
        )
    return number


def event_cost_usd(event, prices, source, lineno):
    """Return the event cost in USD.

    Uses cost_usd when present, otherwise computes it from tokens_in /
    tokens_out with the price table (per-model entry, else default).
    Token counts must be non-negative; an explicit cost_usd may be
    negative (models a provider credit/adjustment).
    """
    raw_cost = event.get("cost_usd")
    if raw_cost is not None:
        return _as_number(raw_cost, "cost_usd", source, lineno)
    tokens_in = _as_number(
        event.get("tokens_in") or 0, "tokens_in", source, lineno
    )
    tokens_out = _as_number(
        event.get("tokens_out") or 0, "tokens_out", source, lineno
    )
    if tokens_in < 0 or tokens_out < 0:
        raise PriceError(
            "%s line %d: tokens_in/tokens_out must be >= 0, got %r / %r"
            % (source, lineno, event.get("tokens_in"), event.get("tokens_out"))
        )
    model = event.get("model")
    entry = None
    if model is not None:
        entry = (prices or {}).get("models", {}).get(str(model))
    if entry is None:
        entry = (prices or {}).get("default")
    if entry is None:
        raise PriceError(
            "%s line %d: no cost_usd and no price available for model %r "
            "(add a prices.default entry to the config)"
            % (source, lineno, model)
        )
    return (
        tokens_in / 1000.0 * entry["input_per_1k"]
        + tokens_out / 1000.0 * entry["output_per_1k"]
    )


def load_ledger(path):
    """Read ledger events. Missing file -> empty list."""
    events = []
    if not os.path.isfile(path):
        if os.path.exists(path):
            raise LedgerError("ledger path is not a file: %s" % path)
        return events
    try:
        fh = open(path, "r", encoding="utf-8")
    except OSError as exc:
        raise LedgerError("cannot read ledger %s: %s" % (path, exc))
    with fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LedgerError(
                    "ledger %s line %d: invalid JSON: %s" % (path, lineno, exc)
                )
    return events


def normalize_event(raw, prices, source, lineno):
    """Validate and normalize one raw usage event dict."""
    if not isinstance(raw, dict):
        raise LedgerError(
            "%s line %d: each line must be a JSON object" % (source, lineno)
        )
    if "timestamp" not in raw:
        raise LedgerError(
            "%s line %d: missing required field 'timestamp'" % (source, lineno)
        )
    parse_timestamp(raw["timestamp"])  # validate now; keep original string
    key = raw.get("key")
    project = raw.get("project")
    if key is None and project is None:
        raise LedgerError(
            "%s line %d: need at least one of 'key' or 'project'"
            % (source, lineno)
        )
    cost = event_cost_usd(raw, prices, source, lineno)
    event = {
        "timestamp": raw["timestamp"],
        "key": str(key) if key is not None else None,
        "project": str(project) if project is not None else None,
        "tokens_in": raw.get("tokens_in"),
        "tokens_out": raw.get("tokens_out"),
        "model": raw.get("model"),
        "cost_usd": round(cost, 6),
    }
    if raw.get("id") is not None:
        event["id"] = str(raw["id"])
    return event


def ingest_file(source_path, ledger_path, prices):
    """Append usage events from a JSONL file to the ledger.

    Deduplicates on the event 'id' field when present. Returns
    (ingested_count, skipped_count, warnings).
    """
    if not os.path.isfile(source_path):
        if os.path.exists(source_path):
            raise LedgerError("usage path is not a file: %s" % source_path)
        raise LedgerError("usage file not found: %s" % source_path)
    try:
        with open(source_path, "r", encoding="utf-8") as fh:
            raw_lines = fh.read().splitlines()
    except OSError as exc:
        raise LedgerError("cannot read usage file %s: %s" % (source_path, exc))

    existing_ids = {
        e.get("id") for e in load_ledger(ledger_path) if e.get("id") is not None
    }
    seen_ids = set()
    new_events = []
    skipped = 0
    warnings = []

    for lineno, line in enumerate(raw_lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            skipped += 1
            warnings.append(
                "%s line %d: invalid JSON, skipped (%s)"
                % (source_path, lineno, exc)
            )
            continue
        try:
            event = normalize_event(raw, prices, source_path, lineno)
        except (LedgerError, PriceError) as exc:
            skipped += 1
            warnings.append(
                "%s line %d: skipped: %s" % (source_path, lineno, exc)
            )
            continue
        event_id = event.get("id")
        if event_id is not None:
            if event_id in existing_ids or event_id in seen_ids:
                skipped += 1
                continue
            seen_ids.add(event_id)
        new_events.append(event)

    if new_events:
        directory = os.path.dirname(os.path.abspath(ledger_path))
        try:
            os.makedirs(directory, exist_ok=True)
            with open(ledger_path, "a", encoding="utf-8") as fh:
                for event in new_events:
                    fh.write(json.dumps(event, sort_keys=True) + "\n")
        except OSError as exc:
            raise LedgerError(
                "cannot append to ledger %s: %s" % (ledger_path, exc)
            )

    return len(new_events), skipped, warnings
