#!/usr/bin/env bash
# Thin operator-ergonomics wrapper around `python -m scripts.research_mcp_ask`.
# Resolves the repo root via the script's own location so it works from any
# working directory, then re-execs into the Python entry point with the same
# arguments. No environment manipulation, no implicit defaults — read the
# Python module for the real interface.

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
exec python -m scripts.research_mcp_ask "$@"
