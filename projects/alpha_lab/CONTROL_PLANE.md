# Alpha Lab Discovery-to-Decision Control Plane

Status: `RESEARCH_ONLY_NON_EXECUTIONAL`
Owner: Brett Olson
Authority: recommendation and evidence routing only

## Outcome

The control plane turns heterogeneous investment research into an auditable
owner-decision queue. It supports cross-sectional models, time-series models,
event studies, machine learning, options-information research, portfolio-
construction tests, execution research, and future techniques without giving
the research agent authority over capital or runtime state.

It may:

- validate and run a frozen research evaluator;
- stop on missing point-in-time inputs;
- draft a licensed-data request with cost and acceptance criteria;
- classify research evidence;
- detect Shadow review checkpoints;
- nominate a candidate for Paper review; and
- produce a compact CIO queue for Brett.

It may not:

- purchase or subscribe to data;
- accept vendor terms;
- write credentials;
- activate a Shadow strategy;
- edit `config/research/strategy_registry.json`;
- promote a model to Paper;
- submit orders or change allocation, broker, cron, deployment, or production
  behavior.

## End-to-end lifecycle

```text
Idea
  ↓ Brett: FREEZE HYPOTHESIS
Frozen hypothesis + frozen evaluator + data contracts
  ↓ Brett: RUN EXPERIMENT
Provider/PIT data gate
  ├─ blocked free data → complete free-source work
  ├─ blocked trial/paid data → CIO DATA_ACCESS_REVIEW
  └─ ready → bounded evaluator
  ↓
Alpha Card + independent review
  ├─ REJECT / ITERATE
  └─ EVIDENCE_READY_FOR_OWNER_REVIEW → CIO RESEARCH_DECISION_REVIEW
       ↓ Brett: PURSUE
     CIO SHADOW_ACTIVATION_REVIEW
       ↓ separate owner-approved onboarding task
     Shadow observation
       ↓ frozen checkpoints, normally 20 and 60 trading days
     CIO SHADOW_CHECKPOINT_REVIEW
       ↓ all final research + Shadow gates pass
     CIO PAPER_PROMOTION_REVIEW
       ↓ Brett explicitly approves a separate Paper-scoping task
     governed implementation outside Alpha Lab
```

No arrow in this diagram is an automatic promotion. An owner decision changes
only the next permitted work state. Any Shadow or Paper implementation remains
a separately scoped task under parent-repository governance.

## Candidate snapshots are not a strategy registry

The control plane consumes immutable `candidate_snapshot` artifacts. A snapshot
is a point-in-time compilation of one hypothesis, its frozen experiment, data
readiness, Alpha Card, owner decision already recorded in `DECISION_LOG.md`, and
Shadow evidence if applicable. It is not canonical identity state and may not
be used to override the research strategy registry or roadmap.

Create a draft from `templates/CANDIDATE_SNAPSHOT.json`, omit
`source_snapshot_hash`, and seal it:

```bash
python -m projects.alpha_lab.control_plane.cli seal-candidate \
  --draft /path/to/candidate_draft.json \
  > /path/to/sealed_candidate_response.json
```

On the authoritative GCP root, add `--write --repo-root .` to persist the
snapshot as an immutable manifested bundle. The response wraps the sealed
snapshot under `candidate`; extract that object only when a standalone local
snapshot is needed for a read-only assessment.

Every gate is boolean and fail-closed. A missing gate is not inferred from a
good return. Evidence references require SHA-256 hashes. A Shadow evidence
reference must be labeled as Shadow evidence before Paper nomination is
possible.

## Generic evaluator adapters

Each new hypothesis may provide one adapter below
`projects.alpha_lab.evaluators`. The frozen spec records:

- technique family;
- module and callable;
- primary metric;
- maximum variant budget;
- exact data-contract IDs; and
- locked challenge period.

The adapter receives only the certified input packet and evaluation phase. It
must return the frozen primary metric name, number of variants attempted, and
`orders_submitted: false`. The runner rejects challenge access unless it is
explicitly authorized, rejects a changed primary metric or excess variants,
and statically rejects direct production imports or order calls.

Every evaluator that produces dated return observations should also call
`projects.alpha_lab.evaluators.regime_diagnostics.summarize_regime_observations`.
The shared envelope enforces that the regime label was available by the decision
timestamp, uses the canonical seven regime labels, requires 30 independent
observations before a regime cell is decision-grade, and requires 252 total
observations before regime-selection coverage is even eligible for review.
Regime slices are secondary diagnostics: they cannot rescue a failed
unconditional test, change allocation, or promote a model. A regime-specific
claim or allocation rule requires its own separately frozen holdout.

```bash
python -m projects.alpha_lab.control_plane.cli run-evaluator \
  --spec /path/to/frozen_evaluator_spec.json \
  --input /path/to/ready_data_gate_packet.json \
  --phase DISCOVERY
```

Challenge-period execution additionally requires
`--authorize-challenge-access`. That flag records permission to read the frozen
challenge panel; it does not relax any evidence gate.

## Licensed-data workflow

A frozen data requirement records the provider, dataset, exact fields, use,
acceptance tests, URL, free alternative, and estimated one-time/monthly costs.

When a required trial or paid source is missing, the queue emits
`DATA_ACCESS_REVIEW` with three owner choices:

- `APPROVE_REQUEST` — authorize outreach, trial, or purchase scoping only;
- `DEFER`; or
- `REJECT`.

Approval to request data is not approval to buy it. Before a dataset becomes
`CERTIFIED_READY`, it must pass the frozen timestamp, identity, amendment,
corporate-action, field, license, and point-in-time audits. Credentials and
vendor payloads live only under the GCP storage policy.

## CIO queue

Build a queue from one or more sealed snapshots:

```bash
python -m projects.alpha_lab.control_plane.cli build-queue \
  --candidate /path/to/candidate_snapshot.json
```

To persist a finalized queue, run on `caerus-vm` using the authoritative GCP
repository root:

```bash
cd /mnt/disks/alpha-lab/alpha-lab-project
/home/brettolson/.venvs/quant-daily-report/bin/python \
  -m projects.alpha_lab.control_plane.cli build-queue \
  --candidate-dir outputs/research/alpha_lab/control_plane/candidate_snapshots \
  --write \
  --repo-root .
```

Writes fail closed anywhere except the canonical GCP repository. Final bundles
are append-only under:

```text
outputs/research/alpha_lab/control_plane/cio_queue/YYYY-MM-DD/<bundle-id>/
```

Each bundle contains `queue.json`, `queue.md`, and a last-written manifest with
file sizes and SHA-256 hashes. The queue sorts Paper reviews ahead of Shadow
checkpoints, research decisions, and data-access requests.

## Paper nomination gate

The control plane nominates a candidate for Brett's Paper decision only when:

1. every required dataset is `CERTIFIED_READY`;
2. the research verdict is `EVIDENCE_READY_FOR_OWNER_REVIEW`;
3. every frozen research gate passes;
4. Brett's recorded research decision is `PURSUE`;
5. Shadow onboarding was separately approved;
6. the final frozen Shadow checkpoint is reached;
7. every frozen Shadow gate passes; and
8. hashed Shadow evidence is attached.

The resulting item is `PAPER_PROMOTION_REVIEW` with choices to scope Paper,
extend Shadow, park, or kill. It contains `promotion_performed: false`.

## Agent operating protocol

On each research review, the Alpha Lab agent should:

1. preserve the frozen hypothesis and complete trial count;
2. locate only finalized GCP inputs and verify their manifests;
3. run or refresh the provider gate;
4. use the generic evaluator or a hypothesis-specific frozen evaluator;
5. create/update the Alpha Card without tuning during review;
6. seal a new candidate snapshot referencing exact evidence hashes;
7. build the CIO queue; and
8. notify Brett only when a new decision item exists, an existing item changes
   materially, or the mechanism fails closed.

Healthy no-action runs should remain quiet. Duplicate decision fingerprints
should not create repeated alerts. Notification deduplication uses
`decision_fingerprint`, which hashes the actionable items without the daily
generation timestamp.

The Codex automation `Alpha Lab CIO review queue` checks this surface at 18:45
ET on weekdays. It is separate from production cron. Before the control-plane
code reaches the GCP checkout through the normal governed git deployment path,
the automation remains silent and performs no copy or deployment.
