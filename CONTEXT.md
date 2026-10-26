---
type: repository
status: active
owner_repo: MohammedAbdelshafy/tokenguard
product_name: API Spend Limits
source_of_truth: https://github.com/MohammedAbdelshafy/tokenguard
---

# API Spend Limits CONTEXT

One job: compare recorded API usage with explicit budgets and report or block the guarded task when a limit is exceeded.

## Inputs
- Commands/configuration: [README](README.md)
- Implementation: [tokenguard](tokenguard/)
- Tests: [tests](tests/)
- Sample events and budgets: [samples](samples/)

## Process
1. Load the configured budgets.
2. Ingest usage records into the local ledger.
3. Compare recorded spend with configured thresholds.
4. Return a clear OK, ALERT or BREACH result.
5. Allow the wrapper script or CI job to stop only when the configured guard is invoked.

## Output
Usage ledger entries, budget status and an exit code that can gate a command.

## Check
Run the tests and sample events. Verify that a breached budget fails in the expected path. Do not claim that all provider spending is captured or blocked.
