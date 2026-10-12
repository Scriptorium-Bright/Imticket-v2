#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$task_root"
task_env="${K6_ENV_FILE:-k6/config/env.local.sh}"
if [[ -f "$task_env" ]]; then
  source "$task_env"
fi
exec python3 k6/scripts/run-experiment.py pilot "$@"
