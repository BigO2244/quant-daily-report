# ADR-002: Aegis control-plane isolation and persistence

## Decision

Implement Aegis as `aiops.aegis`, with an additive SQLite schema and a standard-library REST/read-only dashboard surface. Mission decomposition is template-based and canonicalized; it does not call a model API. AIOPS is the only execution adapter and is represented as an approval-gated command constructor.

## Consequences

- Existing AIOPS lifecycle and exit-code contracts are unchanged.
- The default runner emits packets only. No Aegis code imports trading, broker, allocation, scheduler, paper, pilot, live, or capital modules.
- Existing Atlas and Alpha Lab artifacts can be represented through artifact manifests without copying or executing them. A future importer may record metadata only.

## Migration

`AegisStore` records forward-only numbered migrations in `schema_migrations`; it does not alter existing AIOPS or trading tables.
