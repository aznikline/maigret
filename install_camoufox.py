#!/usr/bin/env python3
"""
Camoufox 浏览器安装脚本
用于在 GitHub API 限流时手动安装 Camoufox
"""

import sys
import os

def install_camoufox():
    """安装 Camoufox 浏览器"""
    print("=" * 60)
    print("Camoufox 浏览器安装器")
    print("=" * 60)
    
    # 检查是否已安装
    try:
        import camoufox
        print(f"✓ Camoufox Python 库已安装")
    except ImportError:
        print("✗ Camoufox Python 库未安装")
        print("  请先运行: pip install camoufox")
        return False
    
    # 尝试获取浏览器
    print("\n正在下载 Camoufox 浏览器...")
    print("这可能需要几分钟时间...")
    
    try:
        from camoufox.pkgman import CamoufoxFetcher
        fetcher = CamoufoxFetcher()
        fetcher.fetch()
        print("✓ Camoufox 浏览器安装成功！")
        return True
    except Exception as e:
        error_msg = str(e)
        if "rate limit" in error_msg.lower():
            print(f"✗ GitHub API 限流: {error_msg}")
            print("\n解决方案:")
            print("1. 等待 1 小时后重试")
            print("2. 或手动下载:")
            print("   访问 https://github.com/daijlo/camoufox/releases")
            print("   下载适合你系统的版本")
            print("   解压到 ~/.cache/camoufox/")
        else:
            print(f"✗ 安装失败: {error_msg}")
        return False

if __name__ == "__main__":
    install_camoufox()
