#!/usr/bin/env bash
set -euo pipefail

event="${1:-cpu}"
duration="${2:-30}"

case "$event" in
  cpu|wall|alloc|lock) ;;
  *)
    echo "Usage: $0 [cpu|wall|alloc|lock] [duration-seconds]" >&2
    exit 1
    ;;
esac

case "$duration" in
  ''|*[!0-9]*)
    echo "duration-seconds must be a positive integer" >&2
    exit 1
    ;;
esac

timestamp="$(date +%Y%m%d-%H%M%S)"
output="/profiles/async-${event}-${timestamp}.html"

docker exec imticket-v2-app \
  asprof -e "$event" -d "$duration" -f "$output" 1

echo "Profile complete: profiles/async-${event}-${timestamp}.html"
