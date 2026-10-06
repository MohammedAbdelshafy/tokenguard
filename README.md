# TokenGuard

API spend guardrails: per-key / per-project monthly budgets, usage ingestion,
threshold alerts, and a hard-stop enforcement hook. **Enforcement, not
analytics.**

How this differs from
[token-dashboard](https://github.com/MohammedAbdelshafy/token-dashboard):
that repo is a *dashboard* — it reads Claude Code transcripts and shows
usage charts. TokenGuard does not chart anything. It *enforces*: it compares
spend against budgets you set, prints alerts, and exits non-zero on breach
so CI jobs and scripts can refuse to run expensive work.

Stdlib only. No install, no dependencies.

## Quickstart

```bash
git clone https://github.com/MohammedAbdelshafy/tokenguard.git
cd tokenguard

# 1. Copy the sample config and point it at your own keys/budgets
cp samples/budgets.yaml budgets.yaml

# 2. Ingest usage events into the local ledger
./bin/tokenguard ingest samples/usage.jsonl
#   (or: python3 -m tokenguard ingest samples/usage.jsonl)

# 3. Check this month's spend against budgets
./bin/tokenguard check

# 4. Gate a command on budgets: runs only if nothing is breached
./bin/tokenguard guard -- python3 expensive_job.py
```

Try the samples end to end (sample events are dated September 2026):

```bash
export TOKENGUARD_LEDGER=/tmp/demo-ledger.jsonl
./bin/tokenguard ingest samples/usage.jsonl
./bin/tokenguard check --month 2026-09
```

Expected: `sk-sample-001` at 92.4% of its $50 budget (ALERT, crossed 80%),
`sk-sample-002` at 125% of its $10 budget (BREACH, exit code 2),
`demo-app` project at 32.4% of $200 (OK).

## Commands

### `tokenguard ingest usage.jsonl`

Appends usage events to the local ledger (`~/.tokenguard/ledger.jsonl` by
default; override with `--ledger` or `TOKENGUARD_LEDGER`).

Each input line is a JSON object:

```json
{"timestamp": "2026-09-12T09:00:00Z", "key": "sk-live-abc", "project": "client-x",
 "model": "gpt-4o-mini", "tokens_in": 1000000, "tokens_out": 100000}
```

or with an explicit cost:

```json
{"timestamp": "2026-09-12T09:00:00Z", "key": "sk-live-abc", "project": "client-x",
 "cost_usd": 0.21}
```

Rules:

- `timestamp` is required (ISO-8601, `Z`/offset, or epoch seconds).
- At least one of `key` / `project` is required.
- `cost_usd`, when present, wins. Otherwise cost is computed from
  `tokens_in` / `tokens_out` using the price table in the config
  (per-model entry, else `prices.default`).
- Lines with an `id` field are deduplicated: re-ingesting the same file
  never double-counts.
- Malformed lines are skipped with a warning on stderr; valid lines are
  still ingested.

### `tokenguard check [--key K] [--project P] [--month YYYY-MM]`

Evaluates spend for a calendar month (default: current month, UTC) against
the budgets in `budgets.yaml` (auto-discovered in cwd, then
`~/.tokenguard/`; override with `--config` or `TOKENGUARD_CONFIG`).

Output: a status table, one `ALERT` line per crossed threshold below 100%,
and one `BREACH` line per exceeded budget.

Exit codes: `0` = within budget (alerts may still print), `2` = at least
one budget breached, `1` = config/usage error. On breach, a JSON payload is
POSTed to `webhook_url` if configured (best-effort, 5s timeout — failures
are logged, never fatal).

### `tokenguard guard -- <command...>`

Runs `check` first. If any budget is breached, the command is **not**
executed and TokenGuard exits `2` with a `REFUSED` message. Otherwise the
command runs and its exit code is propagated. Use it to gate CI steps or
cron jobs that spend API budget.

## GitHub Action

TokenGuard ships as a composite action (`v1` release):

```yaml
- uses: MohammedAbdelshafy/tokenguard@v1
  with:
    budgets: infra/tokenguard/budgets.yaml
    command: python3 expensive_job.py   # optional; omitted = check only
```

It copies your budgets file into the action's checkout and runs
`guard -- <command>` (or `check` when no command is given), failing the
step on breach. Note: the ledger is local to the runner — a fresh CI job
starts with zero spend unless an earlier step in the same job ingests
usage into it (via `TOKENGUARD_LEDGER` / `tokenguard ingest`).

## Config reference (`budgets.yaml`, JSON also accepted)

```yaml
alert_at: [0.8, 0.95]          # alert thresholds (fractions of budget)
webhook_url: ""                # optional breach webhook (POST JSON)

prices:                        # USD per 1,000 tokens
  default:
    input_per_1k: 0.0015
    output_per_1k: 0.006
  models:
    gpt-4o-mini:
      input_per_1k: 0.00015
      output_per_1k: 0.0006

keys:                          # per-API-key monthly budgets
  sk-live-abc:
    monthly_usd: 50

projects:                      # per-project monthly budgets (aggregates keys)
  client-x:
    monthly_usd: 200
```

The YAML reader is a small built-in subset parser (nested maps, `- ` lists,
`[a, b]` flow lists, scalars, `#` comments) — no PyYAML needed. JSON
configs are detected by content.

## Inputs / outputs

- Input: `budgets.yaml`/`budgets.json` (budgets, thresholds, prices,
  webhook), usage JSONL files.
- Output: local append-only ledger JSONL, stdout status table + ALERT /
  BREACH lines, exit codes (`0`/`1`/`2`), optional breach webhook POST.

## Limits

- The ledger is a local JSONL file; there is no server, no multi-user
  sync, and no hosted dashboard.
- Budget windows are calendar months computed in UTC.
- The breach webhook is best-effort (short timeout, failures only warn).
- Spend is only as accurate as the usage events you ingest and the prices
  in your config — TokenGuard does not call provider APIs.
- Key names in the config and ledger are matched as plain strings.

## Tests

```bash
python3 -m unittest discover tests -v
```

## License

MIT — see [LICENSE](LICENSE).
