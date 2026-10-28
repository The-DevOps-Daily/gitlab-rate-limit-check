import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import backoff  # noqa: E402
import budget  # noqa: E402
import find_anon_calls  # noqa: E402
from mock_gitlab import MockGitLab  # noqa: E402

FIX = os.path.join(HERE, "fixtures")


def read(*parts):
    with open(os.path.join(*parts)) as f:
        return f.read()


class Scanner(unittest.TestCase):
    def findings(self, name):
        text = read(FIX, name)
        return [(n, kind) for n, kind, _, _ in find_anon_calls.scan_text(text)]

    def test_pipeline(self):
        # raw install script, API call through $CI_API_V4_URL, archive download;
        # not the job-token call, the git clone or the multi-line call with a token
        self.assertEqual(self.findings(".gitlab-ci.yml"), [(8, "raw"), (12, "api"), (20, "archive")])

    def test_dockerfile_add_is_anonymous(self):
        # ADD is case-insensitive in a Dockerfile
        self.assertEqual(self.findings("Dockerfile"), [(3, "raw"), (5, "raw")])

    def test_edge_cases(self):
        # a token on one command does not cover the next; git clone before && does not hide curl;
        # a comment that mentions CI_JOB_TOKEN is not auth; gitlab.com.example.org is another host;
        # a lowercase job-token header is auth; a YAML folded command is joined
        self.assertEqual(self.findings("edge-cases.yml"), [(4, "api"), (6, "raw"), (7, "raw"), (14, "archive")])

    def test_script_skips_basic_auth_and_comments(self):
        self.assertEqual(self.findings("install.sh"), [(3, "download")])

    def test_clean_tree_exits_zero(self):
        self.assertEqual(find_anon_calls.main([os.path.join(FIX, "clean")]), 0)

    def test_removing_the_token_is_caught(self):
        # Break the clean pipeline on purpose: drop the job-token header.
        text = read(FIX, "clean", ".gitlab-ci.yml")
        broken = text.replace('--header "JOB-TOKEN: $CI_JOB_TOKEN" ', "")
        self.assertNotEqual(text, broken)
        self.assertEqual([k for _, k, _, _ in find_anon_calls.scan_text(broken)], ["api"])


class Budget(unittest.TestCase):
    def test_sliding_windows(self):
        # 100 requests in the first 59.4 s, then 1 per minute for the rest of the hour
        burst = [i * 0.6 for i in range(100)]
        trickle = [60.0 * m for m in range(1, 60)]
        times = burst + trickle
        self.assertEqual(budget.busiest(times, 60), 100)
        self.assertEqual(budget.busiest(times, 3600), 159)

    def test_window_edge_is_exclusive(self):
        self.assertEqual(budget.busiest([0, 60], 60), 1)
        self.assertEqual(budget.busiest([0, 59.999], 60), 2)

    def test_anonymous_callers_are_flagged_per_log(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "runner-nat.jsonl")
            with open(path, "w") as f:
                for i in range(61):
                    f.write(json.dumps({"ts": i * 30, "who": "anon"}) + "\n")
            limits = json.loads(read(os.path.dirname(HERE), "limits.json"))
            self.assertTrue(budget.report(budget.load([path]), "free", 0.8, limits))


    def test_anonymous_callers_are_grouped_by_egress(self):
        # two runners behind one NAT, 40 anonymous requests each: fine apart, over together
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for runner in ("a", "b"):
                path = os.path.join(d, f"{runner}.jsonl")
                with open(path, "w") as f:
                    for i in range(40):
                        f.write(json.dumps({"ts": i * 60, "who": "anon", "egress": "nat-1"}) + "\n")
                paths.append(path)
            callers = budget.load(paths)
            self.assertEqual(list(callers), ["anon@nat-1"])
            self.assertEqual(budget.busiest(callers["anon@nat-1"], 3600), 80)

    def test_redirects_count_as_requests(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.jsonl")
            with open(path, "w") as f:
                f.write(json.dumps({"ts": 0, "who": "anon", "egress": "e", "requests": 3}) + "\n")
            self.assertEqual(len(budget.load([path])["anon@e"]), 3)


class Glcurl(unittest.TestCase):
    def test_logs_valid_json_without_the_token(self):
        with MockGitLab(ok=5, limited=0, remaining=7, reset=1) as m, tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "log.jsonl")
            env = dict(os.environ, GLCURL_LOG=log, GLCURL_EGRESS="test-nat")
            script = os.path.join(os.path.dirname(HERE), "glcurl")
            for args in (["-s", "-o", os.devnull, m.url + '?q="x"'],
                         ["-s", "-o", os.devnull, "--header", "private-token: secret-123", m.url]):
                subprocess.run([script] + args, env=env, check=True)
            rows = [json.loads(line) for line in read(log).splitlines()]
            self.assertEqual([r["who"] for r in rows][0], "anon")
            self.assertNotIn("secret-123", read(log))
            self.assertNotEqual(rows[1]["who"], "anon")
            self.assertEqual({r["status"] for r in rows}, {200})
            self.assertEqual({r["egress"] for r in rows}, {"test-nat"})

    def test_refuses_two_urls(self):
        script = os.path.join(os.path.dirname(HERE), "glcurl")
        r = subprocess.run([script, "http://127.0.0.1:1/a", "http://127.0.0.1:1/b"], capture_output=True)
        self.assertEqual(r.returncode, 2)


class Backoff(unittest.TestCase):
    def test_waits_retry_after(self):
        waits = []
        with MockGitLab(limited=2, retry_after=7) as m:
            status, _, _ = backoff.get(m.url, sleep=waits.append)
        self.assertEqual(status, 200)
        self.assertEqual(waits, [7.0, 7.0])
        self.assertEqual(m.hits, 3)

    def test_headerless_429_uses_capped_jittered_backoff(self):
        waits = []
        with MockGitLab(limited=4) as m:
            status, _, _ = backoff.get(m.url, sleep=waits.append, rng=random.Random(1), base=1, cap=4)
        self.assertEqual(status, 200)
        self.assertEqual(len(waits), 4)
        for attempt, w in enumerate(waits):
            self.assertGreaterEqual(w, 0)
            self.assertLessEqual(w, min(4, 2 ** attempt))

    def test_gives_up_after_tries(self):
        with MockGitLab(limited=10, retry_after=1) as m:
            with self.assertRaises(urllib.error.HTTPError) as e:
                backoff.get(m.url, tries=3, sleep=lambda s: None)
        self.assertEqual(e.exception.code, 429)
        self.assertEqual(m.hits, 3)

    def test_retry_after_http_date_and_garbage(self):
        now = lambda: 1_000_000.0  # noqa: E731
        self.assertEqual(backoff.retry_after_seconds("Mon, 12 Jan 1970 13:46:50 GMT", now), 10.0)
        self.assertIsNone(backoff.retry_after_seconds("soon", now))
        self.assertEqual(backoff.retry_after_seconds("-5", now), 0.0)

    def test_slows_down_near_the_limit(self):
        waits = []
        with MockGitLab(limited=0, remaining=3, reset=1000) as m:
            backoff.get(m.url, sleep=waits.append, now=lambda: 990.0)
        self.assertEqual(waits, [10.0])


if __name__ == "__main__":
    unittest.main()
