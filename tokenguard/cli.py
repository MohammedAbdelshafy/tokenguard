"""TokenGuard command-line interface."""

import argparse
import os
import subprocess
import sys

from . import __version__
from .config import ConfigError, find_config, load_config
from .evaluate import (
    breach_payload,
    current_year_month,
    evaluate,
    parse_month,
    post_webhook,
    render_alert_lines,
    render_breach_lines,
    render_table,
    totals_for_month,
)
from .ledger import LedgerError, PriceError, find_ledger, ingest_file, load_ledger

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BREACH = 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tokenguard",
        description=(
            "API spend guardrails: enforce per-key / per-project monthly "
            "budgets with alerts and hard stops."
        ),
        epilog=(
            "examples:\n"
            "  tokenguard ingest usage.jsonl\n"
            "  tokenguard check\n"
            "  tokenguard check --month 2026-09 --key sk-live-abc\n"
            "  tokenguard guard -- python3 expensive_job.py\n"
            "\n"
            "exit codes: 0 = within budget, 2 = budget breached, "
            "1 = config/usage error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version="tokenguard %s" % __version__
    )
    parser.add_argument(
        "--config",
        default=None,
        help="path to budgets.yaml/json (default: auto-discover)",
    )
    parser.add_argument(
        "--ledger",
        default=None,
        help="path to the usage ledger JSONL (default: ~/.tokenguard/ledger.jsonl)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser(
        "ingest", help="append usage events from a JSONL file to the ledger"
    )
    p_ingest.add_argument("usage_file", help="JSONL file of usage events")
    p_ingest.set_defaults(func=cmd_ingest)

    p_check = sub.add_parser(
        "check", help="evaluate spend vs budgets for a calendar month"
    )
    p_check.add_argument("--key", default=None, help="only check this key")
    p_check.add_argument(
        "--project", default=None, help="only check this project"
    )
    p_check.add_argument(
        "--month",
        default=None,
        help="month to evaluate as YYYY-MM (default: current month, UTC)",
    )
    p_check.set_defaults(func=cmd_check)

    p_guard = sub.add_parser(
        "guard",
        help="run a command only if budgets are not breached",
    )
    p_guard.add_argument("--key", default=None, help="only check this key")
    p_guard.add_argument(
        "--project", default=None, help="only check this project"
    )
    p_guard.add_argument(
        "--month",
        default=None,
        help="month to evaluate as YYYY-MM (default: current month, UTC)",
    )
    p_guard.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="command to run after '--' if budgets pass",
    )
    p_guard.set_defaults(func=cmd_guard)

    return parser


def _load_config_or_exit(args):
    path = find_config(args.config)
    if path is None:
        print(
            "tokenguard: no config found. Create budgets.yaml (see samples/) "
            "or pass --config.",
            file=sys.stderr,
        )
        return None
    try:
        return load_config(path)
    except ConfigError as exc:
        print("tokenguard: %s" % exc, file=sys.stderr)
        return None


def cmd_ingest(args):
    config = None
    config_path = find_config(args.config)
    if config_path is not None:
        try:
            config = load_config(config_path)
        except ConfigError as exc:
            print("tokenguard: %s" % exc, file=sys.stderr)
            return EXIT_ERROR
    prices = config.prices if config else {"default": None, "models": {}}
    ledger_path = find_ledger(args.ledger)
    try:
        ingested, skipped, warnings = ingest_file(
            args.usage_file, ledger_path, prices
        )
    except (LedgerError, PriceError) as exc:
        print("tokenguard: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    for warning in warnings:
        print("tokenguard: warning: %s" % warning, file=sys.stderr)
    print(
        "ingested %d event(s) into %s (%d skipped)"
        % (ingested, ledger_path, skipped)
    )
    return EXIT_OK


def _run_check(args):
    """Shared check logic. Returns (exit_code, message_for_guard)."""
    config = _load_config_or_exit(args)
    if config is None:
        return EXIT_ERROR, "config error"
    if args.month:
        try:
            year, month = parse_month(args.month)
        except ValueError as exc:
            print("tokenguard: %s" % exc, file=sys.stderr)
            return EXIT_ERROR, "bad --month"
    else:
        year, month = current_year_month()

    ledger_path = find_ledger(args.ledger)
    try:
        events = load_ledger(ledger_path)
    except LedgerError as exc:
        print("tokenguard: %s" % exc, file=sys.stderr)
        return EXIT_ERROR, "ledger error"
    if not os.path.isfile(ledger_path):
        print(
            "tokenguard: note: ledger %s not found, treating spend as $0"
            % ledger_path,
            file=sys.stderr,
        )

    key_totals, project_totals = totals_for_month(events, year, month)
    results = evaluate(
        config,
        key_totals,
        project_totals,
        only_key=args.key,
        only_project=args.project,
    )
    if (args.key or args.project) and not results:
        print(
            "tokenguard: no budget matched the given --key/--project filter",
            file=sys.stderr,
        )
        return EXIT_ERROR, "no matching budget"

    print(render_table(results, year, month))
    for line in render_alert_lines(results):
        print(line)
    breach_lines = render_breach_lines(results)
    if breach_lines:
        for line in breach_lines:
            print(line)
        if config.webhook_url:
            ok = post_webhook(
                config.webhook_url, breach_payload(results, year, month)
            )
            if not ok:
                print(
                    "tokenguard: warning: breach webhook POST failed "
                    "(best-effort, continuing)",
                    file=sys.stderr,
                )
        return EXIT_BREACH, "; ".join(breach_lines)
    return EXIT_OK, "budgets OK"


def cmd_check(args):
    exit_code, _ = _run_check(args)
    return exit_code


def cmd_guard(args):
    command = list(args.cmd)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print(
            "tokenguard: guard needs a command: tokenguard guard -- <command...>",
            file=sys.stderr,
        )
        return EXIT_ERROR
    exit_code, message = _run_check(args)
    if exit_code == EXIT_BREACH:
        print(
            "tokenguard: REFUSED to run '%s': %s"
            % (" ".join(command), message),
            file=sys.stderr,
        )
        return EXIT_BREACH
    if exit_code != EXIT_OK:
        print(
            "tokenguard: cannot verify budgets (%s); refusing to run '%s'"
            % (message, " ".join(command)),
            file=sys.stderr,
        )
        return EXIT_ERROR
    print("tokenguard: budgets OK — running: %s" % " ".join(command))
    try:
        completed = subprocess.run(command)
    except FileNotFoundError:
        print(
            "tokenguard: command not found: %s" % command[0], file=sys.stderr
        )
        return EXIT_ERROR
    except OSError as exc:
        print("tokenguard: failed to run command: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    return completed.returncode


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("tokenguard: interrupted", file=sys.stderr)
        return EXIT_ERROR
