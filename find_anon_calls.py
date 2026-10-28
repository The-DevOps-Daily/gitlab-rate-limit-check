#!/usr/bin/env python3
"""Find GitLab.com HTTP calls that go out without credentials.

Usage: find_anon_calls.py [path ...]   (default: current directory)

Scans CI files, shell scripts, Dockerfiles and Makefiles for curl, wget,
Invoke-WebRequest, Dockerfile ADD and git commands that reach gitlab.com
(or $CI_SERVER_URL / $CI_API_V4_URL) with no token. Each one counts against
the anonymous limit of the IP it runs from. Exits 1 when it finds any.
"""
import os
import re
import sys

SCAN_NAMES = re.compile(r"(\.gitlab-ci\.ya?ml|\.ya?ml|\.sh|\.bash|\.ps1|\.mk|^Dockerfile.*|^Makefile)$")
SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "venv"}

GITLAB_URL = re.compile(
    r"(https?://gitlab\.com(?=[/:?#\s\"'`)]|$)[^\s\"'`)]*"
    r"|\$\{?CI_(?:SERVER_URL|API_V4_URL)\}?[^\s\"'`)]*)"
)
TOOLS = re.compile(r"(^|[\s(])(curl|wget|iwr|Invoke-WebRequest|Invoke-RestMethod|git)(\s)", re.I)
DOCKER_ADD = re.compile(r"^ADD\s", re.I)
AUTH = re.compile(
    r"PRIVATE-TOKEN|JOB-TOKEN|Authorization:|CI_JOB_TOKEN|gitlab-ci-token|oauth2:"
    r"|https?://[^/\s]+@gitlab\.com|--user[\s=]|\s-u\s|--netrc|\s-n\s",
    re.I,
)
FOLDED = re.compile(r"^(\s*)(?:-\s+|[\w.-]+:\s+)?>[-+]?\s*$")

ADVICE = {
    "api": "add a token header: JOB-TOKEN: $CI_JOB_TOKEN in CI (packages, releases, raw files, "
           "artifacts, archives) or a project/group access token for other endpoints",
    "raw": "use GET /api/v4/projects/:id/repository/files/:path/raw?ref=:ref with JOB-TOKEN or PRIVATE-TOKEN",
    "archive": "use GET /api/v4/projects/:id/repository/archive with JOB-TOKEN or PRIVATE-TOKEN",
    "download": "download through the API (releases, job artifacts, packages) with a token",
    "web": "fetch through the API with a token, or cache the file in your own registry",
}


def category(url):
    if "/api/v4" in url or "CI_API_V4_URL" in url:
        return "api"
    if "/-/raw/" in url:
        return "raw"
    if "/-/archive/" in url:
        return "archive"
    if "/-/releases/" in url or "/-/jobs/" in url or "/-/package_files/" in url:
        return "download"
    return "web"


def logical_lines(text):
    """Join backslash and PowerShell backtick continuations and YAML folded
    scalars (`- >`) into one line each; keep the first line number."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        start, line = i + 1, lines[i]
        folded = FOLDED.match(line)
        if folded:
            # The folded block is every following line indented deeper than its marker.
            indent, parts = len(folded.group(1)), []
            i += 1
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                parts.append(lines[i].strip())
                i += 1
            yield start + 1, " ".join(p for p in parts if p)
            continue
        buf = []
        while line.rstrip().endswith(("\\", "`")) and i + 1 < len(lines):
            buf.append(line.rstrip()[:-1])
            i += 1
            line = lines[i]
        buf.append(line.rstrip())
        yield start, " ".join(buf)
        i += 1


def split_commands(line):
    """Drop a trailing # comment and split on ; && || | outside quotes."""
    out, buf, quote, j = [], [], None, 0
    while j < len(line):
        c = line[j]
        if quote:
            if c == quote:
                quote = None
            buf.append(c)
        elif c in "'\"":
            quote = c
            buf.append(c)
        elif c == "#" and (j == 0 or line[j - 1].isspace()):
            break
        elif line.startswith(("&&", "||"), j):
            out.append("".join(buf))
            buf = []
            j += 1
        elif c in ";|":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        j += 1
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def scan_text(text):
    """Return (line, kind, url, advice) for each anonymous GitLab call in one file's text."""
    found = []
    for n, line in logical_lines(text):
        for cmd in split_commands(line.strip().lstrip("-").strip()):
            tool = TOOLS.search(" " + cmd)
            if not (tool or DOCKER_ADD.match(cmd)) or AUTH.search(cmd):
                continue
            if tool and tool.group(2).lower() == "git":
                # Git over HTTPS is not counted against the anonymous 60/hour limit.
                continue
            for url in dict.fromkeys(GITLAB_URL.findall(cmd)):
                kind = category(url)
                found.append((n, kind, url, ADVICE[kind]))
    return found


def walk(paths):
    for root in paths:
        if os.path.isfile(root):
            yield root
            continue
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                if SCAN_NAMES.search(f):
                    yield os.path.join(d, f)


def main(argv):
    hits = 0
    for path in walk(argv or ["."]):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for n, kind, url, advice in scan_text(text):
            hits += 1
            print(f"{path}:{n}: [{kind}] anonymous call to {url}\n    fix: {advice}")
    print(f"{hits} anonymous GitLab call(s) found")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
