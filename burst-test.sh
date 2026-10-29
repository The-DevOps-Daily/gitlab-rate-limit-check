#!/usr/bin/env bash
# window-test.sh's burst was stopped by a 429 with no RateLimit-Name and
# Retry-After: 60, not by the hourly limit. Which limit was it, and does the
# hourly limit then stop at exactly 60 requests in the last 3,600 seconds?
#   C. requests back to back until the first 429; that 429's headers and body
#      are saved, without cookies, the CSP and the three headers that carry the
#      Cloudflare ray ID (its suffix names the edge location), and with IP
#      addresses and ray IDs removed from the body
#   D. 75 s later, one request every 1.5 s until the first 429
# If C's burst counts and the window slides, D gets 60 - (counted in the last
# hour before D) successes, then a 429 named unauthenticated_traffic_per_ip.
set -euo pipefail
cd "$(dirname "$0")"
url=${URL:-https://gitlab.com/api/v4/version}
results=${RESULTS:-results}
mkdir -p "$results"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
out="$results/burst-test-$stamp.tsv" saved="$results/burst-test-$stamp-first-429.txt"
echo "# burst-test.sh sha256=$(sha256sum burst-test.sh | cut -c1-12) url=$url started=$(date -u +%FT%TZ)" > "$out"
printf 'step\tn\tutc\tstatus\tratelimit_name\tretry_after\n' >> "$out"

hdr=$(mktemp) body=$(mktemp)
trap 'rm -f "$hdr" "$body"' EXIT
hit() {
  local t
  t=$(date -u +%FT%T.%3NZ)
  status=$(curl -q -4 -s -o "$body" -D "$hdr" -w '%{http_code}' "$url") || status="ERR$?"
  h() { tr -d '\r' < "$hdr" | awk -v k="$1" 'tolower($1) == tolower(k ":") { print $2 }'; }
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$t" "$status" "$(h ratelimit-name)" "$(h retry-after)" >> "$out"
}
save() {
  { echo "# $1 at $(date -u +%FT%TZ): response headers, then the first 600 bytes of the body"
    tr -d '\r' < "$hdr" | grep -viE '^(cf-ray|x-request-id|x-gitlab-meta|set-cookie|nel|report-to|content-security-policy):'
    head -c 600 "$body" | sed -E 's/[0-9]{1,3}(\.[0-9]{1,3}){3}/<ip>/g; s/[0-9a-fA-F]{0,4}(:[0-9a-fA-F]{0,4}){3,7}/<ip>/g; s/(Ray ID:?[^0-9a-f]*)[0-9a-f]{16}/\1<ray>/Ig'
    echo; } >> "$saved"
}

for n in $(seq 1 60); do
  hit C "$n"
  [[ $status == 429 ]] && { save "C$n"; break; }
  [[ $status == ERR* ]] && break
done
sleep "${PAUSE:-75}"
for n in $(seq 1 60); do
  hit D "$n"
  [[ $status == 429 ]] && { save "D$n"; break; }
  [[ $status == ERR* ]] && break
  sleep "${INTERVAL:-1.5}"
done
echo "# ended=$(date -u +%FT%TZ)" >> "$out"
echo "$out"
grep -v '^#' "$out" | tail -n +2 | cut -f1,4,5 | sort | uniq -c
