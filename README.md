# gitlab-rate-limit-check

```bash
python3 find_anon_calls.py path/to/your/repo     # anonymous gitlab.com calls in CI files and scripts
./probe.sh api 10                                 # what GitLab.com's rate limiter tells this IP right now
python3 budget.py --plan free gitlab-requests.jsonl   # busiest minute and hour against the plan limits
```

GitLab.com announced plan-based rate limits for Free and unauthenticated traffic from 2026-10-19, with Premium and Ultimate following in January 2027. Unauthenticated requests get 60 per hour per IP address. This repo measures what that looks like from the client side and gives you three tools to get ahead of it.

Write-up: [DevOps Daily](https://devops-daily.com/posts/) (link updated when the post is live).

## What we measured (2026-10-28, one IP)

| Run | Requests | Result |
| --- | --- | --- |
| `results/api-anon-20261028T224219Z.tsv`, `api-anon-20261028T224902Z.tsv` | 140 anonymous API requests to `gitlab-org/gitlab-runner` | all 200, only the old `throttle_unauthenticated_api` (500/min) in the headers |
| `results/raw-anon-cached-20261028T224644Z.tsv` | 70 raw-file requests, same URL | all 200 with `RateLimit-Remaining` frozen at 494. A manual check two minutes later (`results/raw-cache-headers-manual-20261028T224834Z.txt`) showed `cf-cache-status: HIT`: Cloudflare answered from its cache |
| `results/raw-anon-20261028T225046Z.tsv` | 70 raw-file requests with a cache-busting query | all 200 (`MISS`), only the old `throttle_unauthenticated_web` (500/min) |
| `results/api-anon-20261028T225259Z.tsv` | 70 anonymous API requests to a project in a personal namespace | 58 × 200, then 12 × 429 named `unauthenticated_traffic_per_ip`, `Retry-After` counting down to 23:00:00 UTC |
| `results/blocked-check-20261028T225508Z.tsv` | one request each to six targets while blocked | `gitlab-org` and `inkscape` projects: 200; two personal projects and `/api/v4/version`: 429 |
| `results/count-test-20261028T230045Z.tsv` | `count-test.sh` 45 s after the hour | still blocked: `gitlab-org` 200, everything else 429 with `Retry-After` pointing to midnight |
| `results/unblock-watch-20261028T230207Z.tsv` | one request every 3 minutes | 429 until 23:35:12, not limited at 23:38:12: the block lasted 41 to 44 minutes |

The first API run was made with an earlier copy of the probe loop, before it became `probe.sh`, and the blocked check was a one-off loop, so those two files have no hash stamp. The other script-written files are stamped with the hash of the script that wrote them. The scripts have changed since (shared `lib.sh`, final status from curl's write-out); the hash in each file names the version that produced it.

The headers of a successful response never mention the new limit: the last 200 before the first 429 still said `RateLimit-Remaining: 476` for the old per-minute throttle. Every `Retry-After` on the 429s pointed to the end of the current clock hour, and the block outlasted the first one by more than half an hour.

## The scripts

- `probe.sh api|raw [count]` sends requests with a one-second pause between them and writes status, CDN cache status, `RateLimit-Name`, `RateLimit-Limit`, `RateLimit-Remaining` and `Retry-After` per request to `results/`. Raw-file probes add a cache-busting query unless `CACHE=1`, because a cached response never reaches the rate limiter. `PROJECT=` picks the project; `GITLAB_TOKEN=` sends a token.
- `count-test.sh` checks which anonymous requests count toward the hourly limit. It needs a clean start: an IP that is not blocked and has sent no anonymous GitLab.com requests for at least an hour, because a block can outlast the hour it started in. It reports the run as inconclusive if the first steps are already limited.
- `find_anon_calls.py` finds curl, wget, PowerShell, Dockerfile `ADD` and similar calls to `gitlab.com`, `$CI_SERVER_URL` or `$CI_API_V4_URL` that carry no token, and says what to use instead. Git over HTTPS is skipped: it is not counted against the anonymous limit. Exits 1 on findings, so it can run in CI. It assumes `$CI_SERVER_URL` is gitlab.com.
- `glcurl` is a curl wrapper that logs one JSON line per call (time, egress, caller, path, final status, number of HTTP responses including followed redirects, rate-limit headers) without logging the token. Set `GLCURL_EGRESS` to the public IP or NAT the machine leaves from, and `GLCURL_CALLER` to the GitLab user behind a token. It accepts one URL per call and refuses `--retry`, so every request ends up in the log.
- `budget.py` reads those logs and reports each caller's busiest 60-second and 3,600-second window against `limits.json`. Anonymous requests are grouped by egress, because the anonymous limit is per IP; token requests by caller, because plan limits are per user. The sliding windows are a budgeting model: GitLab does not document its counting rule, and we could not determine it.
- `backoff.py` is a GET that waits `Retry-After` (seconds or an HTTP date) on a 429, falls back to capped exponential backoff with full jitter when a 429 has no usable header, and waits for the reset when `RateLimit-Remaining` gets low. For the anonymous limit, treat `Retry-After` as a minimum.
- `examples/make_nat_log.py` writes a synthetic log (not a measurement) for a CI fleet behind one NAT address, to show `budget.py` output.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The scanner tests include a break test that removes the job-token header from the clean fixture, and edge cases: a token on one command must not cover the next, a comment is not auth, `gitlab.com.example.org` is another host, and YAML folded commands are joined. The scanner is a pattern matcher: it misses URLs built at runtime. The backoff and `glcurl` tests run against a local mock of GitLab's 429 responses, with and without headers; no test touches gitlab.com.

## Limits

`limits.json` holds the plan limits from GitLab's [rate limits page](https://docs.gitlab.com/user/gitlab_com/rate_limits/) as retrieved on 2026-10-28. GitLab can change them; check the page before relying on the numbers.

MIT licensed.
