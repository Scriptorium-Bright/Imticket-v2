#!/usr/bin/env bash
set -euo pipefail

duration="${1:-60s}"
timestamp="$(date +%Y%m%d-%H%M%S)"
output="/profiles/jfr-${timestamp}.jfr"
recording="imticket-${timestamp}"

docker exec imticket-v2-app \
  jcmd 1 JFR.start \
  "name=${recording}" \
  settings=profile \
  "duration=${duration}" \
  "filename=${output}"

echo "JFR recording started: ${duration}"
echo "Output: profiles/jfr-${timestamp}.jfr"
