#!/usr/bin/env bash
# Does the unnamed Cloudflare 429 count requests or error responses? Sends N
# requests (default 60), P at a time (default 4), much faster than the
# back-to-back bursts that hit it, and records each response.
set -euo pipefail
cd "$(dirname "$0")"
export URL=${URL:-https://gitlab.com/api/v4/projects/gitlab-org%2Fgitlab-runner}
n=${N:-60} p=${P:-4}
results=${RESULTS:-results}
mkdir -p "$results"
export OUT="$results/parallel-burst-$(date -u +%Y%m%dT%H%M%SZ).tsv"
echo "# parallel-burst.sh sha256=$(sha256sum parallel-burst.sh | cut -c1-12) url=$URL n=$n p=$p started=$(date -u +%FT%TZ)" > "$OUT"
printf 'n\tutc\tstatus\tratelimit_name\tretry_after\tserver\n' >> "$OUT"
. ./lib.sh
one() {
  local t
  t=$(date -u +%FT%T.%3NZ)
  fetch -4 "$URL"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$t" "$status" "$(header ratelimit-name)" "$(header retry-after)" "$(header server)" >> "$OUT"
}
export -f fetch header one
seq 1 "$n" | xargs -P "$p" -I{} bash -c 'one {}'
echo "# ended=$(date -u +%FT%TZ)" >> "$OUT"
echo "$OUT"
grep -v '^#' "$OUT" | tail -n +2 | cut -f3,4 | sort | uniq -c
