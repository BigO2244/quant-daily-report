#!/usr/bin/env bash
set -euo pipefail

# --- Config defaults (override via env) ---
: "${TRADING_MODE:=alpaca}"
: "${ALPACA_PAPER:=1}"
: "${PAPER_TRADING:=1}"
: "${MODE:=SHADOW}"

# Ensure we run from repo root no matter where called from
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# Activate venv if present (GitHub will create it; Mac mini can too)
if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

# Compute REPORT_DATE in America/New_York unless already set
if [[ -z "${REPORT_DATE:-}" ]]; then
  export TZ="${TZ:-America/New_York}"
  REPORT_DATE="$(date +%F)"
  export REPORT_DATE
fi

echo "=== RUN CONFIG ==="
echo "REPORT_DATE=${REPORT_DATE}"
echo "TRADING_MODE=${TRADING_MODE}"
echo "ALPACA_PAPER=${ALPACA_PAPER}"
echo "PAPER_TRADING=${PAPER_TRADING}"
echo "MODE=${MODE}"
echo "=================="

python3 daily_quant_report.py