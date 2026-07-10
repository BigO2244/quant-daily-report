#!/usr/bin/env bash
# Deploy: put this machine on a clean checkout of origin/main, smoke-test, record SHA.
# Run ON the VM:  bash ~/quant-daily-report/scripts/deploy.sh
# Never partial-deploys: any local overlay is stashed (preserved), never merged.
set -euo pipefail

REPO="${HOME}/quant-daily-report"
cd "${REPO}"

echo "=== deploy: fetch ==="
git fetch origin --quiet

# Preserve any local overlay (tracked-file edits) in a stash, then clean-checkout main.
if ! git diff --quiet || ! git diff --cached --quiet; then
    STASH_MSG="deploy-autostash-$(date +%Y%m%dT%H%M%S)"
    git stash push -m "${STASH_MSG}"
    echo "stashed local overlay as: ${STASH_MSG}"
fi

git checkout -B main origin/main
SHA="$(git rev-parse --short HEAD)"
echo "=== deploy: checked out main @ ${SHA} ==="

echo "=== deploy: smoke ==="
# shellcheck disable=SC1091
source scripts/runtime_env.sh
activate_runtime_venv "${REPO}"
python3 -m py_compile daily_quant_report.py core/live_pilot_guardrails.py \
    core/precompute_contract.py scripts/live_pilot_execute.py \
    scripts/live_pilot_build_plan_from_precompute.py
python3 -m pytest Tests/test_live_pilot_guardrails.py Tests/test_live_pilot_client_order_id.py -q

mkdir -p outputs
printf '{\n  "deployed_sha": "%s",\n  "deployed_at": "%s",\n  "branch": "main"\n}\n' \
    "${SHA}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > outputs/deploy_state.json
echo "DEPLOYED sha=${SHA}"
