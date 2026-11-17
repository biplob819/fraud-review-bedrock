# B3.3 run state

**Now:** green

## History
- 2026-11-17 — built in a parallel worktree lane after A2.5 went green and merged into main.
- 2026-11-17 — verifier pass 1: the diagram is honest, but two proofs could not catch a broken one: no test checked which kind each box is drawn as (swapping Bedrock and the demo, or deleting a built box, passed), and deleting the IAM boundary rect passed. Fixed: tests now check every box's drawn kind against the legend, the legend line each part sits under, that the IAM boundary rect encloses the pilot workflow, a Region or cross-Region phrase anywhere in the file, px-only font sizes, and that the PNG is the full 1750×1175 export and not blank. Should-fix applied: §7 and the SVG no longer claim inference profiles or a VPC endpoint for Mantle (to be confirmed in a pilot), the legend says "every other AWS service", the spec places the analyst app, CloudWatch and KMS outside the boundary as drawn, the demo box names both the owner passcode and own-key paths (D-31), and the PNG was re-exported.
- 2026-11-17 — verifier pass 2: 23 mutation probes; one must-fix (the two DynamoDB boxes shared a title, so neither was proofed). Fixed: the demo box is titled "Amazon DynamoDB (demo)", titles must be unique, both are checked for kind and the pilot one for sitting inside the boundary; I re-probed both missed mutations (pilot table drawn as built, boundary cut to 500 px) and each now fails. Should-fix applied: §7 says "every other AWS service", the "optional" label no longer crosses the panel border, and keyword font sizes and scale transforms are refused. Green.

## Open should-fix (carried forward)
- The demo's own-key path (D-31) calls the Claude API; no arrow is drawn for it (D4.1 may add it).
- Whether the Mantle endpoint can be reached through a VPC endpoint is to be confirmed in a pilot.
