#!/usr/bin/env python3
"""
大众点评自动登录工具 - 扫码登录后自动提取 Cookie
用法: python3 dianping_login.py
"""

import json
import time
import subprocess
import sys

def main():
    print("=" * 60)
    print("大众点评自动登录")
    print("=" * 60)
    
    # 1. 打开登录页
    print("\n[1/3] 正在打开大众点评登录页...")
    subprocess.run(['open', 'https://account.dianping.com/pclogin?redir=https%3A%2F%2Fwww.dianping.com%2F'])
    
    # 2. 等待用户扫码
    print("[2/3] 请用大众点评/美团 App 扫码登录")
    input("    扫码完成后按 Enter 继续...")
    
    # 3. 提取 Cookie
    print("[3/3] 正在从 Chrome 提取 Cookie...")
    try:
        from pycookiecheat import chrome_cookies
        cookies = chrome_cookies('https://www.dianping.com')
        
        if not cookies:
            print("❌ 未找到 Cookie，请确认：")
            print("   1. 已在 Chrome 中完成扫码登录")
            print("   2. 登录页面显示已登录状态")
            return False
        
        # 保存 Cookie
        cookie_list = [
            {'name': k, 'value': v, 'domain': '.dianping.com', 'path': '/'}
            for k, v in cookies.items()
        ]
        
        with open('cookies/dianping.json', 'w') as f:
            json.dump({'cookies': cookie_list, 'saved_at': time.time()}, f)
        
        print(f"\n✅ 成功保存 {len(cookie_list)} 个 Cookie")
        print(f"   文件: cookies/dianping.json")
        print(f"   有效期: 7 天")
        print("\n现在可以正常使用 deep_search.py 搜索大众点评了！")
        return True
        
    except Exception as e:
        print(f"❌ 提取失败: {e}")
        print("\n请确保：")
        print("  1. 已安装 pycookiecheat: pip install pycookiecheat")
        print("  2. 已在 Chrome 中登录大众点评")
        print("  3. 授权了 Keychain 访问")
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
