# N1.3 acceptance criteria

**BRK-01 — client wrapper**
- The request body has model, `max_tokens`, `thinking: adaptive`, `output_config` with effort and a JSON schema, cached system blocks and one user message, and no `temperature`, `top_p`, `top_k` or `tools`.
- Every send passes `guard_request`, retries included; a body carrying a tool definition, or gaining one between attempts, is refused before that send.
- A reply that fails the pydantic model counts as invalid even when the API accepted it.

**BRK-02 — errors and retries**
- Throttling, overload, 5xx and connection errors retry with exponential backoff and jitter, at most 6 attempts, then `transport`.
- An invalid reply retries once, then `schema`. A refusal is not retried: `refusal`.
- Every 4xx other than 408, 409 and 429, client-side SDK errors, and a key or AWS credentials that cannot be resolved raise `ProviderConfigError`, which `fraud`'s `main` maps to exit 2 naming what to set; they are never `transport`. `make_client` names a missing `ANTHROPIC_API_KEY` before any call.
- Tokens from every attempt are summed.

**BRK-03 — cache**
- The key is stable under dict key order and changes with any of its parts.
- The first writer wins: a second write of the same key leaves the first value; a crash mid-write leaves no partial entry.
- A stored entry whose key or `code_version` does not match is refused.
- A registered step whose module source changes (any helper or constant) without a `code_version` bump fails the check.

**BRK-04 — cost**
- Cost per call uses input, output (thinking included), cache read and cache write prices from the provider's `prices.yaml` table.

**BRK-05 — estimate and spend cap**
- `estimate_run` follows §9; `fraud triage --dry-run` prints call counts and the estimate, makes no call, and exits 2 when the estimate exceeds `max_cost_usd`.
- Each call reserves `llm.worst_case_cost` (every schema attempt, the whole body as input at the dearer of the input and cache-write prices, a full `max_tokens` reply), which bounds any billed attempt (an attempt that fails in transport is assumed unbilled; `commit` stops the run if recorded spend ever passes the cap). `SpendCap.reserve` refuses a call when actual plus reserved plus its reservation would pass the cap; concurrent reservations cannot overshoot; `commit` stops the run if actual spend ever passes the cap. `CapReached` maps to exit 3 in `fraud`'s `main`.

- A billed reply whose usage is missing or unreadable (a non-JSON or plain-text 200, absent or malformed usage) is priced at the worst case, not $0.

**BRK-06 — ledger**
- Each real run appends one valid JSON line with run id, provider, split, calls, estimate and actual cost; earlier lines are untouched.

**BRK-07 — concurrency**
- No more than `concurrency` calls are in flight at once; throttled calls back off.

**BRK-08 — provider switch**
- `make_client` builds the Claude API client or the Mantle client from one config switch with SDK retries off; the same body works on both.
- `to_decision` records the provider on every decision.
- Live: both stage schemas are accepted by the API and the replies validate (`structured-schemas.json`).
