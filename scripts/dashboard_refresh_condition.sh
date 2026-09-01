#!/usr/bin/env bash
set -euo pipefail

# systemd ExecCondition semantics: exit 1 skips the non-capital refresh without
# marking it failed; exit 0 permits it. Test-only clock overrides make the
# production windows deterministic under pytest.
weekday="${CAERUS_CLOCK_WEEKDAY:-$(TZ=America/New_York date +%u)}"
hhmm="${CAERUS_CLOCK_HHMM:-$(TZ=America/New_York date +%H%M)}"

if [[ ! "${weekday}" =~ ^[1-7]$ || ! "${hhmm}" =~ ^[0-2][0-9][0-5][0-9]$ ]]; then
    echo "invalid dashboard refresh clock: weekday=${weekday} hhmm=${hhmm}" >&2
    exit 255
fi

hour=$((10#${hhmm:0:2}))
minute=$((10#${hhmm:2:2}))
if (( hour > 23 )); then
    echo "invalid dashboard refresh hour: ${hour}" >&2
    exit 255
fi
minute_of_day=$((hour * 60 + minute))

in_window() {
    local start_minute="$1"
    local end_minute="$2"
    (( minute_of_day >= start_minute && minute_of_day < end_minute ))
}

if (( weekday <= 5 )); then
    # Security master + canonical precompute.
    if in_window 395 450; then exit 1; fi
    # Monday weekly review.
    if (( weekday == 1 )) && in_window 450 525; then exit 1; fi
    # Paper/Live execution through confirmation.
    if in_window 560 615; then exit 1; fi
    # Hydration, broker ledger, portfolio history, and daily audit.
    if in_window 1095 1215; then exit 1; fi
    # Operating truth and CIO reporting.
    if in_window 1250 1275; then exit 1; fi
fi

exit 0
