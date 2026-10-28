#!/usr/bin/env bash
# Send N requests to GitLab.com, with a one-second pause between them, and record
# the status and rate-limit headers of each.
#
#   ./probe.sh api 70        # anonymous API requests
#   ./probe.sh raw 70        # anonymous raw-file (web) requests, each one past the CDN cache
#   CACHE=1 ./probe.sh raw 70   # the same URL every time, so the CDN can answer from cache
#   PROJECT=dwt1%2Fdotfiles ./probe.sh api 70  # another project (URL-encoded path)
#   GITLAB_TOKEN=glpat-... ./probe.sh api 10   # the same, authenticated
#
# Output: results/<kind>-<anon|token>-<UTC time>.tsv, stamped with this script's hash.
set -euo pipefail

kind=${1:-api}
count=${2:-10}
interval=${INTERVAL:-1}
project=${PROJECT:-gitlab-org%2Fgitlab-runner}
case "$kind" in
  api) url="https://gitlab.com/api/v4/projects/$project" ;;
  raw) url="https://gitlab.com/${project//%2F//}/-/raw/HEAD/README.md" ;;
  *) echo "usage: $0 api|raw [count]" >&2; exit 2 ;;
esac

auth=()
who=anon
if [[ -n "${GITLAB_TOKEN:-}" ]]; then
  auth=(--header "PRIVATE-TOKEN: $GITLAB_TOKEN")
  who=token
fi

cd "$(dirname "$0")"
. ./lib.sh
mkdir -p results
out="results/$kind-$who-$(date -u +%Y%m%dT%H%M%SZ).tsv"
{
  echo "# probe.sh sha256=$(sha256sum probe.sh | cut -c1-12) url=$url started=$(date -u +%FT%TZ)"
  printf 'n\tutc\tstatus\tcf_cache\tratelimit_name\tlimit\tremaining\tretry_after\n'
} > "$out"

for n in $(seq 1 "$count"); do
  # A cached raw file never reaches GitLab's rate limiter, and its headers are a stale copy.
  target=$url
  [[ $kind == raw && -z "${CACHE:-}" ]] && target="$url?probe=$(date +%s%N)"
  fetch "${auth[@]}" "$target"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$n" "$(date -u +%T)" "$status" \
    "$(header cf-cache-status)" "$(header ratelimit-name)" "$(header ratelimit-limit)" \
    "$(header ratelimit-remaining)" "$(header retry-after)" >> "$out"
  sleep "$interval"
done

echo "$out"
# Count each distinct status / cache / throttle / limit combination.
tail -n +3 "$out" | cut -f3-6 | sort | uniq -c
