#!/usr/bin/env python3
"""What four counting models predict for the anonymous hourly limit, and which one GitLab matched.

    python3 predict-window.py --plan results/count-test-X.tsv                  # window-test.sh
    python3 predict-window.py --series 03:02:00 --interval 1.9 HISTORY...       # series-test.sh
    python3 predict-window.py --at 04:00:30,04:01:50 HISTORY...                  # reset-test.sh
    python3 predict-window.py --compare results/window-test-Y.tsv results/count-test-X.tsv

The models, each with a limit of 60 per IP:
  fixed     counts requests since the start of the clock hour
  sliding   counts requests in the last 3,600 seconds
  approx    weights the previous clock hour by the share of it still inside the
            last 3,600 seconds, plus the current hour (a common cheap approximation)
  anchored  starts a counter at the first request and resets it 3,600 seconds later
            (a Redis INCR with an EXPIRE set on the first increment)

A request counts if it got a response other than 429 and was not to an exempt
group. A 429 from another limit (no RateLimit-Name unauthenticated_traffic_per_ip)
and a transport error say nothing about the hourly limit, so they are not scored.
count-test.sh records whole seconds after each response; the latency to GitLab
was not recorded, so a prediction that depends on a request within 1.5 s of a
window edge is marked "edge".
"""
import argparse
import datetime as dt
import re
import sys

LIMIT, HOUR = 60, 3600
EXEMPT = ("gitlab-org%2F", "inkscape%2F")
HOURLY = "unauthenticated_traffic_per_ip"
MODELS = ("fixed", "sliding", "approx", "anchored")


def parse(path):
    """Requests in a count-test, window-test, burst-test or series-test file."""
    rows, started, url = [], None, ""
    for line in open(path):
        line = line.rstrip("\n")
        if line.startswith("#"):
            m = re.search(r"(?:started|a)=(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)", line)
            if m and started is None:
                started = dt.datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            m = re.search(r"url=(\S+)", line)
            url = m.group(1) if m else url
            continue
        f = line.split("\t")
        if f[0] in ("step", "n"):
            continue
        step, n, utc, status = f[0], f[1], f[2], f[3]
        name = f[4] if len(f) > 4 else ""
        target = f[6] if len(f) > 6 and f[6].startswith("http") else url
        if "T" in utc:
            # taken just before the request
            t = dt.datetime.fromisoformat(utc.replace("Z", "+00:00")).timestamp() + 0.1
        else:
            # count-test.sh: whole second taken after the response
            t = dt.datetime.combine(started.date(), dt.time.fromisoformat(utc), dt.timezone.utc).timestamp() + 0.5
            if t < started.timestamp() - 1:
                t += 86400
        observed = not status.startswith("ERR") and (status != "429" or name == HOURLY)
        counted = observed and status != "429" and not any(e in target for e in EXEMPT)
        rows.append({"t": t, "counted": counted, "observed": observed, "label": f"{step}{n}", "status": status})
    return rows


def predict(model, times, t):
    """(allowed, count or estimate, edge) for a request at t, given earlier counted times."""
    start = t - t % HOUR
    if model == "fixed":
        lo, n = start, sum(start <= x < t for x in times)
    elif model == "sliding":
        lo, n = t - HOUR, sum(t - HOUR < x < t for x in times)
    elif model == "approx":
        prev = sum(start - HOUR <= x < start for x in times)
        cur = sum(start <= x < t for x in times)
        lo, n = start, prev * (1 - (t - start) / HOUR) + cur
    else:
        first, n = None, 0
        for x in sorted(x for x in times if x < t):
            if first is None or x >= first + HOUR:
                first, n = x, 0
            n += 1
        if first is None or t >= first + HOUR:
            n = 0
        edge = first is not None and abs(t - first - HOUR) < 1.5
        return n < LIMIT, n, edge
    edge = any(abs(x - lo) < 1.5 for x in times) and abs(n - LIMIT) <= 1
    return n < LIMIT, n, edge


def clock(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M:%S")


def counted_times(history):
    return [r["t"] for r in history if r["counted"]]


def run(model, times, t, spacing, limit=80):
    """How many requests a model allows back to back from t, before its first 429."""
    own, allowed = list(times), 0
    while allowed < limit:
        if not predict(model, own, t)[0]:
            break
        own.append(t)
        allowed += 1
        t += spacing
    return allowed, own


def plan(history, p_offset, a_offset, b_offset, spacing):
    times = counted_times(history)
    hour = (max(r["t"] for r in history) // HOUR + 1) * HOUR
    p, a, b = hour + p_offset, hour + a_offset, hour + b_offset
    print(f"history: {len(times)} counted requests, {clock(min(times))} to {clock(max(times))}")
    print(f"P at {clock(p)}, A at {clock(a)}, burst from {clock(b)} every {spacing} s")
    for model in MODELS:
        own = list(times)
        ok_p = predict(model, own, p)[0]
        own += [p] if ok_p else []
        ok_a = predict(model, own, a)[0]
        own += [a] if ok_a else []
        burst, _ = run(model, own, b, spacing)
        print(f"  {model:8} P {'allowed' if ok_p else '429'}, A {'allowed' if ok_a else '429'}, burst: {burst} allowed before the first 429")


def series(history, at, spacing):
    times = counted_times(history)
    day = dt.datetime.fromtimestamp(max(r["t"] for r in history), dt.timezone.utc).date()
    t = dt.datetime.combine(day, dt.time.fromisoformat(at), dt.timezone.utc).timestamp()
    print(f"history: {len(times)} counted requests, {clock(min(times))} to {clock(max(times))}")
    print(f"series from {clock(t)} every {spacing} s")
    for model in MODELS:
        allowed, _ = run(model, times, t, spacing)
        print(f"  {model:8} {allowed} allowed before the first 429")


def at(history, times_of_day, burst_at, spacing):
    times = counted_times(history)
    day = dt.datetime.fromtimestamp(max(r["t"] for r in history), dt.timezone.utc).date()
    ts = [dt.datetime.combine(day, dt.time.fromisoformat(x), dt.timezone.utc).timestamp() for x in times_of_day.split(",")]
    print(f"history: {len(times)} counted requests, {clock(min(times))} to {clock(max(times))}")
    for model in MODELS:
        own, cells = list(times), []
        for t in ts:
            ok, _, edge = predict(model, own, t)
            own += [t] if ok else []
            cells.append(f"{clock(t)} {'allowed' if ok else '429'}{'?' if edge else ''}")
        if burst_at:
            b = dt.datetime.combine(day, dt.time.fromisoformat(burst_at), dt.timezone.utc).timestamp()
            cells.append(f"burst from {clock(b)} every {spacing} s: {run(model, own, b, spacing)[0]} allowed")
        print(f"  {model:8} " + ", ".join(cells))


def compare(history, test):
    times = counted_times(history)
    hits, scored = dict.fromkeys(MODELS, 0), 0
    print("request  utc       actual  " + "  ".join(f"{m:>11}" for m in MODELS))
    for r in test:
        cells = []
        for model in MODELS:
            ok, n, edge = predict(model, times, r["t"])
            mark = "" if not r["observed"] or ok == (r["status"] != "429") else " x"
            hits[model] += r["observed"] and ok == (r["status"] != "429")
            cells.append(f"{('ok' if ok else '429') + ('?' if edge else '') + mark:>7} {n:>4.0f}"[-11:])
        scored += r["observed"]
        note = "" if r["observed"] else "  (another limit: not scored)"
        print(f"{r['label']:8} {clock(r['t'])}  {r['status']:>6}  " + "  ".join(cells) + note)
        if r["counted"]:
            times.append(r["t"])
    print("matched: " + ", ".join(f"{m} {hits[m]}/{scored}" for m in MODELS))
    print("(n = requests counted in that model's window; ? = depends on a request near the window edge; x = wrong)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="predict a window-test.sh run")
    mode.add_argument("--series", metavar="HH:MM:SS", help="predict a series-test.sh run starting then")
    mode.add_argument("--at", metavar="HH:MM:SS,...", help="predict single requests at these times")
    mode.add_argument("--compare", metavar="TEST", help="score each request of a finished run")
    ap.add_argument("--burst-at", metavar="HH:MM:SS", help="with --at: then requests back to back from this time")
    ap.add_argument("--p-offset", type=float, default=-600)
    ap.add_argument("--a-offset", type=float, default=30)
    ap.add_argument("--b-offset", type=float, default=105)
    ap.add_argument("--spacing", "--interval", type=float, default=0.3)
    ap.add_argument("history", nargs="+")
    args = ap.parse_args()
    history = sorted((r for path in args.history for r in parse(path)), key=lambda r: r["t"])
    if args.plan:
        plan(history, args.p_offset, args.a_offset, args.b_offset, args.spacing)
    elif args.series:
        series(history, args.series, args.spacing)
    elif args.at:
        at(history, args.at, args.burst_at, args.spacing)
    else:
        compare(history, parse(args.compare))
    sys.exit(0)
