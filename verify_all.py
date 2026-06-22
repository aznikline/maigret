#!/usr/bin/env python3
"""
Platform API verifier for Maigret results.
Uses actual platform APIs instead of unreliable HTTP status codes.
"""

import json
import sys
import os
import urllib.request
import urllib.parse
import ssl

# Disable SSL verification for convenience
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def http_get(url, headers=None):
    """Simple GET request."""
    h = {'User-Agent': UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return resp.status, resp.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception as e:
        return 0, str(e)


def http_post(url, data, headers=None):
    """Simple POST request."""
    h = {'User-Agent': UA}
    if headers:
        h.update(headers)
    if isinstance(data, dict):
        data = json.dumps(data).encode('utf-8')
        h.setdefault('Content-Type', 'application/json')
    elif isinstance(data, str):
        data = data.encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return resp.status, resp.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode('utf-8', errors='ignore')
        except:
            return e.code, ''
    except Exception as e:
        return 0, str(e)


# ─── Platform Verifiers ───

def verify_netease(username):
    """Netease Cloud Music: search API returns user profiles."""
    data = urllib.parse.urlencode({'s': username, 'type': '1002', 'limit': '5'}).encode()
    code, body = http_post('https://music.163.com/api/search/get', data,
                           {'Referer': 'https://music.163.com/'})
    if code != 200:
        return None
    try:
        d = json.loads(body)
        users = d.get('result', {}).get('userprofiles', [])
        for u in users:
            if u.get('nickname', '').lower() == username.lower():
                return {
                    'url': f"https://music.163.com/#/user/home?id={u['userId']}",
                    'nickname': u['nickname'],
                    'user_id': u['userId'],
                    'avatar': u.get('avatarUrl', ''),
                }
    except:
        pass
    return None


def verify_bilibili(username):
    """Bilibili: search API for users."""
    url = f"https://api.bilibili.com/x/web-interface/search/type?search_type=bili_user&keyword={urllib.parse.quote(username)}"
    code, body = http_get(url, {'Referer': 'https://www.bilibili.com/'})
    if code != 200:
        return None
    try:
        d = json.loads(body)
        results = d.get('data', {}).get('result', [])
        for r in results:
            uname = r.get('uname', '')
            if uname.lower() == username.lower():
                return {
                    'url': f"https://space.bilibili.com/{r['mid']}",
                    'nickname': uname,
                    'user_id': r['mid'],
                    'fans': r.get('fans', 0),
                    'videos': r.get('videos', 0),
                }
    except:
        pass
    return None


def verify_zhihu(username):
    """Zhihu: check if user profile page has real content."""
    url = f"https://www.zhihu.com/people/{username}"
    code, body = http_get(url)
    if code != 200:
        return None
    # Zhihu SPA: check for actual user data in SSR HTML
    if '"url_token"' in body and '"headline"' in body:
        # Try to extract name from JSON-LD or meta
        import re
        name_match = re.search(r'"name"\s*:\s*"([^"]+)"', body)
        headline_match = re.search(r'"headline"\s*:\s*"([^"]*)"', body)
        return {
            'url': url,
            'nickname': name_match.group(1) if name_match else username,
            'headline': headline_match.group(1) if headline_match else '',
        }
    return None


def verify_weibo(username):
    """Weibo: check user profile."""
    url = f"https://weibo.com/{username}"
    code, body = http_get(url)
    if code != 200 or len(body) < 1000:
        return None
    if '"screen_name"' in body:
        import re
        name = re.search(r'"screen_name"\s*:\s*"([^"]+)"', body)
        return {
            'url': url,
            'nickname': name.group(1) if name else username,
        }
    return None


def verify_douyin(username):
    """Douyin: no public API, mark as needs-verification."""
    return {'url': f"https://www.douyin.com/search/{urllib.parse.quote(username)}?type=user",
            '_needs_manual_verify': True}


def verify_ximalaya(username):
    """Ximalaya: search API."""
    url = f"https://www.ximalaya.com/search/{urllib.parse.quote(username)}?type=user"
    code, body = http_get(url)
    if code != 200:
        return None
    # Check for actual user results in SSR content
    if '"anchorDtoList"' in body and username.lower() in body.lower():
        return {'url': url, '_needs_manual_verify': True}
    return None


def verify_leetcode_cn(username):
    """LeetCode CN: GraphQL API."""
    query = {
        "query": "query userPublicProfile($userSlug: String!) { userProfilePublicProfile(userSlug: $userSlug) { username realName siteRanking profile { userSlug } } }",
        "variables": {"userSlug": username}
    }
    code, body = http_post('https://leetcode.cn/graphql/', query, {
        'Content-Type': 'application/json',
        'Referer': 'https://leetcode.cn/'
    })
    if code != 200:
        return None
    try:
        d = json.loads(body)
        profile = d.get('data', {}).get('userProfilePublicProfile')
        if profile and profile.get('username'):
            return {
                'url': f"https://leetcode.cn/u/{username}/",
                'nickname': profile.get('realName') or profile['username'],
                'ranking': profile.get('siteRanking'),
            }
    except:
        pass
    return None


def verify_oschina(username):
    """OSChina: check profile page for actual user content."""
    url = f"https://my.oschina.net/{username}"
    code, body = http_get(url)
    if code != 200 or len(body) < 10000:
        return None
    # Real user pages have specific DOM elements
    if 'user-info' in body or 'user-name' in body or f'/u/{username}' in body:
        return {'url': url}
    return None


def verify_gitee(username):
    """Gitee: check user profile."""
    url = f"https://gitee.com/{username}"
    code, body = http_get(url)
    if code != 200:
        return None
    if '个人主页' in body or '"login"' in body:
        import re
        name = re.search(r'"name"\s*:\s*"([^"]+)"', body)
        return {
            'url': url,
            'nickname': name.group(1) if name else username,
        }
    return None


def verify_github(username):
    """GitHub: REST API."""
    url = f"https://api.github.com/users/{username}"
    code, body = http_get(url)
    if code != 200:
        return None
    try:
        d = json.loads(body)
        if d.get('login'):
            return {
                'url': f"https://github.com/{username}",
                'nickname': d.get('name') or d['login'],
                'bio': d.get('bio', ''),
                'followers': d.get('followers', 0),
                'public_repos': d.get('public_repos', 0),
            }
    except:
        pass
    return None


def verify_medium(username):
    """Medium: try RSS first, fall back to profile page."""
    # Try RSS
    code, body = http_get(f"https://medium.com/feed/@{username}")
    if code == 200 and '<channel>' in body:
        import re
        title = re.search(r'<title>(.*?)</title>', body)
        return {
            'url': f"https://medium.com/@{username}",
            'nickname': title.group(1) if title else username,
        }
    # Fallback: profile page
    code, body = http_get(f"https://medium.com/@{username}")
    if code == 200 and ('@' + username) in body and '"isFollowing"' in body:
        return {'url': f"https://medium.com/@{username}"}
    return None


def verify_weread(username):
    """WeChat Read: no public user profiles by username."""
    return None


def verify_taobao(username):
    """Taobao: no public user profiles without login."""
    return None


def verify_csdn(username):
    """CSDN: search API (no login required)."""
    url = f"https://so.csdn.net/api/v3/search?q={urllib.parse.quote(username)}&t=user"
    code, body = http_get(url)
    if code != 200:
        return None
    try:
        d = json.loads(body)
        total = d.get('total', 0)
        if total > 0:
            for u in d.get('result_vos', []):
                title = u.get('title', '')
                if username.lower() in title.lower():
                    return {'url': u.get('url', f"https://blog.csdn.net/{username}"),
                            'nickname': title}
    except:
        pass
    return None


def verify_juejin(username):
    """Juejin: search API (no login required)."""
    url = f"https://api.juejin.cn/search_api/v1/search?query={urllib.parse.quote(username)}&cursor=0&limit=10&type=user"
    code, body = http_get(url)
    if code != 200:
        return None
    try:
        d = json.loads(body)
        for u in d.get('data', []):
            info = u.get('result_model', {})
            name = info.get('user_name', '')
            if name.lower() == username.lower():
                return {
                    'url': f"https://juejin.cn/user/{info.get('user_id', username)}",
                    'nickname': name,
                }
    except:
        pass
    return None


def verify_hackernews(username):
    """HackerNews: Algolia API (no login required)."""
    url = f"https://hn.algolia.com/api/v1/search?query={urllib.parse.quote(username)}&type=users"
    code, body = http_get(url)
    if code != 200:
        return None
    try:
        d = json.loads(body)
        for h in d.get('hits', []):
            if h.get('author', '').lower() == username.lower():
                return {
                    'url': f"https://news.ycombinator.com/user?id={username}",
                    'nickname': h['author'],
                    'karma': h.get('karma', 0),
                }
    except:
        pass
    return None


def verify_keybase(username):
    """Keybase: API (no login required)."""
    url = f"https://keybase.io/_/api/1.0/user/lookup.json?usernames={username}"
    code, body = http_get(url)
    if code != 200:
        return None
    try:
        d = json.loads(body)
        them = d.get('them', [])
        if them and them[0]:
            user = them[0]
            profile = user.get('profile', {})
            return {
                'url': f"https://keybase.io/{username}",
                'nickname': profile.get('full_name', username),
            }
    except:
        pass
    return None


def verify_weibo(username):
    """Weibo: get visitor token, then check profile page."""
    # Step 1: get visitor TID
    code, body = http_post('https://passport.weibo.com/visitor/genvisitor',
                           'cb=gen_callback&fp=%7B%7D')
    if code != 200:
        return None
    import re
    tid_match = re.search(r'"tid":"([^"]+)"', body)
    if not tid_match:
        return None
    tid = tid_match.group(1)

    # Step 2: exchange TID for SUB cookie
    code, body = http_get(
        f"https://passport.weibo.com/visitor/visitor?a=incarnate&t={tid}&w=2&c=095&gc=&cb=cross_domain&from=weibo&_rand=0.{int(__import__('time').time())}"
    )
    if code != 200:
        return None
    sub_match = re.search(r'"sub":"([^"]+)"', body)
    subp_match = re.search(r'"subp":"([^"]+)"', body)
    if not sub_match:
        return None
    sub = sub_match.group(1)
    subp = subp_match.group(1) if subp_match else ''

    # Step 3: check user profile page
    url = f"https://weibo.com/{username}"
    code, body = http_get(url, {
        'Cookie': f'SUB={sub}; SUBP={subp}',
        'Referer': 'https://weibo.com/',
    })
    if code != 200 or len(body) < 5000:
        return None
    # Look for user data in SSR HTML
    if '"screen_name"' in body:
        name = re.search(r'"screen_name"\s*:\s*"([^"]+)"', body)
        uid = re.search(r'"id"\s*:\s*(\d+)', body)
        return {
            'url': url,
            'nickname': name.group(1) if name else username,
            'user_id': uid.group(1) if uid else '',
        }
    return None


def verify_weibo_search(username):
    """Weibo search: same as verify_weibo."""
    return verify_weibo(username)


def verify_douban(username):
    """Douban: search people (may need cookie)."""
    url = f"https://www.douban.com/search?cat=1013&q={urllib.parse.quote(username)}"
    code, body = http_get(url)
    if code != 200 or len(body) < 1000:
        return None
    import re
    # Look for user links
    users = re.findall(r'https://www\.douban\.com/people/([^/"]+)/', body)
    for u in users:
        if u.lower() == username.lower():
            return {
                'url': f"https://www.douban.com/people/{username}/",
            }
    return None


# ─── Registry ───

VERIFIERS = {
    'NeteaseCloudMusic': verify_netease,
    'Bilibili': verify_bilibili,
    'Zhihu': verify_zhihu,
    'Weibo': verify_weibo,
    'Weibo_Search': verify_weibo_search,
    'Douyin': verify_douyin,
    'Ximalaya': verify_ximalaya,
    'LeetCodeCN': verify_leetcode_cn,
    'OSChina': verify_oschina,
    'Gitee': verify_gitee,
    'GitHub': verify_github,
    'Medium': verify_medium,
    'WeChat_Read': verify_weread,
    'Taobao': verify_taobao,
    'CSDN': verify_csdn,
    'Juejin': verify_juejin,
    'HackerNews': verify_hackernews,
    'Keybase': verify_keybase,
    'Douban': verify_douban,
    'Douban_People': verify_douban,
}


def verify_all(report_json_path, username):
    """Verify all platforms in a Maigret JSON report."""
    with open(report_json_path) as f:
        report = json.load(f)

    print(f"\n{'='*50}")
    print(f"  Verifying: {username}")
    print(f"{'='*50}\n")

    verified = []
    removed = []

    for site_name in list(report.keys()):
        verifier = VERIFIERS.get(site_name)
        if not verifier:
            # No verifier for this platform, keep as-is
            print(f"  [SKIP] {site_name} (no verifier)")
            continue

        print(f"  [CHECK] {site_name}...", end=' ')
        result = verifier(username)

        if result:
            # Update URL
            report[site_name]['url_user'] = result['url']
            report[site_name]['status']['url'] = result['url']

            # Store extra info
            extras = {k: v for k, v in result.items() if k not in ('url', '_needs_manual_verify')}
            report[site_name]['status']['ids'].update(extras)

            if result.get('_needs_manual_verify'):
                print(f"⚠️  {result['url']}  (needs manual check)")
                verified.append((site_name, result['url'], '⚠️'))
            else:
                info = ', '.join(f"{k}={v}" for k, v in extras.items() if v)
                print(f"✅ {result['url']}  {info}")
                verified.append((site_name, result['url'], '✅'))
        else:
            print(f"❌ not found (removing)")
            del report[site_name]
            removed.append(site_name)

    # Save updated JSON
    with open(report_json_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Update TXT
    txt_path = report_json_path.replace('_simple.json', '.txt')
    urls = []
    for site_name, info in report.items():
        urls.append(info['status']['url'])
    with open(txt_path, 'w') as f:
        for u in urls:
            f.write(u + '\n')
        f.write(f"Total Websites Username Detected On : {len(urls)}\n")

    # Summary
    print(f"\n{'='*50}")
    print(f"  Verified: {len(verified)}  |  Removed: {len(removed)}")
    print(f"{'='*50}")
    if verified:
        print(f"\n  Found accounts:")
        for name, url, status in verified:
            print(f"    {status} {name}: {url}")
    if removed:
        print(f"\n  Removed (false positives):")
        for name in removed:
            print(f"    ❌ {name}")
    print()


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: verify_all.py <report.json> <username>")
        sys.exit(1)
    verify_all(sys.argv[1], sys.argv[2])
