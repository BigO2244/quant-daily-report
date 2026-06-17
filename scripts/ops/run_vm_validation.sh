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
