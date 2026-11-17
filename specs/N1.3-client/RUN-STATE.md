# N1.3 run state

**Now:** building

## History
- 2026-11-17 — started after W0.2 went green.

- 2026-11-17 — verifier pass 1: three must-fix (unresolvable credentials crashed instead of exit 2; spend could pass the cap because reservations were estimates; no CLI exit-code mapping), all fixed with five should-fix (guard on retries proven, whole-module code version, the live script reserves and writes its own ledger line, the dry-run test patches the real clients, all other 4xx are config errors).
- 2026-11-17 — verifier pass 2: two must-fix (boto3 credential errors crashed instead of exit 2; the schema-check ledger line was hand-written with a wrong estimate), both fixed: `BotoCoreError` is a config error, and the live check was rerun so the script wrote its own line ($0.0098). The first run's $0.0094, made before the script wrote ledger lines, is recorded as a separate line with no estimate; the W0.2 preflight line was also recorded after its run. Should-fix applied: ASCII-escaped length in the worst case, the transport-billing assumption written down, credential matching narrowed to the SDKs' wording, an unparseable 200 retried as transport, the status code in config errors, `commit` validates its amount.
- 2026-11-17 — verifier pass 3: two must-fix (an unparseable 200 was retried as transport though it is billed; the W0.2 ledger line said 6 calls where 8 were billed), both fixed: an unreadable billed reply counts as an invalid reply (two attempts at most, priced at the worst case through `billed_cost`), and the ledger line now says 8 calls with a note. Lines written after their runs carry a `note`. Should-fix applied: `ProviderConfigError` carries earlier billed tokens, the live script writes its ledger line in a `finally`, the ledger test is exact, `commit` refuses booleans and infinity.
- 2026-11-17 — verifier pass 4: one must-fix (the real client never raises the error the unmetered path caught: a non-JSON 200 crashed, and a plain-text 200 or a reply without readable usage was priced at $0). Fixed: missing or unreadable usage and JSON decode errors count as unmetered billed replies priced at the worst case, proven through the real SDK client on a mock transport. Should-fix applied: the live script settles a config error's earlier tokens, `model_call` moved to code_version 2 (with `llm.CODE_VERSION`), and BRK-05's safety proof names the new test.

## Open should-fix (carried forward)
- From N1.2: route every request through `guard_request` (done here by design).
- A2.3+: the cache's code_version registry covers only `llm`; stage keys add `prompt_hash` (which covers the output schema) and `STAGE_CODE_VERSION`, so register `stages` and `prompts` too.
