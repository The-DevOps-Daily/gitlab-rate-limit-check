#!/usr/bin/env bash
# Which anonymous requests count toward GitLab.com's 60-per-hour limit?
#
# Needs a clean start: an IP that is not blocked and has sent no anonymous
# GitLab.com requests for at least an hour. A block can outlast the hour in
# which it started, so "nothing this hour" is not enough. It sends:
#   1. 10 requests to a project in the gitlab-org group
#   2. 10 requests to an endpoint outside any namespace (/api/v4/version)
#   3. requests to a project in a personal namespace until the first 429
# If gitlab-org requests are not counted and the others are, step 3 gets
# 60 - 10 = 50 successes before the 429. Any 429 in steps 1 or 2 means the
# IP was already limited, and the run is inconclusive.
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh
mkdir -p results
out="results/count-test-$(date -u +%Y%m%dT%H%M%SZ).tsv"
echo "# count-test.sh sha256=$(sha256sum count-test.sh lib.sh | sha256sum | cut -c1-12) started=$(date -u +%FT%TZ)" > "$out"
printf 'step\tn\tutc\tstatus\tratelimit_name\tretry_after\turl\n' >> "$out"

hit() {
  fetch "$3"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$(date -u +%T)" "$status" \
    "$(header ratelimit-name)" "$(header retry-after)" "$3" >> "$out"
}

for n in $(seq 1 10); do hit paid "$n" "https://gitlab.com/api/v4/projects/gitlab-org%2Fgitlab-runner"; sleep 1; done
for n in $(seq 1 10); do hit unscoped "$n" "https://gitlab.com/api/v4/version"; sleep 1; done
for n in $(seq 1 70); do
  hit personal "$n" "https://gitlab.com/api/v4/projects/dwt1%2Fdotfiles"
  [[ $status == 429 ]] && break
  sleep 1
done

echo "$out"
awk -F'\t' 'NR > 2 { c[$1 " " $4]++ } END { for (k in c) print c[k], k }' "$out" | sort -k2
early=$(awk -F'\t' '($1 == "paid" || $1 == "unscoped") && $4 == 429' "$out" | wc -l)
ok=$(awk -F'\t' '$1 == "personal" && $4 == 200' "$out" | wc -l)
if (( early > 0 )); then
  echo "INCONCLUSIVE: $early request(s) in steps 1-2 were already limited"
else
  echo "personal-namespace successes before the first 429: $ok (prediction: 50)"
fi
