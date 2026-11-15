# N1.4 — Policy as code

**Wave 1. Depends on:** N1.1. **Unblocks:** A2.2 (router), A2.3 (stage 1), E2.2 (explanation checks).

## Goal
The clauses the agent may cite live in `policy.md`, each with plain-language prose and a predicate the router checks in code, so "clear only when a clearing condition applies" (B4) and "never invent clauses" (B8) are code checks, not the model's opinion (D-5, D-6).

## Outputs
- `policy.md` v1 at the repo root: signal, clear and no_clear clauses written from fraud-ops practice, thresholds marked illustrative (POL-01).
- `src/fraudreview/policy.py`: `load_policy(path)` parsing headings and one YAML block per clause; `PolicyError` naming the clause (POL-02); `parse_predicate` on an `ast` node allow-list, names restricted to the clause's declared fields, never `eval` (POL-03); `Policy.version` = first 16 hex of sha256 over the file bytes and `prompt_text()` without the YAML (POL-04); `Policy.unknown_clauses` and `Policy.clearing_allowed(cited, case)` on a validated `AlertCase` returning the refusal reasons the router records (POL-05).

## Rules
- Fields come from the `FeatureName` allow-list (N1.1), each with a type from the `AlertCase` schema (number, bool or category). A predicate reads exactly the fields it declares, and every comparison is between values of one type; categories and bools take only `==` and `!=`; a category constant must be one of the 14 categories; a predicate is always a true/false test (no bare number or constant). These are checked when the policy loads, so a bad predicate never reaches the router.
- Predicates read a validated `AlertCase`, so values always have their schema type. A missing value (`amt_vs_usual` for a card with almost no history) fails safe: clear does not hold, no_clear holds, signal does not hold.
- `policy.md` is a small Markdown subset that every renderer (GitHub's cmark-gfm, markdown-it, editor previews) shows the same way: outside code blocks, only blank lines, one `# Title`, `## P<n>.<m> Title` clause headings and unindented prose or simple list items, written in a fixed ASCII character set that excludes every character a renderer can turn into hidden, struck, linked or differently rendered text. Prose may mention only clause ids that exist. A line scan and a CommonMark parse (markdown-it) must agree on every clause heading and code block. So a clause the reader sees is a clause the router checks, and the reverse.
- The file is read with the same strict YAML loader as the config (duplicate keys refused, JSON-style numbers), must be UTF-8, and has exactly one ```yaml block per clause and no other code block, so nothing the router checks can hide from it or leak into the prompt.
- A policy with no clear clause is refused (nothing could ever be cleared, which would hide a typo).
- `clearing_allowed` checks every no_clear clause in the policy, cited or not.

## Out of scope
Routing and confidence rules (A2.2); prompts (A2.3).
