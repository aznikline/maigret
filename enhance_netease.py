#!/usr/bin/env python3
"""
Enhance Maigret results with correct Netease Cloud Music profile URLs.

Netease uses numeric user IDs, not usernames. This script:
1. Calls the Netease search API to find the user
2. Extracts the numeric ID
3. Constructs the correct profile URL
"""

import json
import sys
import urllib.request
import urllib.parse


def search_netease_user(username):
    """Search for a user on Netease Cloud Music by username."""
    url = "https://music.163.com/api/search/get"
    data = urllib.parse.urlencode({
        's': username,
        'type': '1002',  # User search
        'limit': '5'
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=data, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Referer': 'https://music.163.com/'
    })
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            if result.get('result', {}).get('userprofileCount', 0) > 0:
                users = result['result']['userprofiles']
                # Return the first exact match
                for user in users:
                    if user.get('nickname', '').lower() == username.lower():
                        return {
                            'userId': user['userId'],
                            'nickname': user['nickname'],
                            'avatar': user.get('avatarUrl', ''),
                            'signature': user.get('signature', ''),
                            'profileUrl': f"https://music.163.com/#/user/home?id={user['userId']}"
                        }
    except Exception as e:
        print(f"Error searching Netease: {e}", file=sys.stderr)
    
    return None


def enhance_report(report_file, username):
    """Enhance a Maigret JSON report with correct Netease URLs."""
    with open(report_file, 'r') as f:
        report = json.load(f)
    
    # Check if NeteaseCloudMusic is in the results
    if 'NeteaseCloudMusic' in report:
        print(f"\n🔍 Enhancing Netease Cloud Music result for '{username}'...")
        
        user_info = search_netease_user(username)
        
        if user_info:
            # Update the URL to the correct profile link
            report['NeteaseCloudMusic']['url_user'] = user_info['profileUrl']
            report['NeteaseCloudMusic']['status']['url'] = user_info['profileUrl']
            
            # Add extracted info
            report['NeteaseCloudMusic']['status']['ids'].update({
                'netease_user_id': user_info['userId'],
                'netease_nickname': user_info['nickname'],
                'netease_avatar': user_info['avatar'],
                'netease_signature': user_info['signature']
            })
            
            print(f"✅ Found user: {user_info['nickname']} (ID: {user_info['userId']})")
            print(f"   Profile: {user_info['profileUrl']}")
        else:
            print(f"❌ User not found on Netease")
    
    # Write back the enhanced report
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n✓ Enhanced report saved to {report_file}")
    
    # Also update the text report
    txt_file = report_file.replace('_simple.json', '.txt')
    if 'NeteaseCloudMusic' in report:
        lines = []
        with open(txt_file, 'r') as f:
            for line in f:
                if 'music.163.com' in line:
                    lines.append(f"https://music.163.com/#/user/home?id={report['NeteaseCloudMusic']['status']['ids']['netease_user_id']}\n")
                else:
                    lines.append(line)
        
        with open(txt_file, 'w') as f:
            f.writelines(lines)
        
        print(f"✓ Updated text report: {txt_file}")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 enhance_netease.py <report.json> <username>")
        sys.exit(1)
    
    report_file = sys.argv[1]
    username = sys.argv[2]
    
    enhance_report(report_file, username)
