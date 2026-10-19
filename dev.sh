#!/usr/bin/env bash
# Start the WalkingPad app in development mode (auto-reload on code changes).
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source venv/bin/activate
elif [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

exec python dev.py
