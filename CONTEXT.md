---
type: repository
status: active
owner_repo: MohammedAbdelshafy/tokenguard
product_name: API Budget Guard
source_of_truth: https://github.com/MohammedAbdelshafy/tokenguard
---

# API Budget Guard CONTEXT

One job: compare the usage records you provide with explicit budgets and make it possible for a workflow to stop when a configured limit is breached.

## Inputs
- Commands, config and limitations: [README](README.md)
- Implementation: [tokenguard](tokenguard/)
- Tests: [tests](tests/)

## Process
1. Load usage events and append only new events using the supported idempotency rules.
2. Calculate costs using explicit costs or the configured price table.
3. Compare totals with key/project/month budgets.
4. Show warnings and breach status.
5. Return the documented exit code so the parent script can decide whether to continue.

## Output
A budget check with alerts and an exit status.

## Check
Run tests for cost calculation, duplicate ingestion, malformed records and breached budgets. Do not claim that every provider charge is covered without a verified ingestion path.
