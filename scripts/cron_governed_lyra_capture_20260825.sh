#!/usr/bin/env bash
# One-date advisory-only capture wrapper. This file never installs itself.
set -euo pipefail

readonly REPO_ROOT="${CAERUS_REPO_ROOT:-/home/brettolson/quant-daily-report}"
readonly PYTHON_BIN="${CAERUS_PYTHON_BIN:-/home/brettolson/.venvs/quant-daily-report/bin/python}"
readonly CONFIG_PATH="${CAERUS_GOVERNED_LYRA_CAPTURE_CONFIG:-/home/brettolson/.caerus/governed_lyra_capture_20260825.env}"

cd "${REPO_ROOT}"
exec "${PYTHON_BIN}" scripts/run_governed_lyra_capture_20260825.py \
  --config "${CONFIG_PATH}"
