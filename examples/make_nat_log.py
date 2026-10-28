#!/usr/bin/env python3
"""Write a synthetic request log for a CI fleet behind one NAT address.

Not a measurement: a worked example for budget.py. Twelve parallel jobs per
pipeline, a pipeline every ten minutes for one hour. Each job fetches an
install script and makes two API calls without a token, so all of them share
the runner fleet's single egress IP. A release bot with a token adds a burst.
"""
import json
import random

rng = random.Random(42)
rows = []
for pipeline in range(6):
    start = pipeline * 600
    for job in range(12):
        t = start + rng.uniform(5, 40)
        for _ in range(3):
            rows.append({"ts": round(t, 3), "who": "anon", "egress": "runners-nat"})
            t += rng.uniform(0.5, 3)
# release bot (token): 90 calls in about 45 seconds, once
t = 1800.0
for _ in range(90):
    rows.append({"ts": round(t, 3), "who": "release-bot", "egress": "runners-nat"})
    t += 0.5
rows.sort(key=lambda r: r["ts"])
with open("examples/runner-nat.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
print(f"wrote {len(rows)} requests to examples/runner-nat.jsonl")
