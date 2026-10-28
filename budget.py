#!/usr/bin/env python3
"""Compare your busiest minute and busiest hour of GitLab.com requests with the plan limits.

Usage: budget.py [--plan free|premium|ultimate] [--warn 0.8] requests.jsonl [...]

Reads JSON lines with at least "ts" (Unix seconds) and "who" ("anon" for requests
without a token, anything else for a token), as written by ./glcurl. Anonymous
requests are grouped by "egress" (the public IP they leave from; set GLCURL_EGRESS),
or by log file path when a line has none, because the anonymous limit is per IP.
Token requests are grouped by "who", which glcurl sets to GLCURL_CALLER when you
name the GitLab user behind a token: plan limits are per user, not per token.
A line's "requests" field (default 1) counts redirects followed in one call.
The sliding windows here are a budgeting model, stricter than or equal to any
fixed window; GitLab's exact counting rule is not documented.
Exits 1 when any caller uses more than --warn of a limit.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def busiest(times, window):
    """Largest number of requests in any sliding window of `window` seconds."""
    times = sorted(times)
    best, left = 0, 0
    for right, t in enumerate(times):
        while t - times[left] >= window:
            left += 1
        best = max(best, right - left + 1)
    return best


def load(paths):
    callers = defaultdict(list)
    for path in paths:
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                who = r["who"]
                key = f"anon@{r.get('egress') or path}" if who == "anon" else f"token:{who}"
                callers[key].extend([float(r["ts"])] * int(r.get("requests", 1)))
    return callers


def report(callers, plan, warn, limits):
    rows, over = [], False
    for who, times in sorted(callers.items()):
        minute, hour = busiest(times, 60), busiest(times, 3600)
        if who.startswith("anon"):
            per_min, per_hour = limits["current_per_minute"]["unauthenticated_per_ip"], limits["sustained_per_hour"]["anonymous_per_ip"]
        else:
            per_min, per_hour = limits["burst_per_minute"][plan], limits["sustained_per_hour"][plan]
        use = max(minute / per_min, hour / per_hour)
        over |= use > warn
        rows.append((who, len(times), minute, per_min, hour, per_hour, use))
    print(f"{'caller':<28}{'requests':>9}{'max/min':>9}{'limit':>7}{'max/hour':>10}{'limit':>7}{'use':>7}")
    for who, total, minute, per_min, hour, per_hour, use in rows:
        flag = "  <-- over" if use > 1 else ("  <-- near" if use > warn else "")
        print(f"{who:<28}{total:>9}{minute:>9}{per_min:>7}{hour:>10}{per_hour:>7}{use:>7.0%}{flag}")
    return over


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--plan", default="free", choices=["free", "premium", "ultimate"])
    p.add_argument("--warn", type=float, default=0.8)
    p.add_argument("logs", nargs="+")
    a = p.parse_args(argv)
    with open(os.path.join(HERE, "limits.json")) as f:
        limits = json.load(f)
    return 1 if report(load(a.logs), a.plan, a.warn, limits) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
