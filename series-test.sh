#!/usr/bin/env bash
# Does the hourly count slide, or reset 3,600 s after the first request?
# Run it while the IP is blocked. At START (default: 120 s past the next full
# hour) it sends one request every INTERVAL seconds (default 1.5, plus the
# request time) until the first 429, at most MAX (default 60).
# `predict-window.py --series` says what each model expects.
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh
url=${URL:-https://gitlab.com/api/v4/version}
hour=$(( ($(date +%s) / 3600 + 1) * 3600 ))
start=${START:-$(( hour + 120 ))}
results=${RESULTS:-results}
mkdir -p "$results"
out="$results/series-test-$(date -u -d @"$start" +%Y%m%dT%H%M%SZ).tsv"
echo "# series-test.sh sha256=$(sha256sum series-test.sh lib.sh | sha256sum | cut -c1-12) url=$url start=$(date -u -d @"$start" +%FT%TZ)" > "$out"
printf 'step\tn\tutc\tstatus\tratelimit_name\tretry_after\tutc_end\n' >> "$out"
d=$(( start - $(date +%s) ))
(( d > 0 )) && sleep "$d"
for n in $(seq 1 "${MAX:-60}"); do
  t=$(date -u +%FT%T.%3NZ)
  fetch -4 "$url"
  printf 'S\t%s\t%s\t%s\t%s\t%s\t%s\n' "$n" "$t" "$status" "$(header ratelimit-name)" "$(header retry-after)" "$(date -u +%FT%T.%3NZ)" >> "$out"
  [[ $status == 429 || $status == ERR* ]] && break
  sleep "${INTERVAL:-1.5}"
done
echo "# ended=$(date -u +%FT%TZ)" >> "$out"
echo "$out"
grep -v '^#' "$out" | tail -n +2 | cut -f4,5 | sort | uniq -c
