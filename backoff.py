#!/usr/bin/env python3
"""A GitLab API GET that respects 429s, including the ones that come without headers.

    from backoff import get
    status, headers, body = get("https://gitlab.com/api/v4/projects/42", token="glpat-...")

On 429 it waits Retry-After when GitLab sends it, and falls back to capped
exponential backoff with full jitter when it does not (the Projects, Groups and
Users APIs return 429 with no rate-limit headers). It also slows down before
the limit: when RateLimit-Remaining drops to `low_water`, it waits for the reset.

For GitLab.com's anonymous per-IP limit, Retry-After points to the end of the
clock hour, but the block ended 60 minutes after the oldest request that
counted, so it can be too short or too long. Switch the caller to a token
rather than retrying anonymously.
"""
import email.utils
import random
import time
import urllib.error
import urllib.parse
import urllib.request


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but send the token only to the same scheme, host and port."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            old, nxt = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(new.full_url)
            if (old.scheme, old.hostname, old.port) != (nxt.scheme, nxt.hostname, nxt.port):
                new.remove_header("Private-token")
        return new


OPENER = urllib.request.build_opener(SameOriginRedirect)


def retry_after_seconds(value, now):
    """Retry-After is either delay-seconds or an HTTP date. None if missing or invalid."""
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - now())
    except (TypeError, ValueError):
        return None


def get(url, token=None, tries=6, base=1.0, cap=60.0, low_water=5, sleep=time.sleep, rng=None, now=time.time):
    rng = rng or random.Random()
    headers = {"PRIVATE-TOKEN": token} if token else {}
    for attempt in range(tries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with OPENER.open(req, timeout=30) as resp:
                h = resp.headers
                body = resp.read()
                try:
                    remaining, reset = int(h.get("RateLimit-Remaining")), float(h.get("RateLimit-Reset"))
                except (TypeError, ValueError):
                    remaining = reset = None
                if remaining is not None and remaining <= low_water:
                    sleep(max(0.0, reset - now()))
                return resp.status, dict(h), body
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == tries - 1:
                raise
            wait = retry_after_seconds(e.headers.get("Retry-After"), now)
            if wait is not None:
                sleep(wait)
            else:
                sleep(rng.uniform(0, min(cap, base * 2 ** attempt)))
    raise RuntimeError("unreachable")
