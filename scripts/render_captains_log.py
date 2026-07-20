#!/usr/bin/env python3
"""Render synced captain's-log entries as one collapsible HTML/markdown blob.

Run by Home Assistant's `command_line` sensor (see configuration.yaml). Reads
/share/captains_log/YYYY-MM-DD.md (synced there by the nightly Captain's Log
Routine after it commits to the captains-log branch - see CLAUDE.md) and
emits JSON on stdout: {"count": N, "content": "<details>...</details>..."}.

The dashboard's markdown card renders `content` directly - <details>/<summary>
give native click-to-expand behavior with no custom Lovelace cards.
"""
import glob
import json
import os

LOG_DIR = os.environ.get("CAPTAINS_LOG_DIR", "/share/captains_log")


def main():
    files = sorted(glob.glob(os.path.join(LOG_DIR, "*.md")), reverse=True)
    blocks = []
    for path in files:
        date = os.path.basename(path)[:-3]
        with open(path, "r") as f:
            body = f.read().strip()
        blocks.append(f"<details>\n<summary><b>{date}</b></summary>\n\n{body}\n\n</details>")
    content = "\n\n".join(blocks) if blocks else "_No entries yet._"
    print(json.dumps({"count": len(files), "content": content}))


if __name__ == "__main__":
    main()
