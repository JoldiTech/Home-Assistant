#!/usr/bin/env python3
"""Render the captain's-log entries for the Home Assistant dashboard.

Reads the day-files straight from the private GitHub repo's captains-log branch
(the single source of truth the AI-box pipeline pushes to) and emits JSON on
stdout: {"count": N, "content": "<details>...</details>..."} for a Markdown
card. Run by HA's `command_line` sensor (see configuration.yaml).

GitHub read token is in /config/captains_gh.token (mode 600) - a fine-grained
PAT with contents:read on the repo. Stdlib only (urllib), so it runs in HA
Core's Python with no extra packages.
"""
import json
import sys
import urllib.request

REPO = "JoldiTech/Home-Assistant"
BRANCH = "captains-log"
DIR = "captains_log"
TOKEN_FILE = "/config/captains_gh.token"
MAX_DAYS = 60


def _token():
    with open(TOKEN_FILE) as f:
        return f.read().strip()


def _get(url, token, raw=False):
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "ha-captains-log"}
    headers["Accept"] = "application/vnd.github.raw" if raw else "application/vnd.github+json"
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=10).read().decode("utf-8")


def main():
    try:
        token = _token()
        listing = json.loads(
            _get(f"https://api.github.com/repos/{REPO}/contents/{DIR}?ref={BRANCH}", token)
        )
    except Exception as e:
        print(json.dumps({"count": 0, "content": f"_Could not reach GitHub: {e}_"}))
        return

    files = sorted(
        (f for f in listing if f["name"].endswith(".md") and f["name"][0].isdigit()),
        key=lambda f: f["name"], reverse=True,
    )[:MAX_DAYS]

    blocks = []
    for f in files:
        try:
            body = _get(f["url"], token, raw=True).strip()
        except Exception:
            continue
        date = f["name"][:-3]
        blocks.append(f"<details>\n<summary><b>{date}</b></summary>\n\n{body}\n\n</details>")

    content = "\n\n".join(blocks) if blocks else "_No entries yet._"
    print(json.dumps({"count": len(files), "content": content}))


if __name__ == "__main__":
    main()
