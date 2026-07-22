#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${CAERUS_VM_PYTHON:-/home/brettolson/.venvs/quant-daily-report/bin/python}"
PYTEST_BIN="${CAERUS_VM_PYTEST:-/home/brettolson/.venvs/quant-daily-report/bin/pytest}"

echo "[VM_VALIDATION] repo=$(pwd)"
echo "[VM_VALIDATION] head=$(git rev-parse HEAD)"
echo "[VM_VALIDATION] branch=$(git branch --show-current)"

STATUS="$(git status --short)"
if [[ -n "${STATUS}" ]]; then
  echo "[VM_VALIDATION][FAIL] working tree is not clean"
  printf '%s\n' "${STATUS}"
  exit 3
fi
echo "[VM_VALIDATION] working_tree=clean"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[VM_VALIDATION][FAIL] missing python venv binary: ${PYTHON_BIN}"
  exit 127
fi
if [[ ! -x "${PYTEST_BIN}" ]]; then
  echo "[VM_VALIDATION][FAIL] missing pytest venv binary: ${PYTEST_BIN}"
  exit 127
fi

echo "[VM_VALIDATION] python=${PYTHON_BIN}"
"${PYTHON_BIN}" --version
echo "[VM_VALIDATION] pytest=${PYTEST_BIN}"
"${PYTEST_BIN}" --version

# A routine validation must verify the already-attested deployment pin. During
# scripts/deploy.sh, finalize_deployment.py supplies the exact candidate SHA;
# that candidate is checked against both HEAD and origin/main before the marker
# is written. This makes a bare git pull fail validation instead of looking like
# a completed deployment with a stale deploy_state.json.
if [[ -n "${CAERUS_DEPLOY_CANDIDATE_SHA:-}" ]]; then
  if [[ "${CAERUS_DEPLOY_INTERNAL:-0}" != "1" || -n "$(git branch --show-current)" ]]; then
    echo "[VM_VALIDATION][FAIL] candidate mode is restricted to the detached deployment worktree"
    exit 4
  fi
  CANDIDATE_SHA="$(git rev-parse --verify "${CAERUS_DEPLOY_CANDIDATE_SHA}^{commit}")"
  ORIGIN_SHA="$(git rev-parse --verify 'origin/main^{commit}')"
  HEAD_SHA="$(git rev-parse HEAD)"
  if [[ "${CANDIDATE_SHA}" != "${HEAD_SHA}" || "${ORIGIN_SHA}" != "${HEAD_SHA}" ]]; then
    echo "[VM_VALIDATION][FAIL] deployment candidate does not match HEAD and origin/main"
    echo "[VM_VALIDATION] candidate=${CANDIDATE_SHA} head=${HEAD_SHA} origin_main=${ORIGIN_SHA}"
    exit 4
  fi
  echo "[VM_VALIDATION] deployment_candidate=${CANDIDATE_SHA} source_ref=origin/main"
else
  SHA_GUARD_OUTPUT="$(${PYTHON_BIN} scripts/live_pilot_sha_guard.py --repo-root "$(pwd)")" || SHA_GUARD_RC=$?
  SHA_GUARD_RC="${SHA_GUARD_RC:-0}"
  if [[ "${SHA_GUARD_RC}" -ne 0 ]]; then
    echo "[VM_VALIDATION][FAIL] deployed source attestation does not match the running tree"
    printf '%s\n' "${SHA_GUARD_OUTPUT}"
    echo "[VM_VALIDATION] remediation=run ./scripts/deploy.sh; do not hand-edit outputs/deploy_state.json"
    exit 4
  fi
  echo "[VM_VALIDATION] deployment_attestation=verified"
fi

echo "[VM_VALIDATION] operational_validation"
"${PYTHON_BIN}" scripts/operational_validation.py

echo "[VM_VALIDATION] py_compile"
"${PYTHON_BIN}" -m py_compile \
  research_registry/sleeves/manifest.py \
  research_registry/sleeves/evidence.py \
  scripts/research/validate_sleeve_manifest.py \
  scripts/research/validate_sleeve_evidence.py

echo "[VM_VALIDATION] targeted_pytest"
"${PYTEST_BIN}" \
  Tests/test_sleeve_manifest.py \
  Tests/test_sleeve_evidence.py \
  Tests/test_governance_hygiene_agent.py \
  Tests/test_sleeve_numeric_diagnostics.py \
  Tests/test_target_attainment.py \
  -q

echo "[VM_VALIDATION][PASS]"
