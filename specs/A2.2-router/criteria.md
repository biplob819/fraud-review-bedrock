# A2.2 acceptance criteria

**RTE-01 — router rules**
- `check_clear` accepts a clear only when all of rules 1–6 hold and the decision's alert id and policy version match; otherwise it refuses and returns every failing reason in rule order (an error reply fails rule 1 and stops there): `error:<kind>`, `alert_mismatch`, `policy_version_mismatch`, the policy's own reasons (`unknown_clause:`, `no_clear_clause_cited`, `predicate_false:`, `missing_value:`, `no_clear:`), `risk_ceiling`, `low_confidence` (stage 2 only).
- A reply that is not a clear is never accepted as one and carries no refusal reasons except an error.
- `RouterSettings` refuses values outside the config bounds (ceiling 0.05 to 0.99, minimum confidence 0.5 to 1.0), so a hand-built setting cannot switch rule 5 or 6 off.
- Planted check (Tech Spec §5.7): a model clear whose predicate is false is refused.

**RTE-02 — one disposition per alert**
- `dispose` returns exactly one `Disposition` for each case: an accepted stage-1 clear is auto-cleared at stage 1 and needs no stage-2 reply; otherwise a stage-2 reply is required (a `ValueError` if missing). Stage 2: accepted clear → auto-cleared; hold → hold (priority); anything else → escalate (standard). `decision_ids` lists the decisions used, in order; replies for another alert or the wrong stage are refused with `ValueError`.
- `reconcile` returns an empty list exactly when every alert is listed once, has one disposition, and no disposition names an unknown alert; otherwise it names each problem.

**RTE-03 — queues**
- `queues` puts every hold in the priority queue and every escalate in the standard queue, each in input order, and leaves auto-cleared alerts out; every disposition that is not auto-cleared is in exactly one queue.

**RTE-04 — audit log**
- Each event's `hash` is the sha256 of the canonical JSON (sorted keys, no spaces, ASCII with escapes) of the event without `hash`; `prev_hash` is the previous event's hash (empty for seq 0); seq counts up from 0 without gaps.
- `read_log` refuses, naming the line, a log with a line that is not JSON or not a valid `AuditEvent`, a seq gap or repeat, a broken `prev_hash`, a hash that does not match the content, a byte that is not ASCII, a line not in canonical form, or a truncated last line; an empty or missing file is an empty log. Any text the schemas accept (including U+2028 and U+2029) keeps the log readable.
- The chain alone cannot show deleted trailing lines or a suffix rewritten with fresh hashes; `read_log(path, expect_head=(seq, hash))` refuses a log that does not end at a head recorded elsewhere (`AuditLog.head`, the writer's own verified last seq and hash; a JSON list is accepted), which catches both, and a missing file when the recorded head has events.
- `AuditLog` writes one line per event, flushed and fsynced, opening the file only to append, under an exclusive file lock; reopening an existing log verifies it and continues the chain; concurrent appends from threads, and appends from two instances on one file, produce one valid chain; every operation (open, append, head) holds the file lock and works from one read; after each of its own writes the writer records the file's fingerprint (inode, size, modification time), and when the fingerprint has changed it verifies the whole file, which must still hold its own chain (it may only have grown by another writer's valid events), so a file cut short of this writer's events, deleted, replaced or edited (even at the same size, unless its fingerprint is deliberately forged; `read_log` still catches the edit) is refused, and the path must still name the file the writer locked; after any failed write or check the instance refuses further appends.
- `audit_view` returns exactly the events for one alert, in log order.

**RTE-05 — no-action guard**
- `guard_request` raises on a request body with a key that grants tools (`tools`, `tool_choice`, `mcp_servers`, `toolConfig`, compared case-insensitively with `_` and `-` ignored) at any depth, on anything that is not plain JSON data (keys that are not exactly `str`, sets, objects, subclasses of dict, list, tuple or the scalar types, NaN or infinity; tuples are read as JSON arrays), and on a structure that contains itself (shared parts are fine); it returns the body unchanged otherwise. Wiring every model request through it is part of N1.3.
- No module in the package defines or calls a function whose name says it blocks, freezes or declines a card, or contacts, emails, texts or calls a customer (checked on the source with `ast` against a name pattern; a heuristic, not a proof).
