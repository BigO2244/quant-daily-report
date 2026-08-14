#!/usr/bin/env bash
# Deploy: put this machine on a clean checkout of origin/main, validate, and
# atomically attest the exact deployed SHA.
# Run ON the VM:  bash ~/quant-daily-report/scripts/deploy.sh
# Candidate validation occurs in a detached temporary worktree. Production HEAD
# and the prior attestation remain unchanged unless every validation passes.
set -euo pipefail

REPO="${HOME}/quant-daily-report"
cd "${REPO}"

mkdir -p "${HOME}/.caerus" "${REPO}/outputs"
VALIDATION_WORKTREE=""
CANDIDATE_STATE="${REPO}/outputs/deploy_state.candidate.json"
DEPLOY_LOCK_DIR=""
cleanup() {
    if [[ -n "${VALIDATION_WORKTREE}" ]]; then
        git -C "${REPO}" worktree remove --force "${VALIDATION_WORKTREE}" >/dev/null 2>&1 || true
    fi
    rm -f "${CANDIDATE_STATE}"
    if [[ -n "${DEPLOY_LOCK_DIR}" ]]; then
        rmdir "${DEPLOY_LOCK_DIR}" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

if command -v flock >/dev/null 2>&1; then
    exec 9>"${HOME}/.caerus/source_deploy.lock"
    if ! flock -x -w 300 9; then
        echo "FATAL: timed out waiting for live-pilot source lock; deploy outside the execution window" >&2
        exit 3
    fi
else
    DEPLOY_LOCK_DIR="${HOME}/.caerus/source_deploy.lock.d"
    LOCK_ATTEMPTS=0
    while ! mkdir "${DEPLOY_LOCK_DIR}" 2>/dev/null; do
        LOCK_ATTEMPTS=$((LOCK_ATTEMPTS + 1))
        if [[ "${LOCK_ATTEMPTS}" -ge 3000 ]]; then
            echo "FATAL: timed out waiting for live-pilot source lock; deploy outside the execution window" >&2
            exit 3
        fi
        sleep 0.1
    done
fi

echo "=== deploy: fetch ==="
git fetch origin --quiet

BRANCH="$(git branch --show-current)"
STATUS="$(git status --porcelain --untracked-files=all)"
CURRENT_SHA="$(git rev-parse HEAD)"
TARGET_SHA="$(git rev-parse --verify 'origin/main^{commit}')"
if [[ "${BRANCH}" != "main" ]]; then
    echo "FATAL: deployment requires branch main; found ${BRANCH:-DETACHED}" >&2
    exit 3
fi
if [[ -n "${STATUS}" ]]; then
    echo "FATAL: production checkout is dirty; classify and resolve drift before deployment" >&2
    printf '%s\n' "${STATUS}" >&2
    exit 3
fi
if ! git merge-base --is-ancestor "${CURRENT_SHA}" "${TARGET_SHA}"; then
    echo "FATAL: origin/main is not a fast-forward of production HEAD" >&2
    exit 3
fi

SHORT_SHA="$(git rev-parse --short "${TARGET_SHA}")"
echo "=== deploy: validate pinned candidate ${SHORT_SHA} ==="
VALIDATION_WORKTREE="$(mktemp -d "${TMPDIR:-/tmp}/caerus-deploy.XXXXXX")"
rmdir "${VALIDATION_WORKTREE}"
git worktree add --detach "${VALIDATION_WORKTREE}" "${TARGET_SHA}" >/dev/null

(
    cd "${VALIDATION_WORKTREE}"
    # shellcheck disable=SC1091
    source scripts/runtime_env.sh
    activate_runtime_venv "${VALIDATION_WORKTREE}"
    python3 -m py_compile daily_quant_report.py core/live_pilot_guardrails.py \
        core/live_pilot_sha_guard.py core/precompute_contract.py \
        core/paper_target_authority.py core/precompute_bundle_validation.py \
        scripts/finalize_deployment.py scripts/live_pilot_execute.py \
        scripts/live_pilot_build_plan_from_precompute.py \
        scripts/seal_paper_precompute_target.py \
        scripts/certify_execution_readiness.py \
        scripts/build_portfolio_history.py
    python3 -m pytest \
        Tests/test_live_pilot_guardrails.py \
        Tests/test_live_pilot_client_order_id.py \
        Tests/test_live_pilot_sha_guard.py \
        Tests/test_cron_live_pilot.py \
        Tests/test_paper_target_authority.py \
        Tests/test_paper_execution_real_chain.py \
        Tests/test_portfolio_history_builder.py \
        -q
    python3 scripts/finalize_deployment.py \
        --repo-root "${VALIDATION_WORKTREE}" \
        --expected-sha "${TARGET_SHA}" \
        --expected-branch "" \
        --source-ref origin/main \
        --deploy-state "${CANDIDATE_STATE}"
)

git worktree remove "${VALIDATION_WORKTREE}"
VALIDATION_WORKTREE=""

echo "=== deploy: publish pinned candidate ${SHORT_SHA} ==="
git merge --ff-only "${TARGET_SHA}"
if [[ "$(git rev-parse HEAD)" != "${TARGET_SHA}" ]]; then
    echo "FATAL: published HEAD does not match validated target" >&2
    exit 3
fi
if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
    echo "FATAL: production checkout changed during deployment" >&2
    exit 3
fi
mv "${CANDIDATE_STATE}" "${REPO}/outputs/deploy_state.json"
if [[ -n "${DEPLOY_LOCK_DIR}" ]]; then
    rmdir "${DEPLOY_LOCK_DIR}"
    DEPLOY_LOCK_DIR=""
fi
trap - EXIT
echo "DEPLOYED_AND_ATTESTED sha=${TARGET_SHA}"
