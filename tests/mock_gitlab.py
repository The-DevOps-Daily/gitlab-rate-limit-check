"""A local stand-in for GitLab.com's rate limiting, for the backoff tests.

Answers `ok` requests with 200, then `limited` requests with 429. With
retry_after set, each 429 carries Retry-After and RateLimit headers; without
it, the 429 has no headers at all, as the Projects API does.
"""
import http.server
import threading


class MockGitLab:
    def __init__(self, ok=0, limited=2, retry_after=None, remaining=None, reset=None):
        self.ok, self.limited, self.retry_after = ok, limited, retry_after
        self.remaining, self.reset = remaining, reset
        self.hits = 0
        mock = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                mock.hits += 1
                n = mock.hits
                if mock.ok < n <= mock.ok + mock.limited:
                    self.send_response(429)
                    if mock.retry_after is not None:
                        self.send_header("Retry-After", str(mock.retry_after))
                        self.send_header("RateLimit-Name", "throttle_mock")
                        self.send_header("RateLimit-Remaining", "0")
                else:
                    self.send_response(200)
                    if mock.remaining is not None:
                        self.send_header("RateLimit-Remaining", str(mock.remaining))
                        self.send_header("RateLimit-Reset", str(mock.reset))
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/v4/projects/1"

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
