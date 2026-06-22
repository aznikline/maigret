#!/usr/bin/env python3
"""
Cookie helper for closed platforms.
Usage:
  python3 cookie_helper.py export <platform>   # Open browser, login, export cookies
  python3 cookie_helper.py import <platform> <cookie_string>   # Save cookie string
  python3 cookie_helper.py status              # Show saved cookies
  python3 cookie_helper.py test <platform>     # Test if cookies work

Supported platforms: dianping, xianyu, gaode, didi, t3
"""

import sys
import os
import json
import ssl
import urllib.request

COOKIE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies')

PLATFORMS = {
    'dianping': {
        'name': '大众点评',
        'login_url': 'https://www.dianping.com/',
        'cookie_file': 'dianping.txt',
        'test_url': 'https://www.dianping.com/search/keyword/1/0_test',
        'success_check': lambda body: len(body) > 10000 and '验证' not in body and 'account' not in body[:500],
    },
    'xianyu': {
        'name': '闲鱼',
        'login_url': 'https://www.goofish.com/',
        'cookie_file': 'xianyu.txt',
        'test_url': 'https://www.goofish.com/search?keyword=test',
        'success_check': lambda body: len(body) > 5000,
    },
    'gaode': {
        'name': '高德地图',
        'login_url': 'https://www.amap.com/',
        'cookie_file': 'gaode.txt',
        'test_url': 'https://www.amap.com/search?query=test',
        'success_check': lambda body: len(body) > 5000,
    },
    'didi': {
        'name': '滴滴出行',
        'login_url': 'https://www.didiglobal.com/',
        'cookie_file': 'didi.txt',
        'test_url': 'https://www.didiglobal.com/',
        'success_check': lambda body: len(body) > 5000,
    },
    't3': {
        'name': 'T3出行',
        'login_url': 'https://www.t3go.cn/',
        'cookie_file': 't3.txt',
        'test_url': 'https://www.t3go.cn/',
        'success_check': lambda body: len(body) > 5000,
    },
}

os.makedirs(COOKIE_DIR, exist_ok=True)


def get_cookie_path(platform):
    return os.path.join(COOKIE_DIR, PLATFORMS[platform]['cookie_file'])


def cmd_status():
    print("Saved cookies:\n")
    for key, info in PLATFORMS.items():
        path = get_cookie_path(key)
        if os.path.exists(path):
            with open(path) as f:
                cookie = f.read().strip()
            status = f"✅ Saved ({len(cookie)} chars)" if cookie else "❌ Empty"
        else:
            status = "❌ Not saved"
        print(f"  {info['name']:10s} ({key:10s}): {status}")
        print(f"    Login URL: {info['login_url']}")
    print()
    print("To add cookies:")
    print("  1. Open the login URL in your browser")
    print("  2. Login with your account")
    print("  3. Open DevTools (F12) → Network tab")
    print("  4. Refresh the page, click any request")
    print("  5. Copy the Cookie header value")
    print("  6. Run: python3 cookie_helper.py import <platform> '<cookie_string>'")


def cmd_import(platform, cookie_str):
    if platform not in PLATFORMS:
        print(f"Unknown platform: {platform}")
        print(f"Supported: {', '.join(PLATFORMS.keys())}")
        return
    path = get_cookie_path(platform)
    with open(path, 'w') as f:
        f.write(cookie_str)
    print(f"✅ Cookie saved for {PLATFORMS[platform]['name']}")
    print(f"   File: {path}")
    print(f"   Size: {len(cookie_str)} chars")


def cmd_export(platform):
    if platform not in PLATFORMS:
        print(f"Unknown platform: {platform}")
        return
    info = PLATFORMS[platform]
    print(f"Opening {info['name']} login page...")
    print(f"URL: {info['login_url']}")
    print()
    import webbrowser
    webbrowser.open(info['login_url'])
    print("After logging in:")
    print("  1. Press F12 → Network tab")
    print("  2. Refresh page, click any request")
    print("  3. Find 'Cookie:' in request headers")
    print("  4. Copy the full cookie value")
    print("  5. Run:")
    print(f"     python3 cookie_helper.py import {platform} '<paste_cookie_here>'")


def cmd_test(platform):
    if platform not in PLATFORMS:
        print(f"Unknown platform: {platform}")
        return

    path = get_cookie_path(platform)
    if not os.path.exists(path):
        print(f"❌ No cookie saved for {PLATFORMS[platform]['name']}")
        return

    with open(path) as f:
        cookie = f.read().strip()
    if not cookie:
        print(f"❌ Cookie is empty for {PLATFORMS[platform]['name']}")
        return

    info = PLATFORMS[platform]
    print(f"Testing {info['name']} cookie...")

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(info['test_url'], headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Cookie': cookie,
        'Referer': info['login_url'],
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10, context=ctx)
        body = resp.read().decode('utf-8', errors='ignore')
        final_url = resp.url

        if info['success_check'](body):
            print(f"✅ Cookie works! Response: {len(body)} chars")
        else:
            if 'login' in final_url or 'account' in final_url:
                print(f"❌ Cookie expired (redirected to login)")
            elif '验证' in body:
                print(f"❌ Cookie triggered captcha")
            else:
                print(f"⚠️  Unclear result (size={len(body)}, url={final_url[:60]})")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == 'status':
        cmd_status()
    elif cmd == 'import' and len(sys.argv) >= 4:
        cmd_import(sys.argv[2], sys.argv[3])
    elif cmd == 'export' and len(sys.argv) >= 3:
        cmd_export(sys.argv[2])
    elif cmd == 'test' and len(sys.argv) >= 3:
        cmd_test(sys.argv[2])
    else:
        print(__doc__)
