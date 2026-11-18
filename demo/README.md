# Analyst review screen (replay demo)

A Streamlit page that replays a recorded evaluation run on simulated card data. An analyst picks a
held or escalated case, reads the agent's reasons and the policy clauses it cited next to the
baseline's score drivers, and records Confirm fraud, Clear, or Override (with a comment). There is
no block button. Each call is appended to a per-session, hash-chained audit log that the visitor
can download.

**v0.1.0 is replay-only (D-41).** Every case comes from the committed `demo/replay.jsonl`. No model
is called, no cloud service is reached, and no key or secret is read. The hosted demo with passkey
sign-in moves to v0.2. All data is simulated and every number on the screen says so. The decisions you record in the demo are not real: no real card, customer or case is involved, and nothing leaves your browser session except the audit file you choose to download.

## Run it locally

From the repository root:

```bash
uv sync --locked --extra demo
uv run streamlit run demo/app.py
```

Open the address Streamlit prints (usually http://localhost:8501). The audit log lives in a
temporary folder for the browser session; use the download button to keep it.

Without `uv`: `pip install -r demo/requirements.txt` then `streamlit run demo/app.py`
(Python 3.12 or newer).

## Regenerate the replay

```bash
uv run fraud demo-cache --run results/<run> --alerts results/baseline/alerts_<split>.jsonl
```

Cases whose merchant name or other text matches the public-content guard
(`fraudreview.content_guard`) are skipped and the command prints how many.

## Deploy to Streamlit Community Cloud (planned for v0.2, not part of v0.1.0)

The hosted deploy moved out of v0.1.0 (D-44). These are the steps for when it is done.

The repository owner has to do this; it needs their sign-in and cannot be scripted.

1. Sign in at https://share.streamlit.io with the GitHub account that owns the repository.
2. Choose Create app, then deploy a public app from a GitHub repository.
3. Repository: this one. Branch: `main`. Main file path: `demo/app.py`.
4. Under Advanced settings, pick Python 3.12. Leave Secrets empty; the demo needs none.
5. Deploy. Streamlit installs `demo/requirements.txt` (pinned from `uv.lock`) and starts the page.
   The app finds the `fraudreview` package through its `src/` folder, so nothing is published to
   PyPI.
6. Put the app's URL in the README.

To refresh the pins after `uv.lock` changes, re-run the export from `uv export --locked
--extra demo --no-dev --no-emit-project --no-hashes` and keep only the packages the demo imports
(Streamlit, pydantic, PyYAML, markdown-it-py and what they need). The demo must stay free of
`anthropic`, `boto3` and `botocore`; a test checks the import closure.
