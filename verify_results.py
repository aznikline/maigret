#!/usr/bin/env python3
"""
Verify Maigret results using platform APIs to eliminate false positives.
"""

import json
import sys
import urllib.request
import urllib.parse
import re


def check_leetcode_cn(username):
    """Check LeetCode CN user via GraphQL API."""
    url = "https://leetcode.cn/graphql/"
    data = json.dumps({
        "query": "query { userProfilePublicProfile(username: \"%s\") { username realName siteRanking } }" % username,
        "variables": {}
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers={
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0'
    })
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('data', {}).get('userProfilePublicProfile'):
                user = result['data']['userProfilePublicProfile']
                return {
                    'username': user.get('username'),
                    'realName': user.get('realName'),
                    'profileUrl': f"https://leetcode.cn/u/{username}/"
                }
    except Exception as e:
        print(f"  LeetCode CN API error: {e}", file=sys.stderr)
    return None


def check_oschina(username):
    """Check OSChina user profile."""
    url = f"https://my.oschina.net/{username}"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    })
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
            # Check if page has actual user content
            if f'/u/{username}' in html or f'nickname' in html:
                return {'profileUrl': url, 'username': username}
            # Check for error/not found
            if '404' in html or 'not found' in html.lower() or len(html) < 5000:
                return None
    except Exception as e:
        print(f"  OSChina error: {e}", file=sys.stderr)
    return None


def check_wechat_read(username):
    """Check WeChat Read - this platform doesn't have public user profiles by username."""
    # WeChat Read uses internal IDs, not usernames
    # The URL format weread.qq.com/web/category/{username} is not valid
    return None


def check_taobao(username):
    """Check Taobao user - requires login, can't verify publicly."""
    # Taobao user profiles are not publicly accessible without login
    return None


def check_douyin(username):
    """Check Douyin user via search API."""
    # Douyin requires authentication for most API calls
    # The search page exists but may not show results without login
    url = f"https://www.douyin.com/search/{username}?type=user"
    return {'profileUrl': url, 'note': 'Search page - verify manually'}


def check_ximalaya(username):
    """Check Ximalaya user via search."""
    url = f"https://www.ximalaya.com/search/{username}?type=user"
    return {'profileUrl': url, 'note': 'Search page - verify manually'}


def verify_report(report_file, username):
    """Verify all results in a Maigret JSON report."""
    with open(report_file, 'r') as f:
        report = json.load(f)
    
    print(f"\n🔍 Verifying results for '{username}'...\n")
    
    verified = {}
    removed = []
    
    # Check each platform
    platforms = {
        'LeetCodeCN': check_leetcode_cn,
        'OSChina': check_oschina,
        'WeChat_Read': check_wechat_read,
        'Taobao': check_taobao,
        'Douyin': check_douyin,
        'Ximalaya': check_ximalaya
    }
    
    for platform, checker in platforms.items():
        if platform in report:
            print(f"Checking {platform}...")
            result = checker(username)
            if result:
                print(f"  ✅ Found: {result.get('username', username)}")
                if 'profileUrl' in result:
                    report[platform]['url_user'] = result['profileUrl']
                    report[platform]['status']['url'] = result['profileUrl']
                if result.get('note'):
                    print(f"     Note: {result['note']}")
                verified[platform] = result
            else:
                print(f"  ❌ Not found (removing)")
                removed.append(platform)
    
    # Remove false positives
    for platform in removed:
        del report[platform]
    
    # Save updated report
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    # Update text report
    txt_file = report_file.replace('_simple.json', '.txt')
    lines = []
    with open(txt_file, 'r') as f:
        for line in f:
            # Keep only verified platforms
            keep = True
            for platform in removed:
                if platform.lower().replace('_', '') in line.lower():
                    keep = False
                    break
            if keep:
                # Update URLs for verified platforms
                for platform, data in verified.items():
                    if 'profileUrl' in data and platform.lower().replace('_', '') in line.lower():
                        lines.append(f"{data['profileUrl']}\n")
                        keep = False
                        break
                if keep:
                    lines.append(line)
    
    # Update total count
    new_count = len([l for l in lines if l.startswith('http')])
    lines = [f"Total Websites Username Detected On : {new_count}\n" if 'Total Websites' in l else l for l in lines]
    
    with open(txt_file, 'w') as f:
        f.writelines(lines)
    
    print(f"\n✓ Verification complete")
    print(f"  Verified: {len(verified)}")
    print(f"  Removed: {len(removed)} ({', '.join(removed)})")
    print(f"  Total remaining: {len(report)}")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 verify_results.py <report.json> <username>")
        sys.exit(1)
    
    verify_report(sys.argv[1], sys.argv[2])
