#!/usr/bin/env bash
# Refuse to publish secrets or home-lab identifiers. This repository is public.
# Generic patterns live here; host-specific ones live in the gitignored
# docs/local/scan-patterns.txt so the scanner itself leaks nothing.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

patterns=(
  'feed\.piyolog\.com/v1/feed/[^/[:space:]]+/[0-9a-f]{8}'
  '[a-z0-9-]+\.ts\.net'
  '\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}\b'
  '\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b'
)
local_patterns=docs/local/scan-patterns.txt
if [ -f "$local_patterns" ]; then
  while IFS= read -r p; do [ -n "$p" ] && patterns+=("$p"); done < "$local_patterns"
else
  echo "warning: $local_patterns missing; host-specific checks skipped" >&2
fi

files=$(git ls-files -co --exclude-standard | grep -v '^scripts/scan\.sh$' || true)
[ -z "$files" ] && { echo "scan: nothing to check"; exit 0; }

hits=0
for i in "${!patterns[@]}"; do
  if matches=$(printf '%s\n' "$files" | xargs grep -lE -- "${patterns[$i]}" 2>/dev/null); then
    # Print the pattern index, never the pattern: local patterns are themselves sensitive.
    printf 'scan: pattern #%d matched in:\n%s\n' "$i" "$matches" >&2
    hits=$((hits + 1))
  fi
done
[ "$hits" -eq 0 ] && echo "scan: clean ($(printf '%s\n' "$files" | wc -l | tr -d ' ') files)"
[ "$hits" -eq 0 ]
