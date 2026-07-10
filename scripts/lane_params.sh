#!/usr/bin/env bash
# lane_params.sh — SINGLE SOURCE of shared behavior parameters for the two
# execution lanes (paper: cron_execute.sh @ 9:35 ET; live: cron_live_pilot_execute.sh).
#
# CONTRACT
# - Sourced by BOTH lane scripts AFTER their env-file loads (.env for paper,
#   ~/.caerus/live_pilot.env for live). These exports are SHARED DEFAULTS with
#   env-wins semantics (${VAR:-default}): an operator-set value in the lane's
#   env file (e.g. a tighter CAP_PCT / MAX_ORDERS / CONCENTRATED_MAX_WEIGHT set
#   as a live blast-radius restriction) is NEVER silently overridden here.
# - This file carries BEHAVIOR params only. It never touches approval/arming or
#   safety gates (CAERUS_LIVE_PILOT_APPROVED / _SUBMIT_APPROVED / _CRON_APPROVED /
#   _SCHEDULE_ENABLED / _KILL_SWITCH / account pin / endpoint), which remain
#   lane-owned and fail-closed.
# - Exposes CAERUS_LANE_PARAMS_FINGERPRINT (sha256 of the sorted key=value list)
#   so each lane can log it and any divergence between lanes (including a
#   deliberate env-file override) is visible in the two cron logs.

# Minimum per-trade notional. Low floor so the weight-priority rebudget can fill
# top targets on a small account instead of skipping every sub-$100 buy.
export CAERUS_LIVE_PILOT_MIN_TRADE_USD="${CAERUS_LIVE_PILOT_MIN_TRADE_USD:-10}"
# Buy-order blast-radius ceiling for a full rebalance (NOT "unlimited"). Sells are
# governed by the fail-closed sells master gate + whitelist/wildcard, not this cap.
export CAERUS_LIVE_PILOT_MAX_ORDERS="${CAERUS_LIVE_PILOT_MAX_ORDERS:-50}"
# Deploy fraction of portfolio value for the dynamic cap: 0.95 keeps a ~5% cash
# buffer so a full rebalance never drives buying power to ~0.
export CAERUS_LIVE_PILOT_CAP_PCT="${CAERUS_LIVE_PILOT_CAP_PCT:-0.95}"
# Concentrated-alpha per-name ceiling. Concentration is ALWAYS ON (no flag) and
# top-N is regime-adaptive in the engine (CAERUS_CONCENTRATED_TOP_N exists only as
# an emergency env override, deliberately NOT defaulted here). This ceiling must
# match cron_precompute so the execution-time RiskControls cap does not re-clip
# the concentrated book.
export CAERUS_CONCENTRATED_MAX_WEIGHT="${CAERUS_CONCENTRATED_MAX_WEIGHT:-0.50}"

# --- Params fingerprint (sha256 of sorted key=value list) ---------------------
_caerus_lane_params_sorted="$(printf '%s\n' \
    "CAERUS_CONCENTRATED_MAX_WEIGHT=${CAERUS_CONCENTRATED_MAX_WEIGHT}" \
    "CAERUS_LIVE_PILOT_CAP_PCT=${CAERUS_LIVE_PILOT_CAP_PCT}" \
    "CAERUS_LIVE_PILOT_MAX_ORDERS=${CAERUS_LIVE_PILOT_MAX_ORDERS}" \
    "CAERUS_LIVE_PILOT_MIN_TRADE_USD=${CAERUS_LIVE_PILOT_MIN_TRADE_USD}" \
    | LC_ALL=C sort)"
if command -v shasum >/dev/null 2>&1; then
    CAERUS_LANE_PARAMS_FINGERPRINT="$(printf '%s\n' "${_caerus_lane_params_sorted}" | shasum -a 256 | awk '{print $1}')"
elif command -v sha256sum >/dev/null 2>&1; then
    CAERUS_LANE_PARAMS_FINGERPRINT="$(printf '%s\n' "${_caerus_lane_params_sorted}" | sha256sum | awk '{print $1}')"
else
    CAERUS_LANE_PARAMS_FINGERPRINT="sha256_unavailable"
fi
export CAERUS_LANE_PARAMS_FINGERPRINT
unset _caerus_lane_params_sorted
