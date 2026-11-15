# A2.2 run state

**Now:** green

## History
- 2026-11-15 — spec, criteria and tasks written.
- 2026-11-15 — tests first, router and audit built; converge passed twice.
- 2026-11-15 — verifier pass 1 confirmed RTE-01, 02, 03, 05; disproved RTE-04: U+2028/U+2029 in a reason or comment were written raw and split the line on read, making the log unreadable; the docstring overstated tamper detection (deleting trailing lines or rewriting a suffix needs an external anchor). Fixed: canonical JSON is ASCII with escapes and lines split on `\n` only; `read_log(expect_head=...)` with `AuditLog.head`; exclusive file lock and on-disk refresh so two writers keep one chain; appends refused after a failed write; guard matches key spellings, `mcp_servers`, `toolConfig`, refuses non-JSON data and cycles; `RouterSettings` bounds; criteria wording (rule 1 short-circuits, name check is a heuristic).

- 2026-11-15 — verifier pass 2 confirmed RTE-01 (30,000-case differential check), 02, 03, 05; disproved RTE-04: a writer silently continued a file cut short or deleted between appends, and `head` reported the damaged file. Fixed: the writer checks the file still holds its own chain before each append and for `head` (last-line check when unchanged, full verify when grown), refuses otherwise and stops; `head` returns the writer's own verified head; `read_log` accepts the head as a JSON list; the guard refuses only true cycles; wording and the escaping proof fixed.
- 2026-11-15 — verifier pass 3 confirmed RTE-01, 02, 03, 05; disproved RTE-04: a same-size replacement with an earlier line edited passed the last-line check, and `head` read without the lock so another writer's append mid-read falsely stopped the writer. Fixed: every operation holds the file lock and reads once; a fingerprint (inode, size, mtime) recorded after each own write decides whether the whole file is re-verified; any failed check stops the writer; the guard refuses NaN, infinity and subclasses; `reconcile` wording.
- 2026-11-15 — verifier pass 4 confirmed RTE-01, 02, 03, 05; disproved RTE-04: `read_log(expect_head=...)` returned an empty log for a deleted file. Fixed: a missing file is refused when the recorded head has events; the writer also checks after locking that the path still names the locked file (closes an append racing a delete); the guard uses exact container types; criteria name the forged-fingerprint limit.
- 2026-11-15 — verifier pass 5: no MUST-FIX; all five rows confirmed (960-event multi-process stress, every damage case refused). Applied its cheap should-fixes: exact `str` keys in the guard, a size check after each write, a line number on a cut-off last line, criteria wording relative to this writer's events. Green.

## Open should-fix (carried forward)
- N1.3 must route every model request through `guard_request` and test it.
- E2.3 must record the audit head (seq, hash) in `summary.json` and check it with `read_log(expect_head=...)`.
- The ast name check is a heuristic; an import allow-list (no HTTP/SMS/email libraries outside the model client) would be stronger.
- The containing directory is not fsynced when the log file is first created.
- `read_log` reads without the file lock, so a reader racing a writer can see a half-written last line (`incomplete`).
- `dispose` with the same decision id at both stages raises pydantic's error; a schema-valid integer over 4,300 digits cannot be serialised (cap `Count` in the schemas).
- With several writers, each append after another writer's growth re-verifies the whole file (quadratic); the batch run uses one writer.
- A tool list inside a JSON string (`{"body": json.dumps({"tools": ...})}`, Bedrock InvokeModel) passes the guard; N1.3 must guard the decoded body.
- The `a+b` open recreates an empty file after a delete before refusing (harmless: an empty file reads like a missing one).
- On Linux a same-size in-place edit within one mtime tick of the writer's own append keeps the fingerprint (contrived; `read_log` still catches it).
- With several writers, a cut that removes only another writer's events is not seen by a writer that has not refreshed (that writer's recorded head and `expect_head` expose it).
