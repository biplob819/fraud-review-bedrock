# A2.2 — Router, queue and audit log

**Wave 2. Depends on:** N1.1, N1.4. **Unblocks:** A2.3 (stage 1), E2.1 (metrics), D4.1 (review screen).

## Goal
The model recommends; code decides. Every alert ends with exactly one `Disposition`, made by router rules that the model cannot talk its way past, and every decision and disposition is written to an append-only, hash-chained audit log that can be checked and read per alert (D-5, D-12, H2, H3).

## Outputs
- `src/fraudreview/router.py`
  - `RouterSettings` (clear ceiling, minimum confidence), built from `RunConfig`.
  - `check_clear(decision, case, policy, settings)` applies router rules 1–6 to one stage reply and returns whether a clear is accepted plus the reasons it was refused (RTE-01).
  - `dispose(case, stage1, stage2, policy, settings)` turns the stage replies into one `Disposition` (RTE-02).
  - `reconcile(alert_ids, dispositions)` lists every alert without exactly one disposition, and every disposition for an unknown alert (RTE-02).
  - `queues(dispositions)` splits the alerts that need a person into the priority (hold) and standard (escalate) queues (RTE-03).
  - `guard_request(body)` refuses any model request whose keys grant tools (see criteria RTE-05); the package defines no function that blocks a card or contacts a customer (RTE-05).
- `src/fraudreview/audit.py`
  - `AuditLog(path)` appends events as JSON lines: seq, previous hash, and a sha256 hash over the canonical JSON of the event without its hash. Appends are serialised, flushed and fsynced; opening an existing log verifies it first and continues the chain.
  - `read_log(path)` verifies a log and names the first bad line.
  - `audit_view(events, alert_id)` returns one alert's events in order (RTE-04).

## Rules
- Router rules (Tech Spec §2.3): (1) the reply is valid, with no error; (2) every cited clause exists (fields are already limited to the allow-list by the schema); (3) a cited clear clause holds; (4) no no-clear clause holds; (5) `risk_score` < `clear_ceiling`; (6) stage 2 only: `confidence` ≥ `clear_min_confidence`. In addition the decision must belong to this alert and carry this policy's version, so a reply cannot be replayed onto another alert or policy.
- A refused stage-1 clear, a pass, or a stage-1 error goes to stage 2. At stage 2, an accepted clear auto-clears; `hold` goes to the priority queue; everything else (escalate, a refused clear, an error) goes to the standard queue. Neither queue touches the card.
- Refusal reasons are recorded on the `Disposition` with their stage (`s1:` or `s2:`), in rule order.
- Canonical JSON: keys sorted, no spaces, ASCII with escapes, timestamps in UTC (as the schemas serialise them).

## Out of scope
Calling models (N1.3, A2.3, A2.4); metrics (E2.1); the review screen (D4.1).
