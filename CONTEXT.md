---
type: repository
status: active
owner_repo: MohammedAbdelshafy/tokenguard
product_name: API Spend Guard
source_of_truth: https://github.com/MohammedAbdelshafy/tokenguard
---

# API Spend Guard CONTEXT

One job: check recorded API usage against budgets and stop supported commands when the configured threshold has been exceeded.

## Inputs
- Commands and caveats: [README](README.md)
- Implementation: [tokenguard](tokenguard/)
- Tests: [tests](tests/)
- Sample budgets: [samples](samples/)

## Process
1. Read usage events and the configured budget rules.
2. Append accepted events to the local ledger.
3. Calculate usage against the configured limits.
4. Report warnings and breaches with clear exit codes.
5. Gate a supported command only when the budget check passes.

## Output
A usage summary, budget alerts and an exit status that can be used by scripts or CI.

## Check
Run the tests and sample workflow. Keep real API keys out of example data and logs. Explain that the result is only as complete as the usage events and pricing configuration supplied.
