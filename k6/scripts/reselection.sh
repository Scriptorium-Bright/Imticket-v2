#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$task_root"
task_env="${K6_ENV_FILE:-k6/config/env.local.sh}"
if [[ -f "$task_env" ]]; then
  source "$task_env"
fi
task_kind="${1:?Usage: reselection.sh validation|smoke|pilot|baseline --config path [options]}"
shift
case "$task_kind" in
  validation) task_kind=journey-smoke; set -- --validate-seat-conflict --users 2 "$@" ;;
  smoke) task_kind=journey-smoke ;;
  pilot|baseline) ;;
  *) echo "Expected validation, smoke, pilot or baseline" >&2; exit 2 ;;
esac
exec python3 k6/scripts/run-experiment.py "$task_kind" --workload-model reselection "$@"
