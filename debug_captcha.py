#!/usr/bin/env python3
"""
Debug: inspect Dianping captcha page structure.
"""
import asyncio

async def main():
    from playwright.async_api import async_playwright
    import urllib.parse

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            '--disable-blink-features=AutomationControlled', '--no-sandbox'])
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800}, locale='zh-CN')
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        page = await context.new_page()

        # Visit homepage
        await page.goto('https://www.dianping.com/', wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(2)

        # Visit search
        url = f'https://www.dianping.com/search/keyword/1/0_{urllib.parse.quote("zstsang")}'
        await page.goto(url, wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(3)

        # Get full page content
        content = await page.content()
        print(f"Main page size: {len(content)}")
        print(f"URL: {page.url}")
        print()

        # Check frames
        print(f"Frames: {len(page.frames)}")
        for i, frame in enumerate(page.frames):
            furl = frame.url
            fcontent = await frame.content()
            print(f"  Frame {i}: {furl[:100]}  size={len(fcontent)}")
            # Look for interactive elements
            if len(fcontent) > 500:
                # Find form elements, buttons, etc
                import re
                forms = re.findall(r'<(?:input|button|div)[^>]*class="([^"]*)"[^>]*>', fcontent)
                if forms:
                    print(f"    Classes: {forms[:15]}")
                # Find IDs
                ids = re.findall(r'id="([^"]+)"', fcontent)
                if ids:
                    print(f"    IDs: {ids[:10]}")

        # Screenshot
        await page.screenshot(path='/Users/wizout/op/maigret/results/dianping_captcha.png', full_page=True)
        print("\nScreenshot saved to results/dianping_captcha.png")

        await browser.close()

asyncio.run(main())
