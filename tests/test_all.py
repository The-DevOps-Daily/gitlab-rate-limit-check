import http.server
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import backoff  # noqa: E402

spec = importlib.util.spec_from_file_location("predict_window", os.path.join(os.path.dirname(HERE), "predict-window.py"))
predict_window = importlib.util.module_from_spec(spec)
spec.loader.exec_module(predict_window)
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

    def test_token_is_not_sent_to_another_origin(self):
        seen = {}

        class Target(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen["token"] = self.headers.get("PRIVATE-TOKEN")
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)
        threading.Thread(target=target.serve_forever, daemon=True).start()
        other = f"http://127.0.0.1:{target.server_address[1]}/elsewhere"

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", other)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=origin.serve_forever, daemon=True).start()
        try:
            status, _, _ = backoff.get(f"http://127.0.0.1:{origin.server_address[1]}/x", token="secret")
        finally:
            target.shutdown(); origin.shutdown()
            target.server_close(); origin.server_close()
        self.assertEqual(status, 200)
        self.assertIsNone(seen["token"])

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



class Window(unittest.TestCase):
    HOUR = 1793239200  # 2026-10-29T02:00:00Z

    def test_models_disagree_after_the_hour(self):
        times = [self.HOUR - 3540 + i for i in range(60)]  # 01:01:00 to 01:01:59
        pw = predict_window
        self.assertFalse(pw.predict("fixed", times, self.HOUR - 600)[0])
        self.assertFalse(pw.predict("sliding", times, self.HOUR - 600)[0])
        self.assertTrue(pw.predict("fixed", times, self.HOUR + 30)[0])
        self.assertFalse(pw.predict("sliding", times, self.HOUR + 30)[0])
        self.assertTrue(pw.predict("approx", times, self.HOUR + 30)[0])
        # at 02:01:30 the requests up to 01:01:30 have left the last 3,600 seconds
        self.assertEqual(pw.predict("sliding", times, self.HOUR + 90)[1], 29)
        # the anchored counter started at 01:01:00 and is empty again from 02:01:00
        self.assertFalse(pw.predict("anchored", times, self.HOUR + 59)[0])
        self.assertEqual(pw.predict("anchored", times, self.HOUR + 61)[1], 0)

    def test_another_limit_is_not_scored_or_counted(self):
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as f:
            f.write("# burst-test.sh sha256=x url=https://gitlab.com/api/v4/version started=2026-10-29T02:03:28Z\n"
                    "step\tn\tutc\tstatus\tratelimit_name\tretry_after\n"
                    "C\t1\t2026-10-29T02:03:28.118Z\t401\tthrottle_unauthenticated_api\t\n"
                    "C\t2\t2026-10-29T02:03:38.309Z\t429\t\t60\n"
                    "C\t3\t2026-10-29T02:03:39.000Z\tERR28\t\t\n"
                    "D\t1\t2026-10-29T02:05:12.698Z\t429\tunauthenticated_traffic_per_ip\t3287\n")
        rows = predict_window.parse(f.name)
        os.unlink(f.name)
        self.assertEqual([(r["observed"], r["counted"]) for r in rows],
                         [(True, True), (False, False), (False, False), (True, False)])

    def test_parse_counts_only_unexempt_non_429(self):
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False) as f:
            f.write("# count-test.sh sha256=x started=2026-10-29T23:59:58Z\n"
                    "step\tn\tutc\tstatus\tratelimit_name\tretry_after\turl\n"
                    "paid\t1\t23:59:59\t200\t\t\thttps://gitlab.com/api/v4/projects/gitlab-org%2Fgitlab-runner\n"
                    "unscoped\t1\t00:00:01\t401\t\t\thttps://gitlab.com/api/v4/version\n"
                    "personal\t1\t00:00:02\t429\tx\t3598\thttps://gitlab.com/api/v4/projects/a%2Fb\n")
        rows = predict_window.parse(f.name)
        os.unlink(f.name)
        self.assertEqual([r["counted"] for r in rows], [False, True, False])
        self.assertAlmostEqual(rows[1]["t"] - rows[0]["t"], 2.0)  # crossed midnight

    def test_script_records_each_step(self):
        tmp = tempfile.mkdtemp()
        with MockGitLab(ok=3, limited=1) as m:
            import time
            now = int(time.time())
            hour = (now // 3600 + 1) * 3600
            env = dict(os.environ, URL=m.url, IP_CHECK_URL="http://127.0.0.1:9/", RESULTS=tmp,
                       IP_SECRET_FILE=os.path.join(tmp, "secret"),
                       P_OFFSET=str(now + 1 - hour), A_OFFSET=str(now + 2 - hour), B_OFFSET=str(now + 3 - hour))
            subprocess.run([os.path.join(os.path.dirname(HERE), "window-test.sh")], env=env,
                           check=True, capture_output=True)
        (name,) = [n for n in os.listdir(tmp) if n.endswith(".tsv")]
        rows = [l.split("\t") for l in read(tmp, name).splitlines() if not l.startswith(("#", "step"))]
        self.assertEqual([(r[0], r[3]) for r in rows], [("P", "200"), ("A", "200"), ("B", "200"), ("B", "429")])
        self.assertIn("ended=", read(tmp, name))
        self.assertIn("ip_id=unverified", read(tmp, name))


if __name__ == "__main__":
    unittest.main()
