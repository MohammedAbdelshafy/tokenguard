"""TokenGuard — API spend guardrails: budgets, alerts, hard-stop enforcement.

Enforcement, not analytics: set per-key / per-project monthly budgets, ingest
usage events into a local ledger, get threshold alerts, and gate commands or
CI steps on a non-zero exit when a budget is breached.
"""

__version__ = "0.1.0"
