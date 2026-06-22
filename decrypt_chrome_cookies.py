#!/usr/bin/env python3
"""
Extract Weibo login cookies from the local Chrome profile and save them to
cookies/weibo.json — WITHOUT launching a browser and without you re-logging-in.

How: Chrome stores cookies in an SQLite DB under ~/Library/Application Support/
Google/Chrome/<Profile>/Cookies. The values are AES-128-CBC encrypted with a key
that Chrome keeps in the macOS Keychain (item 'Chrome'). We read that key (you
approve once via the Keychain prompt), decrypt the weibo cookies, and write them
in the cookies/<platform>.json format that cookies/__init__.py reads.

Notes:
  - Reads a COPY of the DB (the live file is locked while Chrome runs). Chrome
    does NOT need to be quit.
  - The Keychain prompt is one-time; allow it.
  - Output goes to cookies/weibo.json (already gitignored).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from typing import List


# platform -> (output filename, list of Chrome domain families to load)
PLATFORMS = {
    "weibo": ("weibo.json", ["weibo.com", "weibo.cn", "sina.com.cn"]),
    "xiaohongshu": ("xiaohongshu.json", ["xiaohongshu.com"]),
}


def extract_cookies(platform: str = "weibo") -> tuple:
    """Pull a platform's cookies from Chrome via browser_cookie3.

    Returns (cookies_list, out_path). browser_cookie3 handles Chromium v10/v11
    decryption (PBKDF2 Keychain key + CBC/GCM) so we don't reimplement it.
    """
    if platform not in PLATFORMS:
        raise SystemExit(f"unknown platform {platform!r}; "
                         f"choose from {list(PLATFORMS)}")
    fname, domains = PLATFORMS[platform]
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "cookies", fname)
    try:
        import browser_cookie3 as bc3
    except ImportError:
        raise SystemExit("browser_cookie3 missing. Install:\n"
                         "  /opt/homebrew/bin/python3.11 -m pip install browser_cookie3")
    out = []
    seen = set()
    for domain in domains:
        try:
            jar = bc3.chrome(domain_name=domain)
        except Exception:
            continue
        for c in jar:
            key = (c.domain, c.name)
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": c.name, "value": c.value,
                        "domain": c.domain, "path": c.path})
    return out, out_path


def chrome_key() -> bytes:  # kept for compat; unused now
    return b""


LOGIN_MARKERS = {
    "weibo": ("SUB", "SUBP", "SCF"),
    "xiaohongshu": ("web_session", "a1", "webId"),
}


def main() -> int:
    platform = sys.argv[1] if len(sys.argv) > 1 else "weibo"
    cookies, out_path = extract_cookies(platform)
    if not cookies:
        raise SystemExit(f"no {platform} cookies extracted — "
                         "are you logged in in Chrome?")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out = {"cookies": cookies, "saved_at": __import__("time").time()}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    names = [c["name"] for c in cookies]
    markers = LOGIN_MARKERS.get(platform, ())
    present = {m: (m in names) for m in markers}
    print(f"[✓] saved {len(cookies)} {platform} cookies to {out_path}")
    print(f"    login cookies: {present}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
