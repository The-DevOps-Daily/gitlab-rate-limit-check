#!/usr/bin/env bash
# Is the anonymous limit counted per clock hour or over the last 60 minutes?
#
# Run it right after count-test.sh has blocked the IP, and send no other
# anonymous GitLab.com requests until it is done. It sends:
#   P. one request 10 minutes before the next full hour
#   A. one request at 30 s past the next full hour
#   B. requests back to back from 105 s past the hour until the first 429
# Run `predict-window.py --plan` first to see what each model expects, and
# `predict-window.py HISTORY TEST` afterwards to compare.
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh
url=${URL:-https://gitlab.com/api/v4/version}
hour=$(( ($(date +%s) / 3600 + 1) * 3600 ))
p=$(( hour + ${P_OFFSET:--600} )) a=$(( hour + ${A_OFFSET:-30} )) b=$(( hour + ${B_OFFSET:-105} ))

# Shows the public IPv4 address was the same at the start and the end without
# recording it: an HMAC with a local secret that is not in this repository.
# "unverified" if the lookup failed.
secret=${IP_SECRET_FILE:-$HOME/.config/gitlab-rate-limit-check.secret}
[[ -s $secret ]] || (umask 077; head -c 32 /dev/urandom | base64 > "$secret")
ip_id() {
  local ip
  ip=$(curl -q -4 -s -m 10 "${IP_CHECK_URL:-https://www.cloudflare.com/cdn-cgi/trace}" | sed -n 's/^ip=//p') || true
  [[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || { echo unverified; return; }
  echo "$ip" | openssl dgst -sha256 -hmac "$(cat "$secret")" | awk '{ print substr($NF, 1, 12) }'
}

results=${RESULTS:-results}
mkdir -p "$results"
out="$results/window-test-$(date -u -d @"$hour" +%Y%m%dT%H%M%SZ).tsv"
echo "# window-test.sh sha256=$(sha256sum window-test.sh lib.sh | sha256sum | cut -c1-12) url=$url p=$(date -u -d @"$p" +%FT%TZ) a=$(date -u -d @"$a" +%FT%TZ) b=$(date -u -d @"$b" +%FT%TZ) ip_id=$(ip_id)" > "$out"
printf 'step\tn\tutc\tstatus\tratelimit_name\tretry_after\n' >> "$out"

hit() {
  local t
  t=$(date -u +%FT%T.%3NZ)
  fetch -4 "$url"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$t" "$status" "$(header ratelimit-name)" "$(header retry-after)" >> "$out"
}

wait_until() { local d=$(( $1 - $(date +%s) )); (( d > 0 )) && sleep "$d"; return 0; }

wait_until "$p"
hit P 1
wait_until "$a"
hit A 1
wait_until "$b"
for n in $(seq 1 80); do
  hit B "$n"
  [[ $status == 429 || $status == ERR* ]] && break
done
echo "# ended=$(date -u +%FT%TZ) ip_id=$(ip_id)" >> "$out"
echo "$out"
grep -v '^#' "$out" | tail -n +2 | cut -f1,4 | sort | uniq -c
