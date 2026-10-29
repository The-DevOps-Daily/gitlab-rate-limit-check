#!/usr/bin/env python3
"""What three counting models predict for window-test.sh, and which one GitLab matched.

    python3 predict-window.py --plan results/count-test-X.tsv   # before the run
    python3 predict-window.py results/count-test-X.tsv results/window-test-Y.tsv

The models, each with a limit of 60 per IP:
  fixed    counts requests since the start of the clock hour
  sliding  counts requests in the last 3,600 seconds
  approx   weights the previous clock hour by the share of it still inside the
           last 3,600 seconds, plus the current hour (a common cheap approximation)

A request counts if it got a response other than 429 and was not to an exempt
group. count-test.sh records whole seconds after each response, so those times
are uncertain by about a second; a prediction that depends on a request within
1.5 s of a window edge is marked "edge".
"""
import argparse
import datetime as dt
import re
import sys

LIMIT, HOUR = 60, 3600
EXEMPT = ("gitlab-org%2F", "inkscape%2F")
MODELS = ("fixed", "sliding", "approx")


def parse(path):
    """Requests in a count-test or window-test file as (time, counted, label, status)."""
    rows, day, last, url = [], None, None, ""
    for line in open(path):
        line = line.rstrip("\n")
        if line.startswith("#"):
            m = re.search(r"started=(\d{4}-\d\d-\d\d)", line) or re.search(r" a=(\d{4}-\d\d-\d\d)", line)
            if m and day is None:
                day = dt.date.fromisoformat(m.group(1))
            m = re.search(r"url=(\S+)", line)
            url = m.group(1) if m else url
            continue
        f = line.split("\t")
        if f[0] == "step":
            continue
        step, n, utc, status = f[0], f[1], f[2], f[3]
        target = f[6] if len(f) > 6 else url
        if "T" in utc:
            # window-test.sh: full timestamp taken just before the request
            t = dt.datetime.fromisoformat(utc.replace("Z", "+00:00")).timestamp() + 0.1
        else:
            # count-test.sh: whole second taken after the response
            t = dt.datetime.combine(day, dt.time.fromisoformat(utc), dt.timezone.utc).timestamp() + 0.5
            if last is not None and t < last - 43200:
                day += dt.timedelta(days=1)
                t += 86400
        last = t
        counted = status != "429" and not status.startswith("ERR") and not any(e in target for e in EXEMPT)
        rows.append((t, counted, f"{step}{n}", status))
    return rows


def predict(model, times, t):
    """(allowed, count or estimate, edge) for a request at t, given earlier counted times."""
    start = t - t % HOUR
    if model == "fixed":
        lo, n = start, sum(start <= x < t for x in times)
    elif model == "sliding":
        lo, n = t - HOUR, sum(t - HOUR < x < t for x in times)
    else:
        prev = sum(start - HOUR <= x < start for x in times)
        cur = sum(start <= x < t for x in times)
        lo, n = start, prev * (1 - (t - start) / HOUR) + cur
    edge = any(abs(x - lo) < 1.5 for x in times) and abs(n - LIMIT) <= 1
    return n < LIMIT, n, edge


def clock(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%H:%M:%S")


def plan(history, p_offset, a_offset, b_offset, spacing):
    times = [t for t, counted, _, _ in history if counted]
    hour = (max(t for t, *_ in history) // HOUR + 1) * HOUR
    p, a, b = hour + p_offset, hour + a_offset, hour + b_offset
    print(f"history: {len(times)} counted requests, {clock(min(times))} to {clock(max(times))}")
    print(f"P at {clock(p)}, A at {clock(a)}, burst from {clock(b)} every {spacing} s")
    for model in MODELS:
        own = list(times)
        ok_p, n, _ = predict(model, own, p)
        own += [p] if ok_p else []
        ok_a, n, _ = predict(model, own, a)
        own += [a] if ok_a else []
        burst, t = 0, b
        while burst < 80:
            ok, n, _ = predict(model, own, t)
            if not ok:
                break
            own.append(t)
            burst += 1
            t += spacing
        print(f"  {model:8} P {'allowed' if ok_p else '429'}, A {'allowed' if ok_a else '429'}, burst: {burst} allowed before the first 429")


def compare(history, test):
    times = [t for t, counted, _, _ in history if counted]
    hits = dict.fromkeys(MODELS, 0)
    print("request  utc       actual  " + "  ".join(f"{m:>11}" for m in MODELS))
    for t, counted, label, status in test:
        actual = status != "429"
        cells = []
        for model in MODELS:
            ok, n, edge = predict(model, times, t)
            hits[model] += ok == actual
            mark = "" if ok == actual else " x"
            cells.append(f"{('ok' if ok else '429') + ('?' if edge else '') + mark:>7} {n:>4.0f}"[-11:])
        print(f"{label:8} {clock(t)}  {status:>6}  " + "  ".join(cells))
        if counted:
            times.append(t)
    print("matched: " + ", ".join(f"{m} {hits[m]}/{len(test)}" for m in MODELS))
    print("(n = requests counted in that model's window; ? = depends on a request near the window edge; x = wrong)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--plan", action="store_true", help="predict a window-test.sh run from the history")
    p.add_argument("--p-offset", type=float, default=-600)
    p.add_argument("--a-offset", type=float, default=30)
    p.add_argument("--b-offset", type=float, default=105)
    p.add_argument("--spacing", type=float, default=0.3)
    p.add_argument("history")
    p.add_argument("test", nargs="?")
    args = p.parse_args()
    if args.plan:
        plan(parse(args.history), args.p_offset, args.a_offset, args.b_offset, args.spacing)
    elif args.test:
        compare(parse(args.history), parse(args.test))
    else:
        sys.exit("give a window-test file, or --plan")
