#!/usr/bin/env bash
# Re-capture Hermes CLI golden fixtures.
# Usage: bash backend/scripts/capture_hermes_fixtures.sh
#
# This script is intentionally minimal — it only captures outputs that don't
# require an unconfigured probe profile. For the `profile_show_*`, `profile_create_*`,
# and `gateway_status_*` fixtures you must:
#   1. create a throwaway profile (`hermes profile create test-research-probe --no-alias`)
#   2. capture the relevant outputs by hand
#   3. delete the profile (`hermes profile delete -y test-research-probe`)
#
# Anything captured here is overwritten — review `git diff` before committing.
set -euo pipefail

if ! command -v hermes >/dev/null; then
  echo "ERROR: 'hermes' not on PATH. Install Hermes v0.8+ first." >&2
  exit 1
fi

FIX_DIR="$(cd "$(dirname "$0")"/.. && pwd)/tests/fixtures/hermes-cli"
mkdir -p "$FIX_DIR"

echo "Capturing to $FIX_DIR (hermes $(hermes --version | head -n1))..."

hermes profile list > "$FIX_DIR/profile_list_2_profiles.txt" 2>&1 || true

# NOTE: profile_show / create / gateway captures require an unconfigured test profile.
# Edit this script and re-run if your environment changes.

if [[ -f "$HOME/.hermes/gateway.pid" ]]; then
  cp "$HOME/.hermes/gateway.pid" "$FIX_DIR/gateway_pid_default.json"
fi

echo "Done. Review diffs before committing."
