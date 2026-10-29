#!/usr/bin/env bash
# When exactly does a block from the hourly limit end? Sends one request at
# each UTC time in TIMES (HH:MM:SS, space-separated, in order, today) and
# records the answer. With BURST_AT, it then sends requests back to back from
# that time until the first 429 (at most 70).
# `predict-window.py --at TIMES --burst-at BURST_AT` says what each model expects.
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh
url=${URL:-https://gitlab.com/api/v4/projects/dwt1%2Fdotfiles}
results=${RESULTS:-results}
mkdir -p "$results"
out="$results/reset-test-$(date -u +%Y%m%dT%H%M%SZ).tsv"
echo "# reset-test.sh sha256=$(sha256sum reset-test.sh lib.sh | sha256sum | cut -c1-12) url=$url times=${TIMES// /,} burst_at=${BURST_AT:-none} started=$(date -u +%FT%TZ)" > "$out"
printf 'step\tn\tutc\tstatus\tratelimit_name\tretry_after\tutc_end\n' >> "$out"
n=0
for at in $TIMES; do
  n=$(( n + 1 ))
  d=$(( $(date -u -d "$at" +%s) - $(date +%s) ))
  (( d > 0 )) && sleep "$d"
  t=$(date -u +%FT%T.%3NZ)
  fetch -4 "$url"
  printf 'R\t%s\t%s\t%s\t%s\t%s\t%s\n' "$n" "$t" "$status" "$(header ratelimit-name)" "$(header retry-after)" "$(date -u +%FT%T.%3NZ)" >> "$out"
done
if [[ -n ${BURST_AT:-} ]]; then
  d=$(( $(date -u -d "$BURST_AT" +%s) - $(date +%s) ))
  (( d > 0 )) && sleep "$d"
  for n in $(seq 1 70); do
    t=$(date -u +%FT%T.%3NZ)
    fetch -4 "$url"
    printf 'B\t%s\t%s\t%s\t%s\t%s\t%s\n' "$n" "$t" "$status" "$(header ratelimit-name)" "$(header retry-after)" "$(date -u +%FT%T.%3NZ)" >> "$out"
    [[ $status == 429 || $status == ERR* ]] && break
  done
fi
echo "# ended=$(date -u +%FT%TZ)" >> "$out"
echo "$out"
tail -n +3 "$out" | grep -v '^#' | awk -F'\t' '$1 == "R" { print $3, $4, $5, $6 } $1 == "B" { c[$4 " " $5]++ } END { for (k in c) print c[k], "x", k }'
