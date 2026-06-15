# Patch Agent

Role: Patch agent

No reviewer-identified capital-protection defect required a second patch.

One test-fixture adjustment was made during implementation: legacy cash-gating tests that assumed accepted-only sell state could proceed to buys were narrowed to the new safety invariant. The dedicated incident and unresolved-sell tests now carry the hotfix behavior.

