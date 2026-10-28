#!/usr/bin/env bash
# After a 429 from the anonymous limit, when does the block really end?
# Sends one request every INTERVAL seconds (default 180) and logs each answer
# with its Retry-After. It stops at the first response that is neither a 429,
# a 5xx nor a transport error, for at most 90 minutes. For /api/v4/version an
# anonymous caller then gets 401, which is the normal unthrottled answer.
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh
url=${URL:-https://gitlab.com/api/v4/version}
interval=${INTERVAL:-180}
out="results/unblock-watch-$(date -u +%Y%m%dT%H%M%SZ).tsv"
echo "# unblock-watch.sh sha256=$(sha256sum unblock-watch.sh lib.sh | sha256sum | cut -c1-12) url=$url interval=$interval started=$(date -u +%FT%TZ)" > "$out"
printf 'utc\tstatus\tratelimit_name\tretry_after\n' >> "$out"
end=$(( $(date +%s) + 5400 ))
while (( $(date +%s) < end )); do
  fetch "$url"
  printf '%s\t%s\t%s\t%s\n' "$(date -u +%T)" "$status" "$(header ratelimit-name)" "$(header retry-after)" >> "$out"
  [[ $status != 429 && $status != ERR* && $status != 5* ]] && break
  sleep "$interval"
done
echo "$out"
tail -n +3 "$out"
