# Privacy

## Short version

- There is no telemetry. The code does not phone home, count installs or report errors.
- Your data goes only to the model provider you configure, and only when you run a triage.
- The replay demo, `fraud report --example` and the dashboard reach nothing.

## What leaves your machine

When you run `fraud triage`, each alert is sent to the provider set in `config.yaml`: the Claude API, or Amazon Bedrock. The prompt holds only the allow-listed alert fields (see [Bring your own alert data](own-data.md)) and the policy text. It does not hold names, addresses, card or account numbers, dates of birth or locations. A safety test renders every prompt for the committed alerts and fails if a dropped value appears.

Nothing else is sent. There is no analytics service, no logging service and no second provider. The one other network use is `fraud load`, which downloads the simulated source data from Kaggle with your token.

What your provider does with the data is set by their terms and your account settings. Read them before you send real data. Check the retention and training settings for your account.

## What stays on your machine

- Run folders in `results/`, the response cache in `.fraud/` and your audit logs.
- Your `.env` and any keys. They are read from the environment and are never printed, logged or committed. See [`SECURITY.md`](../../SECURITY.md).
- The spend ledger and dev-run log in `docs/measurements/`, which record counts and costs only. If you fork the project and push, those files go with it, so check them first.

## The demo

The replay demo reads the committed `demo/replay.jsonl`. It calls no model, reaches no cloud service and reads no key. The decisions you record in it are not real. The only file that leaves the browser session is the audit file you choose to download.

## Real data

This project is a demonstration on simulated data. Before you point it at real cardholder data, you need your own review of the legal basis, the provider's terms and your company's rules. Nothing here is advice on that.
