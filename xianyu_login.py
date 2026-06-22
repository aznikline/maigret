#!/usr/bin/env python3
"""
闲鱼扫码登录脚本
使用 Playwright 打开浏览器让用户扫码登录，然后自动保存 Cookie
"""

import asyncio
import json
import os
import time
from pathlib import Path

COOKIE_DIR = Path(__file__).parent / "cookies"
COOKIE_FILE = COOKIE_DIR / "xianyu.json"


async def login_xianyu():
    """使用 Playwright 打开闲鱼登录页面"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("错误: 需要安装 playwright")
        print("运行: pip install playwright && playwright install chromium")
        return False
    
    print("=" * 60)
    print("闲鱼扫码登录")
    print("=" * 60)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=100
        )
        
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        
        page = await context.new_page()
        
        print("\n1. 正在打开闲鱼登录页面...")
        await page.goto('https://login.taobao.com/member/login.jhtml?redirectURL=https%3A%2F%2Fwww.goofish.com%2F')
        
        print("2. 请使用淘宝/闲鱼 App 扫码登录")
        print("   或输入账号密码登录")
        print("\n3. 登录成功后，页面会自动跳转到闲鱼首页")
        print("   请在完成登录后按 Enter 键继续...")
        
        # 等待用户登录
        input()
        
        print("\n4. 正在获取 Cookie...")
        
        # 等待页面加载完成
        await asyncio.sleep(2)
        
        # 获取所有 Cookie
        cookies = await context.cookies()
        
        # 关闭浏览器
        await browser.close()
        
        # 保存 Cookie
        COOKIE_DIR.mkdir(exist_ok=True)
        
        cookie_data = {
            'cookies': cookies,
            'timestamp': time.time(),
            'platform': 'xianyu'
        }
        
        with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cookie_data, f, indent=2, ensure_ascii=False)
        
        print(f"✓ Cookie 已保存到: {COOKIE_FILE}")
        print(f"  共保存 {len(cookies)} 个 Cookie")
        
        return True


def check_cookie_valid():
    """检查 Cookie 是否有效"""
    if not COOKIE_FILE.exists():
        return False
    
    try:
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 检查是否超过 24 小时
        if time.time() - data['timestamp'] > 24 * 3600:
            print("⚠ Cookie 已过期（超过24小时）")
            return False
        
        print(f"✓ Cookie 有效（{len(data['cookies'])} 个）")
        return True
    except Exception as e:
        print(f"✗ 读取 Cookie 失败: {e}")
        return False


def main():
    """主函数"""
    print("\n检查闲鱼 Cookie 状态...")
    
    if check_cookie_valid():
        print("\nCookie 仍然有效，无需重新登录")
        response = input("\n是否强制重新登录？(y/N): ")
        if response.lower() != 'y':
            return
    
    print("\n开始登录流程...")
    success = asyncio.run(login_xianyu())
    
    if success:
        print("\n✓ 登录成功！")
        print("现在可以使用闲鱼搜索功能了")
    else:
        print("\n✗ 登录失败")


if __name__ == "__main__":
    main()
