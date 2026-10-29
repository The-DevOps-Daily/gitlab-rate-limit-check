# gitlab-rate-limit-check

```bash
python3 find_anon_calls.py path/to/your/repo     # anonymous gitlab.com calls in CI files and scripts
./probe.sh api 10                                 # what GitLab.com's rate limiter tells this IP right now
python3 budget.py --plan free gitlab-requests.jsonl   # busiest minute and hour against the plan limits
```

GitLab.com announced plan-based rate limits for Free and unauthenticated traffic from 2026-10-19, with Premium and Ultimate following in January 2027. Unauthenticated requests get 60 per hour per IP address. This repo measures what that looks like from the client side and gives you three tools to get ahead of it.

Write-up: [GitLab.com's 60-requests-an-hour limit is live, and the headers won't warn you](https://devops-daily.com/posts/gitlab-anonymous-rate-limit-60-per-hour)

## What we measured (2026-10-28 and 29, one IP)

| Run | Requests | Result |
| --- | --- | --- |
| `results/api-anon-20261028T224219Z.tsv`, `api-anon-20261028T224902Z.tsv` | 140 anonymous API requests to `gitlab-org/gitlab-runner` | all 200, only the old `throttle_unauthenticated_api` (500/min) in the headers |
| `results/raw-anon-cached-20261028T224644Z.tsv` | 70 raw-file requests, same URL | all 200 with `RateLimit-Remaining` frozen at 494. A manual check two minutes later (`results/raw-cache-headers-manual-20261028T224834Z.txt`) showed `cf-cache-status: HIT`: Cloudflare answered from its cache |
| `results/raw-anon-20261028T225046Z.tsv` | 70 raw-file requests with a cache-busting query | all 200 (`MISS`), only the old `throttle_unauthenticated_web` (500/min) |
| `results/api-anon-20261028T225259Z.tsv` | 70 anonymous API requests to a project in a personal namespace | 58 × 200, then 12 × 429 named `unauthenticated_traffic_per_ip`, `Retry-After` counting down to 23:00:00 UTC |
| `results/blocked-check-20261028T225508Z.tsv` | one request each to six targets while blocked | `gitlab-org` and `inkscape` projects: 200; two personal projects and `/api/v4/version`: 429 |
| `results/count-test-20261028T230045Z.tsv` | `count-test.sh` 45 s after the hour | still blocked: `gitlab-org` 200, everything else 429 with `Retry-After` pointing to midnight |
| `results/unblock-watch-20261028T230207Z.tsv` | one request every 3 minutes | 429 until 23:35:12, not limited at 23:38:12: the block lasted about 41 to 44 minutes |
| `results/count-test-20261029T010045Z.tsv` | `count-test.sh` from a clean start, 01:00:45 UTC on 2026-10-29 | 10 × 200 from `gitlab-org`, 10 × 401 from `/api/v4/version`, then exactly 50 × 200 from the personal project before the 429, as predicted: `gitlab-org` requests did not count, and the 401s did |
| `results/window-test-20261029T020000Z.tsv` | `window-test.sh`, predictions in `results/window-test-prediction-20261029T010858Z.txt`, committed before the run | 01:50:00 and 02:00:30: 429 (a clock-hour window allows the second); from 02:01:45, 25 back to back allowed (a weighted estimate allows 1). A sliding 60-minute window and a counter that resets an hour after its first request both match. Request 26 got a different 429: no `RateLimit-Name`, `Retry-After: 60`, `server: cloudflare` |
| `results/burst-test-20261029T020328Z.tsv` | `burst-test.sh`, prediction in `results/burst-test-prediction-20261029T020323Z.txt` | 25 back-to-back 401s, then the same Cloudflare 429 ("elevated error rates", saved in `...-first-429.txt`); after 75 s, exactly 10 more before `unauthenticated_traffic_per_ip`, as predicted: 25 + 25 + 10 = 60 |
| `results/burst-test-20261029T020555Z.tsv`, `results/parallel-burst-20261029T021027Z.tsv` | 60 back to back, then 60 four at a time, to `gitlab-org/gitlab-runner` while the IP was blocked by the hourly limit | all 200: successful requests did not trip the Cloudflare limit, even at 60 in 8 s |
| `results/burst-test-20261029T021742Z.tsv` | 60 back to back, then 60 more, for a missing file in `gitlab-org/gitlab-runner` (404s) | all 404, no Cloudflare 429: it does not apply one error count to every route |
| `results/series-test-20261029T030200Z.tsv` | `series-test.sh`, one request every 1.9 s from 03:02:00, to `/api/v4/version` | 25 × 401, then the Cloudflare 429 again at request 26, after 48 s: inconclusive for the hourly limit |
| `results/series-test-20261029T030400Z.tsv` | the same with a personal project (200s) from 03:04:00, prediction in `results/series-test-prediction-20261029T030346Z.txt` | 35 × 200, then `unauthenticated_traffic_per_ip`: the resetting counter predicted 35, the sliding window 25 |
| `results/reset-test-20261029T030649Z.tsv` | `reset-test.sh`: one request each at 04:00:30, 04:01:50 and 04:02:10, then back to back from 04:02:15, prediction in `results/reset-test-prediction-20261029T030637Z.txt` | 429, 429 (both with `Retry-After` pointing to 05:00:00), then 200, then 59 × 200 before the next 429: the count that started at 03:02:00.5 reset between 04:01:50 and 04:02:10. Scored per response, given the requests GitLab had accepted before it: the resetting counter matched 63 of 63, the clock hour 61, the sliding window 13, the weighted estimate 4 |

The first API run was made with an earlier copy of the probe loop, before it became `probe.sh`, and the blocked check was a one-off loop, so those two files have no hash stamp. Two earlier requests that counted on 2026-10-28, `/api/v4/version` at 22:38:02 to 22:38:03 and a `/api/v4/projects` search at about 22:52:50, were one-off commands with no result file (the times come from our terminal log); the response to the first carried `ratelimit-reset: 1793227140` (22:39:00 UTC). The other script-written files are stamped with the hash of the script that wrote them. The scripts have changed since (shared `lib.sh`, final status from curl's write-out); the hash in each file names the version that produced it.

The headers of a successful response never mention the new limit: the last 200 before the first 429 still said `RateLimit-Remaining: 476` for the old per-minute throttle. Every `Retry-After` on the 429s pointed to the end of the current clock hour, but the count reset an hour after the first request that counted. With predictions committed before each run, a clock-hour window, a weighted estimate and a sliding 60-minute window each failed at least once; a counter that starts with the first request and resets about 3,600 s later matched every response from the hourly limit. Blocked requests did not extend the block, and the budget was exactly 60.

## The scripts

- `probe.sh api|raw [count]` sends requests with a one-second pause between them and writes status, CDN cache status, `RateLimit-Name`, `RateLimit-Limit`, `RateLimit-Remaining` and `Retry-After` per request to `results/`. Raw-file probes add a cache-busting query unless `CACHE=1`, because a cached response never reaches the rate limiter. `PROJECT=` picks the project; `GITLAB_TOKEN=` sends a token.
- `count-test.sh` checks which anonymous requests count toward the hourly limit. It needs a clean start: an IP that is not blocked and has sent no anonymous GitLab.com requests for at least an hour, because a block can outlast the hour it started in. It reports the run as inconclusive if the first steps are already limited.
- `window-test.sh`, `series-test.sh` and `reset-test.sh` check how the hourly limit is counted: per clock hour, over the last 60 minutes, or from the first request. `window-test.sh` Run it right after `count-test.sh` has blocked the IP. It sends one request 10 minutes before the next full hour, one 30 seconds after it, and a burst from 105 seconds after it until the first 429. At the start and the end it records an HMAC of the public IPv4 address, keyed with a local secret, as evidence that the address was the same at both times without publishing it. `series-test.sh` sends one request every 1.5 s from a given time until the first 429; `reset-test.sh` sends single requests at given times, then a burst.
- `burst-test.sh` sends requests back to back until the first 429 and saves that response (without the cookies and the headers that carry the Cloudflare ray ID), then sends more at a slower pace until the next 429. `parallel-burst.sh` sends requests several at a time, to tell a limit on requests from a limit on errors.
- `predict-window.py` says what four counting models expect from each of those runs (`--plan`, `--series`, `--at`), and afterwards scores each request against them (`--compare`). A 429 from another limit is not scored.
- `find_anon_calls.py` finds curl, wget, PowerShell, Dockerfile `ADD` and similar calls to `gitlab.com`, `$CI_SERVER_URL` or `$CI_API_V4_URL` that carry no token, and says what to use instead. Git over HTTPS is skipped: it is not counted against the anonymous limit. Exits 1 on findings, so it can run in CI. It assumes `$CI_SERVER_URL` is gitlab.com.
- `glcurl` is a curl wrapper that logs one JSON line per call (time, egress, caller, path, final status, number of HTTP responses including followed redirects, rate-limit headers) without logging the token. Set `GLCURL_EGRESS` to the public IP or NAT the machine leaves from, and `GLCURL_CALLER` to the GitLab user behind a token. It accepts one URL per call and refuses `--retry`, so every request ends up in the log.
- `budget.py` reads those logs and reports each caller's busiest 60-second and 3,600-second window against `limits.json`. Anonymous requests are grouped by egress, because the anonymous limit is per IP; token requests by caller, because plan limits are per user. The sliding windows are a conservative budgeting model: GitLab does not document its counting rule, and a counter that resets an hour after its first request, which matched our anonymous tests, never counts more than the busiest sliding hour. We did not test the authenticated limits.
- `backoff.py` is a GET that waits `Retry-After` (seconds or an HTTP date) on a 429, falls back to capped exponential backoff with full jitter when a 429 has no usable header, and waits for the reset when `RateLimit-Remaining` gets low. For the anonymous limit, `Retry-After` points to the end of the clock hour while the count resets an hour after its first request, so it can be too short or too long.
- `examples/make_nat_log.py` writes a synthetic log (not a measurement) for a CI fleet behind one NAT address, to show `budget.py` output.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The scanner tests include a break test that removes the job-token header from the clean fixture, and edge cases: a token on one command must not cover the next, a comment is not auth, `gitlab.com.example.org` is another host, and YAML folded commands are joined. The scanner is a pattern matcher: it misses URLs built at runtime. The backoff and `glcurl` tests run against a local mock of GitLab's 429 responses, with and without headers; no test touches gitlab.com.

## Limits

`limits.json` holds the plan limits from GitLab's [rate limits page](https://docs.gitlab.com/user/gitlab_com/rate_limits/) as retrieved on 2026-10-28. GitLab can change them; check the page before relying on the numbers.

MIT licensed.
