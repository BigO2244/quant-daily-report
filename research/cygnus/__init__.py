"""Caerus Cygnus — post-earnings drift research (FR-051).

RESEARCH_ONLY / NON_EXECUTIONAL. This package builds research artifacts only and
must never send orders or alter Polaris/Orion/Lyra/Phoenix, paper/live
execution, cron timing, allocation, or the strategy registry.

Implementation Wave 1 (per the 2026-06-10 addendum) covers Stage 1 (the EDGAR
8-K Item 2.02 event tape + acceptance-timestamp audit) and Stage 2
(`cygnus_v0_event_reaction`). EDGAR is the sole event source; consensus/revision
vendor work (v1/v2) remains gated.
"""

SCHEMA_VERSION_EVENT_TAPE = "caerus_cygnus_event_tape_v1"
SCHEMA_VERSION_AUDIT = "caerus_cygnus_acceptance_audit_v1"
STRATEGY_ID = "caerus_cygnus"
GOVERNANCE_LABEL = "RESEARCH_ONLY"
EXECUTION_IMPACT = "NON_EXECUTIONAL"
