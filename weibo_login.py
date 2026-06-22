#!/usr/bin/env python3
"""
Open a browser, let you log in to Weibo, then save the login cookies to
cookies/weibo.json in the format weibo.py expects.

Run it:
    python3 weibo_login.py

A Chromium window opens at https://passport.weibo.cn/sso/signin . Log in.
When the address bar lands on a weibo.cn home/profile page (logged in),
press Enter in this terminal — the script dumps cookies and exits.
"""

from __future__ import annotations

import json
import os
import time

from playwright.sync_api import sync_playwright

COOKIE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "cookies", "weibo.json")
LOGIN_URL = "https://passport.weibo.cn/sso/signin?entry=mweibo&url=https%3A%2F%2Fm.weibo.cn"
DONE_HINTS = ("m.weibo.cn", "weibo.cn", "weibo.com")
# keep only cookies relevant to weibo (drop third-party trackers)
KEEP_DOMAIN_ANY = ("weibo.cn", "weibo.com")


def main() -> int:
    os.makedirs(os.path.dirname(COOKIE_PATH), exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                        "Version/16.6 Mobile/15E148 Safari/604.1"),
            viewport={"width": 420, "height": 800},
            is_mobile=True,
            locale="zh-CN",
        )
        page = context.new_page()
        print("[*] opening Weibo login page…")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("[*] log in in the browser window.")
        print("[*] once you are on a weibo home/profile page, come back here "
              "and press Enter to save cookies.")
        input()

        cookies = context.cookies()
        kept = [c for c in cookies
                if any(d in c.get("domain", "") for d in KEEP_DOMAIN_ANY)]
        if not kept:
            print("[!] no weibo cookies captured — are you logged in? exiting.")
            browser.close()
            return 1

        out = {"cookies": [
            {"name": c["name"], "value": c["value"],
             "domain": c["domain"], "path": c.get("path", "/")}
            for c in kept
        ], "saved_at": int(time.time())}
        with open(COOKIE_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        names = [c["name"] for c in out["cookies"]]
        print(f"[✓] saved {len(out['cookies'])} weibo cookies to {COOKIE_PATH}")
        print(f"    includes login cookies: "
              f"SUB={'SUB' in names} SUBP={'SUBP' in names} SCF={'SCF' in names}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
