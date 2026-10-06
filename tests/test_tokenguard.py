"""Tests for TokenGuard. Run: python3 -m unittest discover tests -v"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from tokenguard import yaml_subset  # noqa: E402
from tokenguard.config import Config, ConfigError, load_config  # noqa: E402
from tokenguard.evaluate import (  # noqa: E402
    evaluate,
    render_alert_lines,
    render_breach_lines,
    totals_for_month,
)
from tokenguard.ledger import (  # noqa: E402
    LedgerError,
    PriceError,
    ingest_file,
    load_ledger,
)

MONTH = "2026-09"


def make_config(tmp, text=None, as_json=False):
    if text is None:
        text = (
            "alert_at: [0.8, 0.95]\n"
            "prices:\n"
            "  default:\n"
            "    input_per_1k: 0.001\n"
            "    output_per_1k: 0.002\n"
            "keys:\n"
            "  key-A:\n"
            "    monthly_usd: 50\n"
            "  key-B:\n"
            "    monthly_usd: 10\n"
            "projects:\n"
            "  proj-X:\n"
            "    monthly_usd: 200\n"
        )
    path = os.path.join(tmp, "budgets.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def write_usage(tmp, events):
    path = os.path.join(tmp, "usage.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return path


def run_cli(tmp, *argv, timeout=60):
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT
    env["TOKENGUARD_CONFIG"] = os.path.join(tmp, "budgets.yaml")
    env["TOKENGUARD_LEDGER"] = os.path.join(tmp, "ledger.jsonl")
    return subprocess.run(
        [sys.executable, "-m", "tokenguard"] + list(argv),
        capture_output=True,
        text=True,
        cwd=tmp,
        env=env,
        timeout=timeout,
    )


def evt(ts, key=None, project=None, cost=None, eid=None, **extra):
    e = {"timestamp": ts}
    if key is not None:
        e["key"] = key
    if project is not None:
        e["project"] = project
    if cost is not None:
        e["cost_usd"] = cost
    if eid is not None:
        e["id"] = eid
    e.update(extra)
    return e


class YamlSubsetTest(unittest.TestCase):
    def test_nested_maps_lists_and_scalars(self):
        text = (
            "alert_at: [0.8, 0.95]\n"
            "webhook_url: https://example.com/hook\n"
            "prices:\n"
            "  default:\n"
            "    input_per_1k: 0.0015\n"
            "    output_per_1k: 0.006\n"
            "keys:\n"
            "  sk-proj-A:\n"
            "    monthly_usd: 50\n"
            "flag: true\n"
            "nothing: null\n"
        )
        parsed = yaml_subset.loads(text)
        self.assertEqual(parsed["alert_at"], [0.8, 0.95])
        self.assertEqual(
            parsed["webhook_url"], "https://example.com/hook"
        )
        self.assertEqual(
            parsed["prices"]["default"]["input_per_1k"], 0.0015
        )
        self.assertEqual(parsed["keys"]["sk-proj-A"]["monthly_usd"], 50)
        self.assertIs(parsed["flag"], True)
        self.assertIsNone(parsed["nothing"])

    def test_block_sequence(self):
        parsed = yaml_subset.loads("alert_at:\n  - 0.8\n  - 0.95\n")
        self.assertEqual(parsed["alert_at"], [0.8, 0.95])

    def test_sample_config_parses(self):
        with open(
            os.path.join(REPO_ROOT, "samples", "budgets.yaml"),
            encoding="utf-8",
        ) as fh:
            parsed = yaml_subset.loads(fh.read())
        self.assertEqual(parsed["keys"]["sk-sample-001"]["monthly_usd"], 50)
        self.assertEqual(parsed["projects"]["demo-app"]["monthly_usd"], 200)


class ConfigTest(unittest.TestCase):
    def test_load_yaml_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = make_config(tmp)
            cfg = load_config(yaml_path)
            self.assertEqual(cfg.keys["key-A"], 50.0)
            self.assertEqual(cfg.projects["proj-X"], 200.0)
            self.assertEqual(cfg.alert_at, [0.8, 0.95])

            raw = {
                "keys": {"k": {"monthly_usd": 5}},
                "alert_at": [0.5],
            }
            json_path = os.path.join(tmp, "budgets.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            cfg2 = load_config(json_path)
            self.assertEqual(cfg2.keys["k"], 5.0)
            self.assertEqual(cfg2.alert_at, [0.5])

    def test_invalid_config_rejected(self):
        with self.assertRaises(ConfigError):
            Config({"keys": {"k": {}}}, "test")
        with self.assertRaises(ConfigError):
            Config({"keys": {"k": {"monthly_usd": -1}}}, "test")
        with self.assertRaises(ConfigError):
            Config({"alert_at": [1.5]}, "test")


class IngestTest(unittest.TestCase):
    def test_ingest_appends_and_dedupes_by_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            ledger = os.path.join(tmp, "ledger.jsonl")
            cfg = load_config(os.path.join(tmp, "budgets.yaml"))
            usage = write_usage(
                tmp,
                [
                    evt("2026-09-01T00:00:00Z", key="key-A", cost=5.0, eid="e1"),
                    evt("2026-09-02T00:00:00Z", key="key-A", cost=7.5, eid="e2"),
                ],
            )
            ingested, skipped, _ = ingest_file(usage, ledger, cfg.prices)
            self.assertEqual((ingested, skipped), (2, 0))
            # ingest the same file again: ids dedupe, nothing new appended
            ingested2, skipped2, _ = ingest_file(usage, ledger, cfg.prices)
            self.assertEqual((ingested2, skipped2), (0, 2))
            events = load_ledger(ledger)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["cost_usd"], 5.0)

    def test_ingest_computes_cost_from_price_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            ledger = os.path.join(tmp, "ledger.jsonl")
            cfg = load_config(os.path.join(tmp, "budgets.yaml"))
            usage = write_usage(
                tmp,
                [
                    evt(
                        "2026-09-01T00:00:00Z",
                        key="key-A",
                        eid="t1",
                        tokens_in=10000,
                        tokens_out=5000,
                    )
                ],
            )
            ingested, _, _ = ingest_file(usage, ledger, cfg.prices)
            self.assertEqual(ingested, 1)
            events = load_ledger(ledger)
            # 10 * 0.001 + 5 * 0.002 = 0.02
            self.assertAlmostEqual(events[0]["cost_usd"], 0.02)

    def test_ingest_prefers_explicit_cost_usd(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            ledger = os.path.join(tmp, "ledger.jsonl")
            cfg = load_config(os.path.join(tmp, "budgets.yaml"))
            usage = write_usage(
                tmp,
                [
                    evt(
                        "2026-09-01T00:00:00Z",
                        key="key-A",
                        cost=3.25,
                        eid="c1",
                        tokens_in=999999,
                        tokens_out=999999,
                    )
                ],
            )
            ingest_file(usage, ledger, cfg.prices)
            events = load_ledger(ledger)
            self.assertAlmostEqual(events[0]["cost_usd"], 3.25)

    def test_ingest_skips_bad_lines_with_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            ledger = os.path.join(tmp, "ledger.jsonl")
            cfg = load_config(os.path.join(tmp, "budgets.yaml"))
            path = os.path.join(tmp, "usage.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"timestamp": "2026-09-01T00:00:00Z", "key": "key-A", "cost_usd": 1.0}\n')
                fh.write("not json at all\n")
                fh.write('{"key": "key-A", "cost_usd": 1.0}\n')  # no timestamp
            ingested, skipped, warnings = ingest_file(path, ledger, cfg.prices)
            self.assertEqual(ingested, 1)
            self.assertEqual(skipped, 2)
            self.assertEqual(len(warnings), 2)


class EvaluateTest(unittest.TestCase):
    def _results(self, spend_map):
        cfg = Config(
            {
                "keys": {"key-A": {"monthly_usd": 50}},
                "projects": {"proj-X": {"monthly_usd": 200}},
                "alert_at": [0.8, 0.95],
            },
            "test",
        )
        key_totals = {"key-A": spend_map.get("key-A", 0.0)}
        project_totals = {"proj-X": spend_map.get("proj-X", 0.0)}
        return evaluate(cfg, key_totals, project_totals)

    def test_month_windowing(self):
        events = [
            evt("2026-09-15T00:00:00Z", key="key-A", cost=10.0),
            evt("2026-08-31T23:59:59Z", key="key-A", cost=999.0),
            evt("2026-10-01T00:00:00Z", key="key-A", cost=999.0),
        ]
        key_totals, _ = totals_for_month(events, 2026, 9)
        self.assertAlmostEqual(key_totals["key-A"], 10.0)

    def test_threshold_alerts(self):
        results = self._results({"key-A": 42.0})  # 84% of 50
        alerts = render_alert_lines(results)
        self.assertEqual(len(alerts), 1)
        self.assertIn("ALERT", alerts[0])
        self.assertIn("80%", alerts[0])
        self.assertNotIn("95%", alerts[0])
        self.assertEqual(render_breach_lines(results), [])

        results95 = self._results({"key-A": 48.0})  # 96% of 50
        alerts95 = render_alert_lines(results95)
        self.assertEqual(len(alerts95), 2)  # crossed both 80 and 95
        self.assertTrue(any("95%" in a for a in alerts95))

    def test_breach_detection(self):
        results = self._results({"key-A": 55.0})  # 110% of 50
        breaches = render_breach_lines(results)
        self.assertEqual(len(breaches), 1)
        self.assertIn("BREACH", breaches[0])
        self.assertIn("key-A", breaches[0])

    def test_key_project_isolation(self):
        cfg = Config(
            {
                "keys": {
                    "key-A": {"monthly_usd": 50},
                    "key-B": {"monthly_usd": 10},
                },
                "projects": {"proj-X": {"monthly_usd": 200}},
                "alert_at": [0.8],
            },
            "test",
        )
        key_totals = {"key-A": 100.0, "key-B": 1.0}
        project_totals = {"proj-X": 30.0}
        results = {r.name: r for r in evaluate(cfg, key_totals, project_totals)}
        # key-A breached, key-B fine, project fine: spend is isolated
        self.assertTrue(results["key-A"].breached)
        self.assertFalse(results["key-B"].breached)
        self.assertFalse(results["proj-X"].breached)
        self.assertAlmostEqual(results["proj-X"].spent_usd, 30.0)


class CliTest(unittest.TestCase):
    def test_check_alert_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            ledger = os.path.join(tmp, "ledger.jsonl")
            cfg = load_config(os.path.join(tmp, "budgets.yaml"))
            usage = write_usage(
                tmp, [evt("2026-09-05T00:00:00Z", key="key-A", cost=42.0)]
            )
            ingest_file(usage, ledger, cfg.prices)
            proc = run_cli(tmp, "check", "--month", MONTH)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("ALERT", proc.stdout)
            self.assertNotIn("BREACH", proc.stdout)

    def test_check_breach_exit_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            ledger = os.path.join(tmp, "ledger.jsonl")
            cfg = load_config(os.path.join(tmp, "budgets.yaml"))
            usage = write_usage(
                tmp, [evt("2026-09-05T00:00:00Z", key="key-B", cost=12.5)]
            )
            ingest_file(usage, ledger, cfg.prices)
            proc = run_cli(tmp, "check", "--month", MONTH)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("BREACH", proc.stdout)

    def test_check_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            ledger = os.path.join(tmp, "ledger.jsonl")
            cfg = load_config(os.path.join(tmp, "budgets.yaml"))
            usage = write_usage(
                tmp,
                [
                    evt("2026-09-05T00:00:00Z", key="key-A", cost=42.0),
                    evt("2026-09-06T00:00:00Z", key="key-B", cost=12.5),
                ],
            )
            ingest_file(usage, ledger, cfg.prices)
            proc = run_cli(tmp, "check", "--month", MONTH, "--key", "key-A")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("key-A", proc.stdout)
            self.assertNotIn("key-B", proc.stdout)

    def test_guard_refuses_on_breach(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            ledger = os.path.join(tmp, "ledger.jsonl")
            cfg = load_config(os.path.join(tmp, "budgets.yaml"))
            usage = write_usage(
                tmp, [evt("2026-09-05T00:00:00Z", key="key-B", cost=12.5)]
            )
            ingest_file(usage, ledger, cfg.prices)
            marker = os.path.join(tmp, "should_not_exist.txt")
            proc = run_cli(
                tmp,
                "guard",
                "--month",
                MONTH,
                "--",
                "touch",
                marker,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("REFUSED", proc.stderr)
            self.assertFalse(os.path.exists(marker))

    def test_guard_runs_on_pass_and_propagates_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            marker = os.path.join(tmp, "ran.txt")
            proc = run_cli(
                tmp, "guard", "--month", MONTH, "--", "touch", marker
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(os.path.exists(marker))

            proc2 = run_cli(
                tmp,
                "guard",
                "--month",
                MONTH,
                "--",
                sys.executable,
                "-c",
                "import sys; sys.exit(7)",
            )
            self.assertEqual(proc2.returncode, 7)

    def test_webhook_best_effort_does_not_hang_or_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = (
                "alert_at: [0.8]\n"
                'webhook_url: "http://127.0.0.1:9/tokenguard"\n'
                "keys:\n"
                "  key-B:\n"
                "    monthly_usd: 10\n"
            )
            make_config(tmp, text=text)
            ledger = os.path.join(tmp, "ledger.jsonl")
            cfg = load_config(os.path.join(tmp, "budgets.yaml"))
            usage = write_usage(
                tmp, [evt("2026-09-05T00:00:00Z", key="key-B", cost=12.5)]
            )
            ingest_file(usage, ledger, cfg.prices)
            proc = run_cli(tmp, "check", "--month", MONTH, timeout=30)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("BREACH", proc.stdout)

    def test_sample_files_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            import shutil

            shutil.copy(
                os.path.join(REPO_ROOT, "samples", "budgets.yaml"),
                os.path.join(tmp, "budgets.yaml"),
            )
            shutil.copy(
                os.path.join(REPO_ROOT, "samples", "usage.jsonl"),
                os.path.join(tmp, "usage.jsonl"),
            )
            proc = run_cli(tmp, "ingest", "usage.jsonl")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("ingested 6 event(s)", proc.stdout)
            proc = run_cli(tmp, "check", "--month", MONTH)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("ALERT", proc.stdout)  # sk-sample-001 at 92%
            self.assertIn("BREACH", proc.stdout)  # sk-sample-002 at 125%


class ConfigNumberValidationTest(unittest.TestCase):
    """monthly_usd / prices / alert_at must be finite numbers, not bools."""

    def test_nan_inf_budgets_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ConfigError, msg="budget %r" % (bad,)):
                Config({"keys": {"k": {"monthly_usd": bad}}}, "test")
            with self.assertRaises(
                ConfigError, msg="project budget %r" % (bad,)
            ):
                Config({"projects": {"p": {"monthly_usd": bad}}}, "test")

    def test_bool_budgets_rejected(self):
        for bad in (True, False):
            with self.assertRaises(ConfigError, msg="budget %r" % (bad,)):
                Config({"keys": {"k": {"monthly_usd": bad}}}, "test")

    def test_nan_inf_prices_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ConfigError, msg="price %r" % (bad,)):
                Config(
                    {
                        "prices": {
                            "default": {
                                "input_per_1k": bad,
                                "output_per_1k": 0.0,
                            }
                        }
                    },
                    "test",
                )

    def test_bool_prices_rejected(self):
        with self.assertRaises(ConfigError):
            Config(
                {
                    "prices": {
                        "default": {
                            "input_per_1k": True,
                            "output_per_1k": 0.0,
                        }
                    }
                },
                "test",
            )

    def test_bool_alert_threshold_rejected(self):
        with self.assertRaises(ConfigError):
            Config({"alert_at": [True]}, "test")

    def test_numeric_strings_still_accepted(self):
        cfg = Config({"keys": {"k": {"monthly_usd": "50"}}}, "test")
        self.assertEqual(cfg.keys["k"], 50.0)

    def test_nan_budget_rejected_from_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "budgets.yaml")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("keys:\n  k:\n    monthly_usd: nan\n")
            with self.assertRaises(ConfigError):
                load_config(path)


class LedgerNumberValidationTest(unittest.TestCase):
    def _ingest_lines(self, tmp, lines):
        make_config(tmp)
        ledger = os.path.join(tmp, "ledger.jsonl")
        cfg = load_config(os.path.join(tmp, "budgets.yaml"))
        path = os.path.join(tmp, "usage.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
        return ingest_file(path, ledger, cfg.prices), ledger

    def test_negative_tokens_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (ingested, skipped, warnings), _ = self._ingest_lines(
                tmp,
                [
                    json.dumps(
                        {
                            "timestamp": "2026-09-01T00:00:00Z",
                            "key": "key-A",
                            "tokens_in": -100,
                            "tokens_out": 0,
                            "model": "gpt-4o-mini",
                        }
                    )
                ],
            )
            self.assertEqual(ingested, 0)
            self.assertEqual(skipped, 1)
            self.assertTrue(any(">= 0" in w for w in warnings), warnings)

    def test_bool_cost_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (ingested, skipped, warnings), _ = self._ingest_lines(
                tmp,
                [
                    json.dumps(
                        {
                            "timestamp": "2026-09-01T00:00:00Z",
                            "key": "key-A",
                            "cost_usd": True,
                        }
                    )
                ],
            )
            self.assertEqual(ingested, 0)
            self.assertEqual(skipped, 1)
            self.assertTrue(
                any("must be a number" in w for w in warnings), warnings
            )

    def test_nan_inf_cost_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            (ingested, skipped, warnings), ledger = self._ingest_lines(
                tmp,
                [
                    # json allows NaN/Infinity literals via python's encoder
                    '{"timestamp": "2026-09-01T00:00:00Z", "key": "key-A", "cost_usd": NaN}',
                    '{"timestamp": "2026-09-01T00:00:00Z", "key": "key-A", "cost_usd": Infinity}',
                ],
            )
            self.assertEqual(ingested, 0)
            self.assertEqual(skipped, 2)
            self.assertTrue(
                any("must be finite" in w for w in warnings), warnings
            )
            self.assertEqual(load_ledger(ledger), [])

    def test_negative_cost_allowed_as_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            (ingested, skipped, _), ledger = self._ingest_lines(
                tmp,
                [
                    json.dumps(
                        {
                            "timestamp": "2026-09-01T00:00:00Z",
                            "key": "key-A",
                            "cost_usd": -2.5,
                        }
                    )
                ],
            )
            self.assertEqual((ingested, skipped), (1, 0))
            events = load_ledger(ledger)
            self.assertEqual(events[0]["cost_usd"], -2.5)


class LedgerFileErrorTest(unittest.TestCase):
    def test_ingest_to_directory_ledger_is_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage = write_usage(
                tmp,
                [evt("2026-09-01T00:00:00Z", key="key-A", cost=1.0)],
            )
            with self.assertRaises(LedgerError) as ctx:
                ingest_file(
                    usage, tmp, {"default": None, "models": {}}  # tmp is a dir
                )
            # load_ledger's directory guard fires first during dedupe;
            # either way it must be a clean LedgerError, not a traceback
            self.assertIn("not a file", str(ctx.exception))

    def test_ingest_from_directory_source_is_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LedgerError) as ctx:
                ingest_file(tmp, os.path.join(tmp, "l.jsonl"), {})
            self.assertIn("not a file", str(ctx.exception))

    def test_load_ledger_on_directory_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LedgerError):
                load_ledger(tmp)

    def test_load_ledger_missing_file_still_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                load_ledger(os.path.join(tmp, "nope.jsonl")), []
            )


class CliEdgeTest(unittest.TestCase):
    def test_missing_ledger_note_goes_to_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            # ledger.jsonl does not exist
            proc = run_cli(tmp, "check", "--month", MONTH)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("treating spend as $0", proc.stderr)
            self.assertNotIn("treating spend as $0", proc.stdout)
            self.assertIn("TokenGuard check", proc.stdout)

    def test_help_shows_examples_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(tmp, "--help")
            self.assertEqual(proc.returncode, 0)
            self.assertIn("examples:", proc.stdout)
            self.assertIn("guard --", proc.stdout)
            self.assertIn("exit codes:", proc.stdout)

    def test_ingest_bad_numbers_warn_and_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_config(tmp)
            path = os.path.join(tmp, "usage.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    '{"timestamp": "2026-09-01T00:00:00Z", "key": "key-A", '
                    '"cost_usd": true}\n'
                )
                fh.write(
                    '{"timestamp": "2026-09-02T00:00:00Z", "key": "key-A", '
                    '"cost_usd": 1.5}\n'
                )
            proc = run_cli(tmp, "ingest", "usage.jsonl")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("ingested 1 event(s)", proc.stdout)
            self.assertIn("must be a number", proc.stderr)

    def test_check_rejects_bool_budget_with_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "budgets.yaml"), "w") as fh:
                fh.write("keys:\n  key-A:\n    monthly_usd: true\n")
            proc = run_cli(tmp, "check", "--month", MONTH)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("must be a number", proc.stderr)


if __name__ == "__main__":
    unittest.main()
