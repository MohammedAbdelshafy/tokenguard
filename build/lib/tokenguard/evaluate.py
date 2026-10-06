"""Spend evaluation: calendar-month totals vs budgets, alerts, breaches."""

import json
import urllib.request
from datetime import datetime, timezone

from .ledger import parse_timestamp


class BudgetResult:
    """Outcome for one budgeted key or project."""

    def __init__(self, kind, name, budget_usd, spent_usd, thresholds):
        self.kind = kind  # "key" or "project"
        self.name = name
        self.budget_usd = budget_usd
        self.spent_usd = spent_usd
        self.ratio = spent_usd / budget_usd if budget_usd else 0.0
        self.thresholds_crossed = sorted(
            t for t in thresholds if self.ratio >= t
        )
        self.breached = self.ratio >= 1.0

    @property
    def status(self):
        if self.breached:
            return "BREACH"
        if self.thresholds_crossed:
            highest = int(self.thresholds_crossed[-1] * 100)
            return "ALERT>=%d%%" % highest
        return "OK"


def totals_for_month(events, year, month):
    """Sum cost_usd per key and per project for one calendar month (UTC)."""
    key_totals = {}
    project_totals = {}
    for event in events:
        try:
            dt = parse_timestamp(event.get("timestamp"))
        except Exception:
            continue  # malformed timestamps are rejected at ingest time
        if dt.year != year or dt.month != month:
            continue
        try:
            cost = float(event.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            continue
        key = event.get("key")
        project = event.get("project")
        if key is not None:
            key_totals[key] = key_totals.get(key, 0.0) + cost
        if project is not None:
            project_totals[project] = project_totals.get(project, 0.0) + cost
    return key_totals, project_totals


def evaluate(config, key_totals, project_totals, only_key=None, only_project=None):
    """Evaluate all (or filtered) budgets. Returns [BudgetResult]."""
    results = []
    for name, budget in sorted(config.keys.items()):
        if only_key is not None and name != only_key:
            continue
        results.append(
            BudgetResult(
                "key", name, budget, key_totals.get(name, 0.0), config.alert_at
            )
        )
    for name, budget in sorted(config.projects.items()):
        if only_project is not None and name != only_project:
            continue
        results.append(
            BudgetResult(
                "project",
                name,
                budget,
                project_totals.get(name, 0.0),
                config.alert_at,
            )
        )
    return results


def render_table(results, year, month):
    lines = ["TokenGuard check — month %04d-%02d (UTC)" % (year, month)]
    if not results:
        lines.append("(no budgets matched)")
        return "\n".join(lines)
    header = "%-8s %-20s %10s %10s %7s  %s" % (
        "type",
        "name",
        "budget",
        "spent",
        "pct",
        "status",
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        lines.append(
            "%-8s %-20s %10s %10s %6.1f%%  %s"
            % (
                r.kind,
                r.name[:20],
                "$%.2f" % r.budget_usd,
                "$%.2f" % r.spent_usd,
                r.ratio * 100,
                r.status,
            )
        )
    return "\n".join(lines)


def render_alert_lines(results):
    """ALERT lines for budgets that crossed a threshold but are not breached."""
    lines = []
    for r in results:
        if r.breached or not r.thresholds_crossed:
            continue
        for t in r.thresholds_crossed:
            lines.append(
                "ALERT %s=%s spent $%.2f of $%.2f (%.1f%%) — crossed %d%% threshold"
                % (
                    r.kind,
                    r.name,
                    r.spent_usd,
                    r.budget_usd,
                    r.ratio * 100,
                    int(t * 100),
                )
            )
    return lines


def render_breach_lines(results):
    lines = []
    for r in results:
        if r.breached:
            lines.append(
                "BREACH %s=%s spent $%.2f of $%.2f (%.1f%%) — monthly budget exceeded"
                % (
                    r.kind,
                    r.name,
                    r.spent_usd,
                    r.budget_usd,
                    r.ratio * 100,
                )
            )
    return lines


def breach_payload(results, year, month):
    breaches = [
        {
            "type": r.kind,
            "name": r.name,
            "budget_usd": round(r.budget_usd, 2),
            "spent_usd": round(r.spent_usd, 2),
            "ratio": round(r.ratio, 4),
        }
        for r in results
        if r.breached
    ]
    return {
        "source": "tokenguard",
        "month": "%04d-%02d" % (year, month),
        "breaches": breaches,
        "text": "TokenGuard: %d budget breach(es) in %04d-%02d: %s"
        % (
            len(breaches),
            year,
            month,
            ", ".join("%s=%s" % (b["type"], b["name"]) for b in breaches),
        ),
    }


def post_webhook(url, payload, timeout=5):
    """Best-effort webhook POST. Returns True on 2xx, False otherwise.

    Never raises: network failures are the caller's warning, not a crash.
    """
    try:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def current_year_month():
    now = datetime.now(timezone.utc)
    return now.year, now.month


def parse_month(text):
    """Parse 'YYYY-MM' into (year, month)."""
    try:
        dt = datetime.strptime(text.strip(), "%Y-%m")
    except ValueError:
        raise ValueError(
            "invalid --month %r: expected format YYYY-MM" % (text,)
        )
    return dt.year, dt.month
