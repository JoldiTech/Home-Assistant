#!/usr/bin/env python3
"""Render the captain's-log entries for the Home Assistant dashboard.

Reads the day-files straight from the private GitHub repo's captains-log branch
(the single source of truth the AI-box pipeline pushes to) and emits JSON on
stdout: {"count": N, "content": "<details>...</details>..."} for a Markdown
card. Run by HA's `command_line` sensor (see /config/command_line.yaml).

GitHub read token is in /config/captains_gh.token (mode 600) - a fine-grained
PAT with contents:read on the repo. Stdlib only (urllib), so it runs in HA
Core's Python with no extra packages.
"""
import json
import urllib.request

REPO = "JoldiTech/Home-Assistant"
BRANCH = "captains-log"
DIR = "captains_log"
TOKEN_FILE = "/config/captains_gh.token"
# GraphQL rather than the REST contents API: the day-files being rendered come
# back in two round trips, where REST needs one request per file — up to
# MAX_DAYS of them, enough to drift into the command_line sensor's 15 s
# command_timeout as days accumulate, and a killed process drops the sensor
# entirely instead of showing the error card. HTTP_TIMEOUT bounds each socket
# operation, not the run as a whole.
GRAPHQL_URL = "https://api.github.com/graphql"
MAX_DAYS = 60
HTTP_TIMEOUT = 6


def _token():
    with open(TOKEN_FILE) as f:
        return f.read().strip()


def _graphql(query, token):
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={
            "Authorization": f"bearer {token}",
            "User-Agent": "ha-captains-log",
            "Content-Type": "application/json",
        },
    )
    payload = json.loads(
        urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read().decode("utf-8")
    )
    # A rejected query still comes back 200, with the reason in "errors".
    if payload.get("errors"):
        raise RuntimeError(payload["errors"][0].get("message", "GraphQL error"))
    return payload.get("data") or {}


def _day_files(token, owner, name):
    data = _graphql(
        '{repository(owner:"%s",name:"%s"){object(expression:"%s:%s")'
        "{... on Tree{entries{name type}}}}}" % (owner, name, BRANCH, DIR),
        token,
    )
    entries = ((data.get("repository") or {}).get("object") or {}).get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"{DIR} is not a directory on {BRANCH}")
    return sorted(
        (e["name"] for e in entries
         if e["type"] == "blob" and e["name"].endswith(".md") and e["name"][0].isdigit()),
        reverse=True,
    )[:MAX_DAYS]


def _bodies(token, owner, name, files):
    fields = " ".join(
        "d%d: object(expression:%s){... on Blob{text}}"
        % (i, json.dumps(f"{BRANCH}:{DIR}/{fn}"))
        for i, fn in enumerate(files)
    )
    data = _graphql('{repository(owner:"%s",name:"%s"){%s}}' % (owner, name, fields), token)
    repo = data.get("repository") or {}
    return [(repo.get("d%d" % i) or {}).get("text") or "" for i in range(len(files))]


def main():
    try:
        owner, name = REPO.split("/", 1)
        token = _token()
        files = _day_files(token, owner, name)
        bodies = _bodies(token, owner, name, files) if files else []
    except Exception as e:
        print(json.dumps({"count": 0, "content": f"_Could not reach GitHub: {e}_"}))
        return

    blocks = [
        f"<details>\n<summary><b>{fn[:-3]}</b></summary>\n\n{body.strip()}\n\n</details>"
        for fn, body in zip(files, bodies) if body.strip()
    ]
    content = "\n\n".join(blocks) if blocks else "_No entries yet._"
    print(json.dumps({"count": len(files), "content": content}))


if __name__ == "__main__":
    main()
