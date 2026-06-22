#!/usr/bin/env python3
"""
Dianping user search with captcha bypass using Playwright + ddddocr.
"""

import asyncio
import json
import re
import time
import random
import base64
import urllib.parse


async def search_dianping(username):
    """Search Dianping for a username, bypassing Yoda captcha."""
    from playwright.async_api import async_playwright

    results = []

    async with async_playwright() as p:
        # Launch stealth browser
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )

        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='zh-CN',
        )

        # Inject stealth scripts
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        # Step 1: Visit homepage first
        print("  [1] Visiting dianping.com...")
        try:
            await page.goto('https://www.dianping.com/', wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"  [1] Homepage error: {e}")

        # Step 2: Navigate to search
        print(f"  [2] Searching for: {username}")
        search_url = f'https://www.dianping.com/search/keyword/1/0_{urllib.parse.quote(username)}'
        try:
            await page.goto(search_url, wait_until='domcontentloaded', timeout=15000)
            await asyncio.sleep(3)
        except Exception as e:
            print(f"  [2] Search page error: {e}")

        # Step 3: Check if captcha appeared
        page_content = await page.content()
        has_captcha = '验证' in page_content or 'yoda' in page_content.lower() or 'verify' in page_content.lower()
        print(f"  [3] Captcha detected: {has_captcha}")

        if has_captcha:
            # Try to handle the captcha
            captcha_solved = await _handle_captcha(page)
            if captcha_solved:
                print("  [4] Captcha solved! Waiting for redirect...")
                await asyncio.sleep(3)
                # Wait for redirect back to search
                try:
                    await page.wait_for_url('**/search/**', timeout=15000)
                except:
                    pass
                page_content = await page.content()
            else:
                print("  [4] Could not solve captcha")

        # Step 4: Extract results
        print("  [5] Extracting results...")
        page_content = await page.content()
        text_content = await page.inner_text('body')

        # Count username mentions (excluding search box echo)
        mentions = text_content.lower().count(username.lower())
        print(f"  [5] Username mentions in page: {mentions}")

        if mentions > 2:
            # Try to find user profile links
            member_links = re.findall(r'/member/(\d+)', page_content)
            if member_links:
                for mid in set(member_links):
                    results.append({
                        'platform': 'Dianping',
                        'url': f'https://www.dianping.com/member/{mid}',
                        'user_id': mid,
                    })
                    print(f"  [5] Found user: member/{mid}")

            # Also check for shop owner mentions
            shop_links = re.findall(r'/shop/(\w+)', page_content)
            if shop_links:
                for sid in set(shop_links[:3]):
                    print(f"  [5] Found shop: shop/{sid}")

        if not results and mentions > 2:
            results.append({
                'platform': 'Dianping',
                'url': search_url,
                '_needs_manual_verify': True,
                'note': f'Username appears {mentions} times in search results',
            })

        # Step 5: Also try mobile version (sometimes less captcha)
        if not results:
            print("  [6] Trying mobile version...")
            mobile_page = await context.new_page()
            mobile_url = f'https://m.dianping.com/search?keyword={urllib.parse.quote(username)}&type=user'
            try:
                await mobile_page.goto(mobile_url, wait_until='domcontentloaded', timeout=15000)
                await asyncio.sleep(3)
                mobile_content = await mobile_page.content()
                mobile_text = await mobile_page.inner_text('body')
                mobile_mentions = mobile_text.lower().count(username.lower())
                print(f"  [6] Mobile mentions: {mobile_mentions}")
                if mobile_mentions > 1:
                    results.append({
                        'platform': 'Dianping',
                        'url': mobile_url,
                        '_needs_manual_verify': True,
                    })
            except Exception as e:
                print(f"  [6] Mobile error: {e}")
            await mobile_page.close()

        await browser.close()

    return results if results else None


async def _handle_captcha(page):
    """Try to handle Yoda captcha on the page."""
    import ddddocr

    try:
        # Wait for captcha iframe or element
        await asyncio.sleep(2)

        # Screenshot the captcha area
        captcha_frame = None
        frames = page.frames
        for frame in frames:
            content = await frame.content()
            if 'yoda' in content.lower() or '验证' in content:
                captcha_frame = frame
                break

        if not captcha_frame:
            print("  [Captcha] No captcha frame found")
            return False

        print("  [Captcha] Found captcha frame")

        # Look for slider captcha elements
        slider = await captcha_frame.query_selector('.yoda-slider, .slider-btn, [class*="slider"], [class*="drag"]')
        if not slider:
            # Try main page
            slider = await page.query_selector('.yoda-slider, .slider-btn, [class*="slider"], [class*="drag"]')

        if not slider:
            print("  [Captcha] No slider element found, trying click-based captcha")
            # Try clicking through any click-based captcha
            return await _handle_click_captcha(page, captcha_frame)

        print("  [Captcha] Found slider element")

        # Get slider position
        box = await slider.bounding_box()
        if not box:
            print("  [Captcha] Cannot get slider bounding box")
            return False

        # Get captcha images for ddddocr
        # Take screenshot of the captcha area
        screenshot_bytes = await page.screenshot()

        # Use ddddocr to detect slider gap position
        ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)

        # Try slide_comparison - need background and foreground images
        # For now, use a random drag distance as fallback
        drag_distance = random.randint(150, 280)
        print(f"  [Captcha] Dragging slider {drag_distance}px")

        # Simulate human-like drag
        start_x = box['x'] + box['width'] / 2
        start_y = box['y'] + box['height'] / 2

        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(0.1)
        await page.mouse.down()
        await asyncio.sleep(0.1)

        # Generate human-like movement path
        steps = _generate_human_track(drag_distance)
        current_x = start_x
        for step in steps:
            current_x += step
            offset_y = random.uniform(-2, 2)
            await page.mouse.move(current_x, start_y + offset_y)
            await asyncio.sleep(random.uniform(0.01, 0.03))

        await asyncio.sleep(0.1)
        await page.mouse.up()
        await asyncio.sleep(3)

        # Check if captcha was solved
        new_content = await page.content()
        if '验证' not in new_content and 'yoda' not in new_content.lower():
            print("  [Captcha] Slider captcha solved!")
            return True

        print("  [Captcha] Slider captcha not solved, retrying...")
        return False

    except Exception as e:
        print(f"  [Captcha] Error: {e}")
        return False


async def _handle_click_captcha(page, frame):
    """Handle click-based captcha (image selection, character order, etc.)."""
    try:
        # Take screenshot and try to identify clickable elements
        screenshot = await page.screenshot()

        # For now, just return False - click captchas need more complex handling
        print("  [Captcha] Click captcha not yet implemented")
        return False
    except Exception as e:
        print(f"  [Captcha] Click captcha error: {e}")
        return False


def _generate_human_track(distance):
    """Generate human-like mouse movement track for slider drag."""
    track = []
    current = 0
    mid = distance * 0.7  # Acceleration phase
    t = 0.2
    v = 0

    while current < distance:
        if current < mid:
            a = random.uniform(2, 4)  # Accelerate
        else:
            a = random.uniform(-3, -1)  # Decelerate

        v0 = v
        v = v0 + a * t
        move = v0 * t + 0.5 * a * t * t
        current += move
        track.append(round(move))

    # Add small corrections at the end
    for _ in range(random.randint(1, 3)):
        track.append(random.choice([-1, 0, 1]))

    return track


if __name__ == '__main__':
    import sys
    username = sys.argv[1] if len(sys.argv) > 1 else 'zstsang'
    print(f"Searching Dianping for: {username}\n")
    result = asyncio.run(search_dianping(username))
    if result:
        print(f"\nResults:")
        for r in result:
            print(f"  {r}")
    else:
        print("\nNo results found")
