# Reusable patterns

Four patterns from this project that a team could lift into another agent. Each one is small. Each comes with where it lives here, one piece of evidence, and what it does not do.

All numbers are from simulated data (see `DATA_CARD.md`). They show that the code behaves as designed. They do not show how it would perform on a bank's real alerts.

## PAT-01: Policy as code, and a router that decides

### Problem

A model that both judges a case and acts on it can be talked into a wrong action. In fraud review, the costly error is clearing a real fraud.

### Pattern

Write the rules in a file people can read. Give each rule an ID, a kind (signal, clear or no-clear) and a machine-checked condition. The model may only cite rule IDs and recommend. A plain function, the router, accepts or refuses every clear. The model never acts. A request guard also refuses any model call that carries tools.

Excerpt from `src/fraudreview/router.py`:

```python
    if not case.risk_score < settings.clear_ceiling:  # rule 5
        reasons.append("risk_ceiling")
    if decision.stage == 2:  # rule 6
        assert isinstance(output, Stage2Output)
        if not output.confidence >= settings.clear_min_confidence:
            reasons.append("low_confidence")
    return ClearCheck(not reasons, tuple(reasons))
```

### Where it lives

- `policy.md` holds the clauses. `src/fraudreview/policy.py` loads and checks them.
- `src/fraudreview/router.py` holds `check_clear`, `dispose`, `queues` and `guard_request`.
- `config.yaml` holds the risk ceiling and the minimum confidence.
- Tests: `tests/test_policy.py`, `tests/test_router.py`.

### Evidence from this project

On the simulated eval set, 0 of 55 frauds were auto-cleared (simulated data). The test `test_planted_clear_with_a_false_predicate_is_refused` in `tests/test_router.py` shows a clear that cites a false clause being refused.

### Limits

- The router is only as good as the rules. A missing no-clear clause is a hole the router cannot see.
- Zero leaks out of 55 is a small sample. The interval is wide.
- The thresholds are illustrative and set on simulated data. A bank would set its own.
- The guard refuses known tool keys. It does not stop a model from writing harmful text.

## PAT-02: A human-review queue with a hash-chained audit log

### Problem

When people and a model share a decision, you need to show later what was recommended, what the router did, and what the analyst did. A log that can be edited quietly is not evidence.

### Pattern

The router puts every alert that is not auto-cleared in one of two queues: priority for likely fraud, standard for uncertain or failed cases. Every decision, disposition and analyst action is appended to a log. Each line carries the hash of the line before it, so an edit or a deletion breaks the chain at a named line.

Excerpt from `src/fraudreview/audit.py`:

```python
def event_hash(event: AuditEvent) -> str:
    """The hash an event must carry: sha256 over its canonical JSON without `hash`."""
    return hashlib.sha256(canonical(event.model_dump(mode="json", exclude={"hash"}))).hexdigest()
```

### Where it lives

- `src/fraudreview/audit.py`: the append-only log, `read_log` and `audit_view`.
- `src/fraudreview/router.py`: `queues`.
- `src/fraudreview/schemas.py`: the event types, including analyst actions.
- Tests: `tests/test_audit.py`.

### Evidence from this project

The test `test_a_rehashed_edit_still_breaks_the_next_link` in `tests/test_audit.py` edits a line and recomputes its hash. The next link still fails. On the simulated eval set, 402 alerts went to the standard queue and 54 to the priority queue (simulated data).

### Limits

- A hash chain shows tampering. It does not prevent it. Someone who rewrites the whole file can rebuild the chain.
- To catch that, record the head hash somewhere else and pass it back as `expect_head`. Here the caller must do this.
- The log is a local file with one writer lock. It is not a database and not built for many machines.
- The queue is a list of alert IDs. There is no screen, assignment or deadline.

## PAT-03: Explanation checks

### Problem

A model gives a reason with each recommendation. A fluent reason can still be wrong. You want checks that run on every reason and say plainly what they could not check.

### Pattern

Run cheap checks on every explanation:

- cited clauses exist and cited fields are on the allow-list;
- groundedness: the numbers in the reason are compared with the record;
- driver agreement: the reasons the model gave are compared with the features that drive a score.

The value check has three outcomes: verified, contradicted, unverifiable. A claim is verified only when it matches the record. A correct rounding that falls outside the tolerance is called unverifiable, not verified.

Excerpt from `src/fraudreview/valuecheck.py`:

```python
    diff = abs(v - t)
    if diff <= max(ABS_TOL, REL_TOL * abs(t)):
        return "verified"
    half = 0.5 * 10 ** (-_decimals(c.shown)) / (100 if c.pct else 1)
    if diff <= half * (1 + 1e-9):
        return "unverifiable"  # a correct rounding, but outside the tolerance
    return "contradicted"
```

### Where it lives

- `src/fraudreview/valuecheck.py`: claim finding and the strict check.
- `src/fraudreview/explain.py`: clause and field checks, protected terms, driver agreement.
- `src/fraudreview/judging.py`: the faithfulness judge run.
- Tests: `tests/test_valuecheck.py`, `tests/test_explain.py`, `tests/test_judging.py`.
- Numbers: `docs/measurements/summary-eval2.json`.

### Evidence from this project

Of 3,913 value claims on the simulated eval set, 1,098 were verified, 0 were contradicted and 2,815 were unverifiable (simulated data). Most claims could not be checked. That is the honest reading.

### Limits

- Never verifying a false claim is not the same as catching every false claim. Unverifiable claims pass through unchecked.
- Driver agreement was 0.46 on the simulated eval set (simulated data). It is low, and it is reported as it is.
- The faithfulness judge result is a single-judge partial and is not calibrated. The second judge produced no verdicts. Not every claim was judged. The evidence sentences round numbers, and policy statements have no policy text beside them, so correct claims can read as unsupported. Read it as a rough signal, not a score.
- The checks look at the reason. They do not tell you the recommendation was right.

## PAT-04: A cost model and a spend cap

### Problem

Model calls cost money, and an agent loop can spend a lot before anyone looks. A cap that restarts on every rerun is not a cap.

### Pattern

Before a call, reserve the worst case it could bill. After the call, settle the reservation with what it billed. If the reservation does not fit under the cap, refuse the call. Settle on every path, including errors. Write one line per run to a public ledger. Count the ledger when you check the cap, so the cap is a total across reruns.

Excerpt from `src/fraudreview/llm.py`:

```python
    reserve = worst_case_cost(body, price)
    token = await cap.reserve(reserve)
    try:
        result = await call_model(client, body, output_model, **kw)
        usd = billed_cost(result.tokens, result.unmetered, body, price)
```

### Where it lives

- `src/fraudreview/cost.py`: `Estimate`, `SpendCap`, dry-run lines.
- `src/fraudreview/llm.py`: `settled_call` and the worst-case price.
- `src/fraudreview/ledger.py`: the ledger and `total_spend`.
- `docs/measurements/spend-ledger.jsonl`: the public ledger.
- Tests: `tests/test_judging.py`.

### Evidence from this project

The test `test_the_cap_is_a_total_across_reruns_of_the_same_run` in `tests/test_judging.py` reruns judging under the same cap and checks that the ledger total stays within it. The measured cost of the two stages was about 6.24 USD per 1,000 alerts on the simulated eval set (simulated data).

### Limits

- A resumed judging run once overspent an approved cap. The run started a fresh per-run cap each time it resumed, so it spent nearly twice the approved amount. The cap now counts the ledger, and a renamed run folder cannot start a fresh cap. The overspend is recorded in `docs/decisions.md` (D-43).
- The ledger is a file you trust. Anyone who can edit it can reset the cap.
- Worst-case reservation is cautious. It can refuse a call that would have fit.
- Token prices are inputs in `prices.yaml`. If they are wrong, the estimate is wrong.
- This is a client-side cap. It does not replace a limit set by your cloud or API provider.

## When not to use these

- If a wrong action is cheap and easy to undo, a router and a queue may be more machinery than you need.
- If you have no machine-checkable rules, PAT-01 has nothing to check. Writing the rules is the hard part.
- If one person runs the agent for a few calls, a hash chain and a ledger are more than that needs. A simple call counter may do.
- If the explanation is not part of the decision, PAT-03 adds cost for little gain.
- None of these replace review by the people who own the risk.

## What real data would change

The data here is simulated, so the numbers show the machinery works, not how it would fare in production. Real alerts would change the rules, the thresholds and the risk ceiling. The low rate of real fraud would make the leak count much harder to measure, and a zero would need far more cases to mean much. Real analysts would add labels, which would let the faithfulness judge be calibrated and the driver check be tested against what they see. Real traffic would also move over time, so the monitoring and the cap would need watching, not just setting once.
