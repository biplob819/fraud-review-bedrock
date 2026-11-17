# B3.3 — Reference architecture

**Wave 4. Depends on:** A2.5. **Unblocks:** D4.2 (project page).

## Goal
One diagram that shows what ran and what an AWS pilot would add, honestly labelled, for the README, the portfolio site and model-risk reviewers (US9, Tech Spec §7).

## Outputs
- `docs/assets/architecture.svg`: on a white background, with every label as real text.
  - Left: the local machine that ran everything (CLI, LightGBM baseline, router and policy-as-code, cache, spend cap and ledger, hash-chained audit) and the Claude API (Haiku 5.5, Sonnet 5.5; D-35).
  - Right: the owner's AWS project, us-east-2 only, with no cross-Region arrows:
    - Bedrock through the in-Region Mantle endpoint (supported, not run; D-27, D-34);
    - the passkey-protected hosted demo, a Lambda function URL plus DynamoDB (proposed; D-31);
    - inside an IAM boundary, the pilot workflow from §7: S3, EventBridge, Step Functions with Lambda tasks, DynamoDB and S3 with Object Lock; outside it, the analyst queue app, CloudWatch and KMS (all proposed).
  - The legend has three kinds: built (ran), supported (not run), proposed (pilot) (ARC-01, ARC-02).
- `docs/assets/architecture.png`: exported at 1750 × 1175 for the site (ARC-03).
- `tests/test_architecture.py`: checks the legend's three kinds, that every built component is named, the AWS services and the IAM boundary, that only us-east-2 appears, a minimum font size of 11 px, and that the PNG exists.
- Tech Spec §7 "Built versus proposed" is updated to the three-kind legend.
