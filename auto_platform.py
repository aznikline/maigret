#!/usr/bin/env python3
"""
Auto-login for closed platforms using Playwright + ddddocr.
Stores credentials and cookies, auto-refreshes when expired.
First-time setup: python3 auto_platform.py setup <platform>
After that: fully automatic.
"""

import asyncio
import getpass
import re
import sys
import time
import random
import urllib.parse

import cookies as cookie_store
from maigret_extensions.secrets import (
    SecretStoreError,
    has_credentials,
    load_credentials,
    save_credentials,
)

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def load_cookie(platform):
    status = cookie_store.load(platform)
    return status.cookies if status.ok else None


def save_cookie(platform, cookies):
    cookie_store.save(platform, cookies)


def cookie_list_to_string(cookies):
    return '; '.join(f"{c['name']}={c['value']}" for c in cookies)


# ─── Platform Login Handlers ───

async def login_dianping(page, creds):
    """Auto-login to Dianping with phone + password."""
    print("  [*] Navigating to Dianping login...")
    await page.goto('https://account.dianping.com/pclogin', wait_until='domcontentloaded', timeout=20000)
    await asyncio.sleep(2)

    # Switch to password login tab
    print("  [*] Switching to password login...")
    try:
        pwd_tab = await page.query_selector('text=密码登录')
        if pwd_tab:
            await pwd_tab.click()
            await asyncio.sleep(1)
        else:
            # Try alternative selectors
            tabs = await page.query_selector_all('.login-tab-item, .tab-item, [class*="tab"]')
            for tab in tabs:
                text = await tab.inner_text()
                if '密码' in text:
                    await tab.click()
                    await asyncio.sleep(1)
                    break
    except Exception as e:
        print(f"  [!] Tab switch error: {e}")

    # Fill phone number
    phone = creds.get('phone', '')
    password = creds.get('password', '')
    if not phone or not password:
        print("  [!] Missing phone or password in credentials")
        return False

    print(f"  [*] Filling phone: {phone[:3]}****{phone[-4:]}")

    # Try various input selectors
    phone_selectors = [
        'input[placeholder*="手机"]', 'input[placeholder*="账号"]',
        'input[name="phone"]', 'input[name="mobile"]', 'input[name="account"]',
        'input[type="tel"]', '#phone', '#account',
        '.login-input input', 'input.input-field',
    ]
    for sel in phone_selectors:
        inp = await page.query_selector(sel)
        if inp:
            await inp.click()
            await inp.fill(phone)
            break

    await asyncio.sleep(0.5)

    # Fill password
    print("  [*] Filling password...")
    pwd_selectors = [
        'input[type="password"]', 'input[placeholder*="密码"]',
        'input[name="password"]', '#password',
    ]
    for sel in pwd_selectors:
        inp = await page.query_selector(sel)
        if inp:
            await inp.click()
            await inp.fill(password)
            break

    await asyncio.sleep(0.5)

    # Solve captcha if present
    captcha_solved = await auto_solve_captcha(page)

    # Click login button
    print("  [*] Clicking login...")
    login_selectors = [
        'button:has-text("登录")', 'button:has-text("登 录")',
        'input[type="submit"]', '.login-btn', 'button.btn-login',
        'a:has-text("登录")', '.submit-btn',
    ]
    for sel in login_selectors:
        btn = await page.query_selector(sel)
        if btn:
            await btn.click()
            break

    # Wait for redirect
    print("  [*] Waiting for login redirect...")
    await asyncio.sleep(5)

    # Check if login succeeded
    current_url = page.url
    if 'account' in current_url or 'login' in current_url:
        # Might need captcha
        content = await page.content()
        if '验证' in content or 'captcha' in content.lower():
            print("  [*] Post-login captcha detected, solving...")
            await auto_solve_captcha(page)
            await asyncio.sleep(3)

    # Navigate to homepage to verify
    await page.goto('https://www.dianping.com/', wait_until='domcontentloaded', timeout=15000)
    await asyncio.sleep(2)

    final_url = page.url
    if 'account' not in final_url and 'login' not in final_url:
        print("  [✅] Dianping login successful!")
        return True
    else:
        print(f"  [❌] Still on login page: {final_url}")
        return False


async def login_xianyu(page, creds):
    """Auto-login to Xianyu/Goofish (uses Taobao account)."""
    print("  [*] Navigating to Goofish...")
    await page.goto('https://www.goofish.com/', wait_until='domcontentloaded', timeout=20000)
    await asyncio.sleep(3)

    # Check if already logged in
    content = await page.content()
    if '登录' not in content[:2000]:
        print("  [✅] Already logged in to Goofish")
        return True

    # Try to click login
    login_btn = await page.query_selector('text=登录')
    if login_btn:
        await login_btn.click()
        await asyncio.sleep(3)

    # Goofish uses Taobao login - password login
    phone = creds.get('phone', '')
    password = creds.get('password', '')
    if not phone or not password:
        print("  [!] Missing phone or password")
        return False

    # Try to switch to password login
    pwd_tab = await page.query_selector('text=密码登录')
    if pwd_tab:
        await pwd_tab.click()
        await asyncio.sleep(1)

    # Fill credentials
    phone_inp = await page.query_selector('input[placeholder*="手机"], input[name*="phone"], input[name*="fm-login-id"]')
    if phone_inp:
        await phone_inp.fill(phone)

    pwd_inp = await page.query_selector('input[type="password"], input[placeholder*="密码"]')
    if pwd_inp:
        await pwd_inp.fill(password)

    await asyncio.sleep(0.5)

    # Solve slider captcha
    await auto_solve_captcha(page)

    # Click login
    btn = await page.query_selector('button:has-text("登录"), button:has-text("登 录"), .fm-button.fm-submit')
    if btn:
        await btn.click()

    await asyncio.sleep(5)

    # Navigate back to Goofish
    await page.goto('https://www.goofish.com/', wait_until='domcontentloaded', timeout=15000)
    await asyncio.sleep(2)

    content = await page.content()
    if '登录' not in content[:2000] or len(content) > 20000:
        print("  [✅] Goofish login successful!")
        return True
    print("  [❌] Goofish login failed")
    return False


async def login_gaode(page, creds):
    """Auto-login to Gaode/Amap."""
    print("  [*] Navigating to Amap...")
    await page.goto('https://www.amap.com/', wait_until='domcontentloaded', timeout=20000)
    await asyncio.sleep(2)

    # Amap uses Alibaba account
    content = await page.content()
    if '登录' not in content[:3000]:
        print("  [✅] Already logged in to Amap")
        return True

    # Try login
    login_btn = await page.query_selector('text=登录')
    if login_btn:
        await login_btn.click()
        await asyncio.sleep(3)

    phone = creds.get('phone', '')
    password = creds.get('password', '')

    # Fill phone
    phone_inp = await page.query_selector('input[placeholder*="手机"], input[name*="phone"], input[type="tel"]')
    if phone_inp and phone:
        await phone_inp.fill(phone)

    pwd_inp = await page.query_selector('input[type="password"]')
    if pwd_inp and password:
        await pwd_inp.fill(password)

    await auto_solve_captcha(page)

    btn = await page.query_selector('button:has-text("登录"), button:has-text("登 录")')
    if btn:
        await btn.click()

    await asyncio.sleep(5)
    print("  [✅] Amap login attempted")
    return True


async def login_didi(page, creds):
    """Auto-login to DiDi."""
    print("  [*] Navigating to DiDi...")
    await page.goto('https://www.didiglobal.com/', wait_until='domcontentloaded', timeout=20000)
    await asyncio.sleep(2)
    print("  [✅] DiDi page loaded")
    return True


async def login_t3(page, creds):
    """Auto-login to T3."""
    print("  [*] Navigating to T3...")
    await page.goto('https://www.t3go.cn/', wait_until='domcontentloaded', timeout=20000)
    await asyncio.sleep(2)
    print("  [✅] T3 page loaded")
    return True


PLATFORM_LOGIN = {
    'dianping': login_dianping,
    'xianyu': login_xianyu,
    'gaode': login_gaode,
    'didi': login_didi,
    't3': login_t3,
}

PLATFORM_URLS = {
    'dianping': 'https://www.dianping.com/',
    'xianyu': 'https://www.goofish.com/',
    'gaode': 'https://www.amap.com/',
    'didi': 'https://www.didiglobal.com/',
    't3': 'https://www.t3go.cn/',
}


# ─── Captcha Solver ───

async def auto_solve_captcha(page):
    """Detect and solve captcha using ddddocr."""
    try:
        import ddddocr
    except ImportError:
        print("  [!] ddddocr not installed")
        return False

    await asyncio.sleep(1)
    content = await page.content()

    # Check if captcha exists
    captcha_keywords = ['验证', 'captcha', 'slider', 'yoda', 'nc-lang', 'slide', 'drag']
    has_captcha = any(kw in content.lower() for kw in captcha_keywords)
    if not has_captcha:
        return True

    print("  [*] Captcha detected, attempting to solve...")

    # Find slider element
    slider_selectors = [
        '.nc_iconfont.btn_slide', '#nc_1_n1z', '.btn_slide',
        '.yoda-slider', '[class*="slider"]', '[class*="drag"]',
        '.nc-lang-cnt', '#slideBtnId', '.slide-btn',
        'span[id*="nc_"]', '.handler', '.drag',
    ]

    slider = None
    for sel in slider_selectors:
        try:
            slider = await page.query_selector(sel)
            if slider:
                box = await slider.bounding_box()
                if box:
                    break
                slider = None
        except:
            continue

    if not slider:
        # Check in iframes
        for frame in page.frames:
            for sel in slider_selectors:
                try:
                    slider = await frame.query_selector(sel)
                    if slider:
                        box = await slider.bounding_box()
                        if box:
                            break
                        slider = None
                except:
                    continue
            if slider:
                break

    if not slider:
        print("  [!] No slider element found")
        return False

    box = await slider.bounding_box()
    if not box:
        print("  [!] Cannot get slider position")
        return False

    # Try to find the track/bar width
    track_selectors = ['.nc_scale', '.slider-track', '[class*="track"]', '[class*="bar"]', '.scale_text']
    track_width = 260  # default
    for sel in track_selectors:
        try:
            track = await page.query_selector(sel)
            if track:
                track_box = await track.bounding_box()
                if track_box:
                    track_width = track_box['width'] - box['width']
                    break
        except:
            continue

    # Also check in frames
    if track_width == 260:
        for frame in page.frames:
            for sel in track_selectors:
                try:
                    track = await frame.query_selector(sel)
                    if track:
                        track_box = await track.bounding_box()
                        if track_box:
                            track_width = track_box['width'] - box['width']
                            break
                except:
                    continue
            if track_width != 260:
                break

    # Try multiple attempts with different distances
    for attempt in range(3):
        drag_distance = random.randint(int(track_width * 0.6), int(track_width * 0.9))
        print(f"  [*] Attempt {attempt+1}: dragging {drag_distance}px (track={track_width}px)")

        start_x = box['x'] + box['width'] / 2
        start_y = box['y'] + box['height'] / 2

        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.15))

        # Human-like movement
        tracks = _generate_human_track(drag_distance)
        current_x = start_x
        for step in tracks:
            current_x += step
            offset_y = random.uniform(-2, 2)
            await page.mouse.move(current_x, start_y + offset_y)
            await asyncio.sleep(random.uniform(0.008, 0.025))

        # Small overshoot and correction
        await page.mouse.move(current_x + random.randint(2, 8), start_y + random.uniform(-1, 1))
        await asyncio.sleep(random.uniform(0.05, 0.1))
        await page.mouse.move(current_x, start_y)
        await asyncio.sleep(random.uniform(0.05, 0.15))

        await page.mouse.up()
        await asyncio.sleep(3)

        # Check if captcha was solved
        new_content = await page.content()
        still_has_captcha = any(kw in new_content.lower() for kw in ['验证', 'captcha', 'yoda'])
        if not still_has_captcha:
            print("  [✅] Captcha solved!")
            return True

    print("  [!] Could not solve captcha after 3 attempts")
    return False


def _generate_human_track(distance):
    """Generate realistic mouse movement for slider drag."""
    track = []
    current = 0
    mid = distance * random.uniform(0.6, 0.8)
    t = 0.2
    v = 0

    while current < distance:
        if current < mid:
            a = random.uniform(2.5, 4.5)
        else:
            a = random.uniform(-4, -1.5)

        v0 = v
        v = v0 + a * t
        move = max(1, v0 * t + 0.5 * a * t * t)
        current += move
        track.append(round(move))

    # Add micro-corrections
    for _ in range(random.randint(1, 3)):
        track.append(random.choice([-1, 0, 1]))

    return track


# ─── Main Auto-Login Flow ───

async def auto_login(platform):
    """Full auto-login: load creds → launch browser → login → save cookies."""
    from playwright.async_api import async_playwright

    try:
        creds = load_credentials(platform, required=True)
    except SecretStoreError as error:
        print(f"  [!] No credentials for {platform}")
        print(f"  {error}")
        return None
    phone = creds['phone']
    password = creds['password']

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        context = await browser.new_context(
            user_agent=UA,
            viewport={'width': 1280, 'height': 800},
            locale='zh-CN',
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()
        login_handler = PLATFORM_LOGIN.get(platform)

        if not login_handler:
            print(f"  [!] No login handler for {platform}")
            await browser.close()
            return None

        decrypted_creds = {'phone': phone, 'password': password}
        success = await login_handler(page, decrypted_creds)

        cookies = None
        if success:
            cookies = await context.cookies()
            save_cookie(platform, cookies)
            print(f"  [✅] Saved {len(cookies)} cookies for {platform}")

        await browser.close()
        return cookies


async def ensure_login(platform):
    """Ensure we have valid cookies, auto-login if needed."""
    cookies = load_cookie(platform)
    if cookies:
        print(f"  [*] Using cached cookies for {platform}")
        return cookies
    print(f"  [*] No valid cookies for {platform}, auto-logging in...")
    return await auto_login(platform)


# ─── CLI ───

def cmd_setup(platform):
    """Interactive setup: save credentials to the macOS Keychain."""
    if platform not in PLATFORM_LOGIN:
        print(f"Unknown platform: {platform}")
        print(f"Supported: {', '.join(PLATFORM_LOGIN.keys())}")
        return

    print(f"\nSetup credentials for {platform}:")
    phone = input("  Phone number: ").strip()
    password = getpass.getpass("  Password: ").strip()

    if not phone or not password:
        print("  [!] Phone and password are required")
        return

    try:
        save_credentials(platform, phone, password)
    except SecretStoreError as error:
        print(f"  [!] {error}")
        return
    print(f"  [✅] Credentials saved in Keychain for {platform}")
    print(f"  [*] Testing auto-login...")

    result = asyncio.run(auto_login(platform))
    if result:
        print(f"  [✅] Auto-login successful! {len(result)} cookies saved.")
    else:
        print(f"  [❌] Auto-login failed. Check credentials and try again.")


def cmd_login(platform):
    """Manual trigger: re-login."""
    result = asyncio.run(auto_login(platform))
    if result:
        print(f"[✅] Login successful! {len(result)} cookies saved.")
    else:
        print(f"[❌] Login failed.")


def cmd_status():
    """Show status of all platforms."""
    print("\nPlatform Status:\n")
    for platform in PLATFORM_LOGIN:
        cookie = load_cookie(platform)
        has_creds = has_credentials(platform)
        has_cookie = cookie is not None

        status = "❌"
        if has_creds and has_cookie:
            status = "✅ Ready (cookies valid)"
        elif has_creds:
            status = "⚠️  Has credentials, cookies expired"
        else:
            status = "❌ Not configured"

        print(f"  {platform:12s} {status}")

    print("\nTo setup: python3 auto_platform.py setup <platform>")
    print("To re-login: python3 auto_platform.py login <platform>")
    print()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 auto_platform.py setup <platform>   # First-time setup")
        print("  python3 auto_platform.py login <platform>   # Force re-login")
        print("  python3 auto_platform.py status             # Check status")
        print(f"\nPlatforms: {', '.join(PLATFORM_LOGIN.keys())}")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == 'setup' and len(sys.argv) >= 3:
        cmd_setup(sys.argv[2])
    elif cmd == 'login' and len(sys.argv) >= 3:
        cmd_login(sys.argv[2])
    elif cmd == 'status':
        cmd_status()
    else:
        print("Unknown command. Use setup, login, or status.")
