#!/usr/bin/env python3
"""
大众点评自动登录和搜索 - Playwright版本
支持：
- 自动检测Cookie过期
- 自动启动浏览器完成扫码登录
- 自动保存Cookie
- Cookie有效期内全自动运行
"""

import asyncio
import json
import os
import time
import ssl
import urllib.request
from pathlib import Path
from playwright.async_api import async_playwright

COOKIE_DIR = Path(__file__).parent / "cookies"
COOKIE_FILE = COOKIE_DIR / "dianping.json"
COOKIE_EXPIRY_SECONDS = 7 * 24 * 60 * 60  # 7天

COOKIE_DIR.mkdir(exist_ok=True)


def cookies_valid():
    """检查Cookie是否存在且未过期"""
    if not COOKIE_FILE.exists():
        return False
    
    try:
        with open(COOKIE_FILE) as f:
            data = json.load(f)
        
        saved_at = data.get("saved_at", 0)
        cookies = data.get("cookies", [])
        
        # 检查过期
        if time.time() - saved_at > COOKIE_EXPIRY_SECONDS:
            print("[!] Cookie已过期")
            return False
        
        # 测试Cookie是否仍然有效
        if not test_cookies(cookies):
            print("[!] Cookie已失效")
            return False
        
        return True
    except Exception as e:
        print(f"[!] 检查Cookie失败: {e}")
        return False


def test_cookies(cookies):
    """测试Cookie是否有效（访问主页检查）"""
    try:
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        url = "https://www.dianping.com/"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Cookie": cookie_str,
        })
        
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        html = resp.read().decode("utf-8")
        
        # 如果被重定向到验证页面，说明Cookie失效
        if "verify.meituan.com" in resp.url or "验证" in html[:1000]:
            return False
        
        return True
    except Exception as e:
        print(f"[!] 测试Cookie失败: {e}")
        return False


async def login_with_playwright():
    """使用Playwright打开浏览器完成登录"""
    print("[*] 启动Playwright浏览器...")
    
    async with async_playwright() as p:
        # 启动有头浏览器（用户可以看到）
        browser = await p.chromium.launch(
            headless=False,  # 显示浏览器窗口
            args=["--window-size=1280,800"]
        )
        
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        
        # 访问登录页面
        print("[*] 打开大众点评登录页...")
        await page.goto("https://account.dianping.com/pclogin")
        
        # 等待用户扫码登录
        print("[*] 请使用大众点评App扫码登录...")
        print("[*] 登录成功后会自动检测并保存Cookie")
        
        # 等待登录完成（最多等待5分钟）
        max_wait = 300  # 秒
        check_interval = 3  # 秒
        waited = 0
        
        while waited < max_wait:
            await asyncio.sleep(check_interval)
            waited += check_interval
            
            current_url = page.url
            print(f"[{waited}s] 当前页面: {current_url}")
            
            # 如果跳转离开登录页面，说明登录成功
            if "account.dianping.com" not in current_url and "login" not in current_url.lower():
                print("[✓] 检测到登录成功！")
                break
            
            # 检查页面是否已经登录（通过检查用户信息元素）
            try:
                # 尝试查找用户头像或用户名元素
                user_element = await page.query_selector('.user-avatar, .user-name, [class*="avatar"], [class*="user"]')
                if user_element:
                    print("[✓] 检测到已登录状态！")
                    break
            except:
                pass
        else:
            print("[!] 等待超时，请重试")
            await browser.close()
            return False
        
        # 访问一次搜索页面，确保Cookie包含搜索权限
        print("[*] 访问搜索页面以获取完整Cookie...")
        await page.goto("https://www.dianping.com/search/keyword/1/0_test")
        await asyncio.sleep(2)
        
        # 获取所有Cookie
        cookies = await context.cookies()
        
        # 保存Cookie
        cookie_data = {
            "cookies": cookies,
            "saved_at": time.time()
        }
        
        with open(COOKIE_FILE, "w") as f:
            json.dump(cookie_data, f, indent=2, ensure_ascii=False)
        
        print(f"[✓] 已保存 {len(cookies)} 个Cookie到 {COOKIE_FILE}")
        
        await browser.close()
        return True


async def search_with_cookies(username):
    """使用保存的Cookie进行搜索"""
    if not COOKIE_FILE.exists():
        print("[!] Cookie文件不存在")
        return None
    
    with open(COOKIE_FILE) as f:
        data = json.load(f)
    
    cookies = data.get("cookies", [])
    cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
    
    # 构建搜索URL
    url = f"https://www.dianping.com/search/keyword/1/0_{username}"
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Cookie": cookie_str,
    })
    
    try:
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        
        # 检查是否被重定向到验证页面
        if "verify.meituan.com" in resp.url:
            print("[!] 触发验证码，Cookie可能已失效")
            return None
        
        html = resp.read().decode("utf-8")
        return html
    except Exception as e:
        print(f"[!] 搜索请求失败: {e}")
        return None


async def main():
    """主函数"""
    import sys
    
    # 检查是否需要登录
    need_login = not cookies_valid()
    
    if need_login:
        print("\n[!] 需要登录大众点评")
        success = await login_with_playwright()
        if not success:
            print("[✗] 登录失败")
            sys.exit(1)
    else:
        print("[✓] Cookie有效，无需登录")
    
    # 如果有参数，执行搜索
    if len(sys.argv) > 1:
        username = sys.argv[1]
        print(f"\n[*] 搜索用户名: {username}")
        
        html = await search_with_cookies(username)
        if html:
            print(f"[✓] 搜索成功，响应大小: {len(html)} bytes")
            
            # 简单解析结果
            if username.lower() in html.lower():
                import re
                # 查找用户名出现的上下文
                pattern = re.compile(r'.{0,50}' + re.escape(username) + r'.{0,50}', re.IGNORECASE)
                matches = pattern.findall(html)
                print(f"[✓] 找到 {len(matches)} 处匹配")
                
                # 显示前3个匹配
                for i, match in enumerate(matches[:3], 1):
                    # 清理HTML标签
                    clean = re.sub(r'<[^>]+>', ' ', match).strip()
                    print(f"  {i}. {clean}")
            else:
                print("[!] 未在搜索结果中找到该用户名")
        else:
            print("[✗] 搜索失败，可能需要重新登录")
            # 删除失效的Cookie
            if COOKIE_FILE.exists():
                COOKIE_FILE.unlink()
                print("[*] 已删除失效Cookie，下次运行将重新登录")


if __name__ == "__main__":
    asyncio.run(main())
