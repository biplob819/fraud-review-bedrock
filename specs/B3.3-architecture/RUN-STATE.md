# B3.3 run state

**Now:** building

## History
- 2026-11-17 — built in a parallel worktree lane after A2.5 went green and merged into main.
- 2026-11-17 — verifier pass 1: the diagram is honest, but two proofs could not catch a broken one: no test checked which kind each box is drawn as (swapping Bedrock and the demo, or deleting a built box, passed), and deleting the IAM boundary rect passed. Fixed: tests now check every box's drawn kind against the legend, the legend line each part sits under, that the IAM boundary rect encloses the pilot workflow, a Region or cross-Region phrase anywhere in the file, px-only font sizes, and that the PNG is the full 1750×1175 export and not blank. Should-fix applied: §7 and the SVG no longer claim inference profiles or a VPC endpoint for Mantle (to be confirmed in a pilot), the legend says "every other AWS service", the spec places the analyst app, CloudWatch and KMS outside the boundary as drawn, the demo box names both the owner passcode and own-key paths (D-31), and the PNG was re-exported.

## Open should-fix (carried forward)
- Whether the Mantle endpoint can be reached through a VPC endpoint is to be confirmed in a pilot.
