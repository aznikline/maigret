#!/usr/bin/env python3
"""
Unified anti-detection HTTP layer for Chinese platforms.
Integrates: curl_cffi, DrissionPage, patchright, nodriver.
"""

import json
import os
import re
import time
import ssl
import urllib.request
import urllib.parse

COOKIE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies')

# ─── Layer 1: curl_cffi (TLS fingerprint impersonation) ───

def fetch_curl_cffi(url, headers=None, timeout=15, impersonate='chrome'):
    """Use curl_cffi to impersonate browser TLS fingerprint."""
    try:
        from curl_cffi import requests as cffi_requests
        h = headers or {}
        resp = cffi_requests.get(url, headers=h, timeout=timeout, impersonate=impersonate)
        return resp.status_code, resp.text
    except ImportError:
        return 0, ''
    except Exception as e:
        return 0, str(e)


# ─── Layer 2: DrissionPage (Chinese platform specialist) ───

def fetch_drission(url, cookies=None, timeout=20):
    """Use DrissionPage for Chinese platforms (Meituan, Taobao, etc.)."""
    try:
        from DrissionPage import SessionPage
        page = SessionPage()
        page.set.timeouts(timeout)
        if cookies:
            if isinstance(cookies, str):
                for pair in cookies.split(';'):
                    pair = pair.strip()
                    if '=' in pair:
                        name, value = pair.split('=', 1)
                        page.set.cookies(name.strip(), value.strip(), domain='.dianping.com')
            elif isinstance(cookies, list):
                for c in cookies:
                    page.set.cookies(c['name'], c['value'], domain=c.get('domain', ''))
        page.get(url)
        return page.response.status_code if hasattr(page, 'response') else 200, page.html
    except ImportError:
        return 0, ''
    except Exception as e:
        return 0, str(e)


def search_drission_dianping(username, cookie_list=None):
    """Search Dianping using DrissionPage with cookie support."""
    try:
        from DrissionPage import SessionPage
        page = SessionPage()
        page.set.timeouts(20)

        # Load cookies
        if cookie_list:
            for c in cookie_list:
                page.set.cookies(c['name'], c['value'], domain='.dianping.com')
        else:
            cookie_file = os.path.join(COOKIE_DIR, 'dianping.json')
            if os.path.exists(cookie_file):
                with open(cookie_file) as f:
                    data = json.load(f)
                for c in data.get('cookies', []):
                    page.set.cookies(c['name'], c['value'], domain='.dianping.com')

        # Visit homepage first to establish session
        page.get('https://www.dianping.com/')
        time.sleep(1)

        # Search
        url = f'https://www.dianping.com/search/keyword/1/0_{urllib.parse.quote(username)}'
        page.get(url)
        html = page.html

        # Check results
        if 'verify.meituan.com' in str(page.url) or len(html) < 10000:
            return None

        text = re.sub(r'<[^>]+>', ' ', html)
        count = text.lower().count(username.lower())

        if count > 2:
            member_links = re.findall(r'/member/(\d+)', html)
            if member_links:
                return {
                    'platform': 'Dianping',
                    'url': f'https://www.dianping.com/member/{member_links[0]}',
                    'user_id': member_links[0],
                    '_method': 'DrissionPage'
                }
            return {
                'platform': 'Dianping',
                'url': url,
                '_needs_manual_verify': True,
                'note': f'Found {count} mentions via DrissionPage',
                '_method': 'DrissionPage'
            }
        return None
    except Exception:
        return None


# ─── Layer 3: curl_cffi enhanced search ───

def search_curl_cffi_zhihu(username):
    """Search Zhihu using curl_cffi with Chrome TLS fingerprint."""
    code, body = fetch_curl_cffi(
        f'https://www.zhihu.com/api/v4/search_v3?t=people&q={urllib.parse.quote(username)}',
        headers={'Referer': 'https://www.zhihu.com/'}
    )
    if code != 200:
        return None
    try:
        d = json.loads(body)
        for item in d.get('data', []):
            obj = item.get('object', {})
            if obj.get('url_token', '').lower() == username.lower():
                return {
                    'platform': 'Zhihu',
                    'url': f'https://www.zhihu.com/people/{username}',
                    'nickname': obj.get('name', username),
                    'headline': obj.get('headline', ''),
                    '_method': 'curl_cffi'
                }
    except:
        pass
    return None


def search_curl_cffi_douban(username):
    """Search Douban using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://www.douban.com/search?cat=1013&q={urllib.parse.quote(username)}',
        impersonate='chrome'
    )
    if code != 200:
        return None
    users = re.findall(r'https://www\.douban\.com/people/([^/"]+)/', body)
    for u in users:
        if u.lower() == username.lower():
            return {
                'platform': 'Douban',
                'url': f'https://www.douban.com/people/{username}/',
                '_method': 'curl_cffi'
            }
    return None


def search_curl_cffi_github(username):
    """Search GitHub using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://api.github.com/users/{username}',
        impersonate='chrome'
    )
    if code != 200:
        return None
    try:
        d = json.loads(body)
        if d.get('login'):
            return {
                'platform': 'GitHub',
                'url': f'https://github.com/{username}',
                'nickname': d.get('name') or d['login'],
                'bio': d.get('bio', ''),
                'followers': d.get('followers', 0),
                'public_repos': d.get('public_repos', 0),
                '_method': 'curl_cffi'
            }
    except:
        pass
    return None


def search_curl_cffi_xianyu(username):
    """Search Xianyu/Goofish using curl_cffi with cookie."""
    cookie_file = os.path.join(COOKIE_DIR, 'xianyu.json')
    cookie_str = ''
    if os.path.exists(cookie_file):
        with open(cookie_file) as f:
            data = json.load(f)
        cookie_str = '; '.join(f"{c['name']}={c['value']}" for c in data.get('cookies', []))

    headers = {}
    if cookie_str:
        headers['Cookie'] = cookie_str

    code, body = fetch_curl_cffi(
        f'https://www.goofish.com/search?keyword={urllib.parse.quote(username)}',
        headers=headers,
        impersonate='chrome'
    )
    if code != 200:
        return None

    text = re.sub(r'<[^>]+>', ' ', body)
    count = text.lower().count(username.lower())
    if count > 2:
        return {
            'platform': 'Xianyu',
            'url': f'https://www.goofish.com/search?keyword={urllib.parse.quote(username)}',
            '_needs_manual_verify': True,
            'note': f'Found {count} mentions via curl_cffi',
            '_method': 'curl_cffi'
        }
    return None


def search_curl_cffi_gaode(username):
    """Search Gaode/Amap using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://www.amap.com/search?query={urllib.parse.quote(username)}',
        impersonate='chrome'
    )
    if code != 200:
        return None
    text = re.sub(r'<[^>]+>', ' ', body)
    if username.lower() in text.lower():
        return {
            'platform': 'Gaode',
            'url': f'https://www.amap.com/search?query={urllib.parse.quote(username)}',
            '_needs_manual_verify': True,
            '_method': 'curl_cffi'
        }
    return None


def search_curl_cffi_bilibili(username):
    """Search Bilibili using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://api.bilibili.com/x/web-interface/search/type?search_type=bili_user&keyword={urllib.parse.quote(username)}',
        headers={'Referer': 'https://www.bilibili.com/'},
        impersonate='chrome'
    )
    if code != 200:
        return None
    try:
        d = json.loads(body)
        for r in d.get('data', {}).get('result', []) or []:
            if r.get('uname', '').lower() == username.lower():
                return {
                    'platform': 'Bilibili',
                    'url': f'https://space.bilibili.com/{r["mid"]}',
                    'nickname': r['uname'],
                    'user_id': r['mid'],
                    'fans': r.get('fans', 0),
                    '_method': 'curl_cffi'
                }
    except:
        pass
    return None


def search_curl_cffi_reddit(username):
    """Search Reddit using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://www.reddit.com/user/{username}/about.json',
        headers={'User-Agent': 'osint-search/1.0'},
        impersonate='chrome'
    )
    if code != 200:
        return None
    try:
        d = json.loads(body)
        data = d.get('data', {})
        if data.get('name'):
            return {
                'platform': 'Reddit',
                'url': f'https://www.reddit.com/user/{username}',
                'nickname': data['name'],
                'karma': data.get('total_karma', 0),
                '_method': 'curl_cffi'
            }
    except:
        pass
    return None


def search_curl_cffi_spotify(username):
    """Search Spotify using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://open.spotify.com/user/{username}',
        impersonate='chrome'
    )
    if code == 200 and '"profile"' in body:
        return {
            'platform': 'Spotify',
            'url': f'https://open.spotify.com/user/{username}',
            '_method': 'curl_cffi'
        }
    return None


def search_curl_cffi_steam(username):
    """Search Steam using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://steamcommunity.com/id/{username}',
        impersonate='chrome'
    )
    if code != 200:
        return None
    if 'The specified profile could not be found' not in body:
        if 'steamid' in body or 'profile' in body:
            return {
                'platform': 'Steam',
                'url': f'https://steamcommunity.com/id/{username}',
                '_method': 'curl_cffi'
            }
    return None


def search_curl_cffi_leetcode_cn(username):
    """Search LeetCode CN using curl_cffi."""
    query = {
        "query": "query userPublicProfile($userSlug: String!) { userProfilePublicProfile(userSlug: $userSlug) { username realName siteRanking } }",
        "variables": {"userSlug": username}
    }
    code, body = fetch_curl_cffi(
        'https://leetcode.cn/graphql/',
        headers={
            'Content-Type': 'application/json',
            'Referer': 'https://leetcode.cn/',
        },
        impersonate='chrome'
    )
    # Need POST for GraphQL
    try:
        from curl_cffi import requests as cffi_requests
        resp = cffi_requests.post(
            'https://leetcode.cn/graphql/',
            json=query,
            headers={'Referer': 'https://leetcode.cn/'},
            timeout=15,
            impersonate='chrome'
        )
        d = json.loads(resp.text)
        p = d.get('data', {}).get('userProfilePublicProfile')
        if p and p.get('username'):
            return {
                'platform': 'LeetCodeCN',
                'url': f'https://leetcode.cn/u/{username}/',
                'nickname': p.get('realName') or p['username'],
                'ranking': p.get('siteRanking'),
                '_method': 'curl_cffi'
            }
    except:
        pass
    return None


def search_curl_cffi_gitee(username):
    """Search Gitee using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://gitee.com/{username}',
        impersonate='chrome'
    )
    if code != 200:
        return None
    if '"login"' in body or '个人主页' in body:
        name = re.search(r'"name"\s*:\s*"([^"]+)"', body)
        return {
            'platform': 'Gitee',
            'url': f'https://gitee.com/{username}',
            'nickname': name.group(1) if name else username,
            '_method': 'curl_cffi'
        }
    return None


def search_curl_cffi_csdn(username):
    """Search CSDN using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://so.csdn.net/api/v3/search?q={urllib.parse.quote(username)}&t=user',
        impersonate='chrome'
    )
    if code != 200:
        return None
    try:
        d = json.loads(body)
        for r in d.get('result_vos', []):
            if username.lower() in r.get('title', '').lower():
                return {
                    'platform': 'CSDN',
                    'url': r.get('url', f'https://blog.csdn.net/{username}'),
                    'nickname': r.get('title', username),
                    '_method': 'curl_cffi'
                }
    except:
        pass
    return None


def search_curl_cffi_juejin(username):
    """Search Juejin using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://api.juejin.cn/search_api/v1/search?query={urllib.parse.quote(username)}&cursor=0&limit=10&type=user',
        impersonate='chrome'
    )
    if code != 200:
        return None
    try:
        d = json.loads(body)
        for item in d.get('data', []):
            info = item.get('result_model', {})
            if info.get('user_name', '').lower() == username.lower():
                return {
                    'platform': 'Juejin',
                    'url': f'https://juejin.cn/user/{info.get("user_id", username)}',
                    'nickname': info['user_name'],
                    '_method': 'curl_cffi'
                }
    except:
        pass
    return None


def search_curl_cffi_hackernews(username):
    """Search HackerNews using curl_cffi."""
    code, body = fetch_curl_cffi(
        f'https://hn.algolia.com/api/v1/search?query={urllib.parse.quote(username)}&tags=author',
        impersonate='chrome'
    )
    if code != 200:
        return None
    try:
        d = json.loads(body)
        for h in d.get('hits', []):
            if h.get('author', '').lower() == username.lower():
                return {
                    'platform': 'HackerNews',
                    'url': f'https://news.ycombinator.com/user?id={username}',
                    'nickname': h['author'],
                    'karma': h.get('points', 0),
                    '_method': 'curl_cffi'
                }
    except:
        pass
    return None


def search_curl_cffi_xiaohongshu(username):
    """Search Xiaohongshu (Little Red Book) using curl_cffi."""
    # Try user profile page
    code, body = fetch_curl_cffi(
        f'https://www.xiaohongshu.com/user/profile/{username}',
        impersonate='chrome'
    )
    if code == 200 and len(body) > 5000:
        if username.lower() in body.lower():
            return {
                'platform': 'Xiaohongshu',
                'url': f'https://www.xiaohongshu.com/user/profile/{username}',
                '_needs_manual_verify': True,
                '_method': 'curl_cffi'
            }
    return None


def search_curl_cffi_kuaishou(username):
    """Search Kuaishou using curl_cffi."""
    # Try user profile
    code, body = fetch_curl_cffi(
        f'https://www.kuaishou.com/profile/{username}',
        impersonate='chrome'
    )
    if code == 200 and len(body) > 5000:
        if username.lower() in body.lower():
            return {
                'platform': 'Kuaishou',
                'url': f'https://www.kuaishou.com/profile/{username}',
                '_needs_manual_verify': True,
                '_method': 'curl_cffi'
            }
    return None


def search_curl_cffi_douyin(username):
    """Search Douyin (TikTok China) using curl_cffi."""
    # Try user search
    code, body = fetch_curl_cffi(
        f'https://www.douyin.com/search/{urllib.parse.quote(username)}?type=user',
        impersonate='chrome'
    )
    if code == 200 and len(body) > 5000:
        if username.lower() in body.lower():
            return {
                'platform': 'Douyin',
                'url': f'https://www.douyin.com/search/{urllib.parse.quote(username)}?type=user',
                '_needs_manual_verify': True,
                '_method': 'curl_cffi'
            }
    return None


def search_curl_cffi_weibo(username):
    """Search Weibo using curl_cffi."""
    # Try user search
    code, body = fetch_curl_cffi(
        f'https://s.weibo.com/user?q={urllib.parse.quote(username)}',
        impersonate='chrome'
    )
    if code == 200 and len(body) > 5000:
        if username.lower() in body.lower():
            return {
                'platform': 'Weibo',
                'url': f'https://s.weibo.com/user?q={urllib.parse.quote(username)}',
                '_needs_manual_verify': True,
                '_method': 'curl_cffi'
            }
    return None


def search_curl_cffi_qq(username):
    """Search QQ Zone using curl_cffi."""
    # Try QQ profile
    code, body = fetch_curl_cffi(
        f'https://user.qzone.qq.com/{username}',
        impersonate='chrome'
    )
    if code == 200 and len(body) > 3000:
        if username in body:
            return {
                'platform': 'QQ',
                'url': f'https://user.qzone.qq.com/{username}',
                '_needs_manual_verify': True,
                '_method': 'curl_cffi'
            }
    return None


def search_curl_cffi_tieba(username):
    """Search Baidu Tieba using curl_cffi."""
    # Try user profile
    code, body = fetch_curl_cffi(
        f'https://tieba.baidu.com/home/main?un={urllib.parse.quote(username)}',
        impersonate='chrome'
    )
    if code == 200 and len(body) > 5000:
        if username.lower() in body.lower():
            return {
                'platform': 'Tieba',
                'url': f'https://tieba.baidu.com/home/main?un={urllib.parse.quote(username)}',
                '_needs_manual_verify': True,
                '_method': 'curl_cffi'
            }
    return None


def search_curl_cffi_zhihu(username):
    """Search Zhihu using curl_cffi."""
    # Try user profile
    code, body = fetch_curl_cffi(
        f'https://www.zhihu.com/people/{username}',
        impersonate='chrome'
    )
    if code == 200 and len(body) > 5000:
        if username.lower() in body.lower():
            return {
                'platform': 'Zhihu',
                'url': f'https://www.zhihu.com/people/{username}',
                '_needs_manual_verify': True,
                '_method': 'curl_cffi'
            }
    return None


# ─── Unified search registry ───

CURL_CFFI_SEARCHERS = {
    'GitHub': search_curl_cffi_github,
    'Zhihu': search_curl_cffi_zhihu,
    'Douban': search_curl_cffi_douban,
    'Bilibili': search_curl_cffi_bilibili,
    'Reddit': search_curl_cffi_reddit,
    'Spotify': search_curl_cffi_spotify,
    'Steam': search_curl_cffi_steam,
    'LeetCodeCN': search_curl_cffi_leetcode_cn,
    'Gitee': search_curl_cffi_gitee,
    'CSDN': search_curl_cffi_csdn,
    'Juejin': search_curl_cffi_juejin,
    'HackerNews': search_curl_cffi_hackernews,
    'Xianyu': search_curl_cffi_xianyu,
    'Gaode': search_curl_cffi_gaode,
    'Xiaohongshu': search_curl_cffi_xiaohongshu,
    'Kuaishou': search_curl_cffi_kuaishou,
    'Douyin': search_curl_cffi_douyin,
    'Weibo': search_curl_cffi_weibo,
    'QQ': search_curl_cffi_qq,
    'Tieba': search_curl_cffi_tieba,
}

DRISSION_SEARCHERS = {
    'Dianping': search_drission_dianping,
}


def run_enhanced_search(username):
    """Run enhanced search using all anti-detection layers."""
    results = {}

    # Layer 1: curl_cffi for all platforms
    for platform, searcher in CURL_CFFI_SEARCHERS.items():
        try:
            result = searcher(username)
            if result:
                results[platform] = result
        except Exception:
            pass

    # Layer 2: DrissionPage for Chinese platforms
    for platform, searcher in DRISSION_SEARCHERS.items():
        try:
            result = searcher(username)
            if result:
                results[platform] = result
        except Exception:
            pass

    return results


if __name__ == '__main__':
    import sys
    username = sys.argv[1] if len(sys.argv) > 1 else 'zstsang'
    print(f"Running enhanced search for: {username}\n")
    results = run_enhanced_search(username)
    print(f"\nFound {len(results)} results:")
    for platform, data in results.items():
        url = data.get('url', '')
        extra = {k: v for k, v in data.items() if k not in ('platform', 'url', '_method', '_needs_manual_verify') and v}
        method = data.get('_method', '')
        print(f"  ✅ {platform}: {url}  [{method}]")
        if extra:
            print(f"     {extra}")
