#!/usr/bin/env python3
"""
Direct username search across platform APIs.
Bypasses Maigret's HTTP-based detection, uses APIs directly.
No login required.
"""

import json
import sys
import os
import ssl
import urllib.request
import urllib.parse
import re
import time
import asyncio

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def http_get(url, headers=None, timeout=15):
    h = {'User-Agent': UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        return e.code, ''
    except Exception:
        return 0, ''


def http_post(url, data, headers=None, timeout=15):
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
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode('utf-8', errors='ignore')
        except:
            return e.code, ''
    except Exception:
        return 0, ''


# ─── Search Functions ───

def search_netease(u):
    code, body = http_post('https://music.163.com/api/search/get',
        urllib.parse.urlencode({'s': u, 'type': '1002', 'limit': '5'}).encode(),
        {'Referer': 'https://music.163.com/'})
    if code != 200: return None
    try:
        d = json.loads(body)
        for user in d.get('result', {}).get('userprofiles', []):
            if user.get('nickname', '').lower() == u.lower():
                return {'platform': 'NeteaseCloudMusic', 'url': f"https://music.163.com/#/user/home?id={user['userId']}",
                        'nickname': user['nickname'], 'user_id': user['userId'],
                        'avatar': user.get('avatarUrl', '')}
    except: pass
    return None

def search_bilibili(u):
    code, body = http_get(f"https://api.bilibili.com/x/web-interface/search/type?search_type=bili_user&keyword={urllib.parse.quote(u)}",
        {'Referer': 'https://www.bilibili.com/'})
    if code != 200: return None
    try:
        d = json.loads(body)
        for r in d.get('data', {}).get('result', []) or []:
            if r.get('uname', '').lower() == u.lower():
                return {'platform': 'Bilibili', 'url': f"https://space.bilibili.com/{r['mid']}",
                        'nickname': r['uname'], 'user_id': r['mid'],
                        'fans': r.get('fans', 0), 'videos': r.get('videos', 0)}
    except: pass
    return None

def search_zhihu(u):
    code, body = http_get(f"https://www.zhihu.com/api/v4/search_v3?t=people&q={urllib.parse.quote(u)}")
    if code != 200: return None
    try:
        d = json.loads(body)
        for item in d.get('data', []):
            obj = item.get('object', {})
            if obj.get('url_token', '').lower() == u.lower():
                return {'platform': 'Zhihu', 'url': f"https://www.zhihu.com/people/{u}",
                        'nickname': obj.get('name', u),
                        'headline': obj.get('headline', '')}
    except: pass
    return None

def search_github(u):
    code, body = http_get(f"https://api.github.com/users/{u}")
    if code != 200: return None
    try:
        d = json.loads(body)
        if d.get('login'):
            return {'platform': 'GitHub', 'url': f"https://github.com/{u}",
                    'nickname': d.get('name') or d['login'],
                    'bio': d.get('bio', ''),
                    'followers': d.get('followers', 0),
                    'public_repos': d.get('public_repos', 0)}
    except: pass
    return None

def search_leetcode_cn(u):
    query = {"query": "query userPublicProfile($userSlug: String!) { userProfilePublicProfile(userSlug: $userSlug) { username realName siteRanking } }",
             "variables": {"userSlug": u}}
    code, body = http_post('https://leetcode.cn/graphql/', query,
        {'Content-Type': 'application/json', 'Referer': 'https://leetcode.cn/'})
    if code != 200: return None
    try:
        d = json.loads(body)
        p = d.get('data', {}).get('userProfilePublicProfile')
        if p and p.get('username'):
            return {'platform': 'LeetCodeCN', 'url': f"https://leetcode.cn/u/{u}/",
                    'nickname': p.get('realName') or p['username'],
                    'ranking': p.get('siteRanking')}
    except: pass
    return None

def search_csdn(u):
    code, body = http_get(f"https://so.csdn.net/api/v3/search?q={urllib.parse.quote(u)}&t=user")
    if code != 200: return None
    try:
        d = json.loads(body)
        for r in d.get('result_vos', []):
            if u.lower() in r.get('title', '').lower():
                return {'platform': 'CSDN', 'url': r.get('url', f"https://blog.csdn.net/{u}"),
                        'nickname': r.get('title', u)}
    except: pass
    return None

def search_juejin(u):
    code, body = http_get(f"https://api.juejin.cn/search_api/v1/search?query={urllib.parse.quote(u)}&cursor=0&limit=10&type=user")
    if code != 200: return None
    try:
        d = json.loads(body)
        for item in d.get('data', []):
            info = item.get('result_model', {})
            if info.get('user_name', '').lower() == u.lower():
                return {'platform': 'Juejin', 'url': f"https://juejin.cn/user/{info.get('user_id', u)}",
                        'nickname': info['user_name']}
    except: pass
    return None

def search_medium(u):
    code, body = http_get(f"https://medium.com/feed/@{u}")
    if code == 200 and '<channel>' in body:
        title = re.search(r'<title>(.*?)</title>', body)
        if title:
            # Clean CDATA and XML tags
            name = title.group(1)
            name = re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', name)
            name = name.replace('Stories by ', '').replace(' on Medium', '').strip()
            return {'platform': 'Medium', 'url': f"https://medium.com/@{u}",
                    'nickname': name}
    code, body = http_get(f"https://medium.com/@{u}")
    if code == 200 and '"isFollowing"' in body:
        return {'platform': 'Medium', 'url': f"https://medium.com/@{u}"}
    return None

def search_hackernews(u):
    code, body = http_get(f"https://hn.algolia.com/api/v1/search?query={urllib.parse.quote(u)}&type=users")
    if code != 200: return None
    try:
        d = json.loads(body)
        for h in d.get('hits', []):
            if h.get('author', '').lower() == u.lower():
                return {'platform': 'HackerNews', 'url': f"https://news.ycombinator.com/user?id={u}",
                        'nickname': h['author'], 'karma': h.get('karma', 0)}
    except: pass
    return None

def search_gitee(u):
    code, body = http_get(f"https://gitee.com/{u}")
    if code != 200: return None
    if '"login"' in body or '个人主页' in body:
        name = re.search(r'"name"\s*:\s*"([^"]+)"', body)
        return {'platform': 'Gitee', 'url': f"https://gitee.com/{u}",
                'nickname': name.group(1) if name else u}
    return None

def search_oschina(u):
    code, body = http_get(f"https://my.oschina.net/{u}")
    if code != 200 or len(body) < 10000: return None
    if 'user-info' in body or 'user-name' in body:
        return {'platform': 'OSChina', 'url': f"https://my.oschina.net/{u}"}
    return None

def search_keybase(u):
    code, body = http_get(f"https://keybase.io/_/api/1.0/user/lookup.json?usernames={u}")
    if code != 200: return None
    try:
        d = json.loads(body)
        them = d.get('them', [])
        if them and them[0]:
            return {'platform': 'Keybase', 'url': f"https://keybase.io/{u}",
                    'nickname': them[0].get('profile', {}).get('full_name', u)}
    except: pass
    return None

def search_reddit(u):
    code, body = http_get(f"https://www.reddit.com/user/{u}/about.json",
        {'User-Agent': 'osint-search/1.0'})
    if code != 200: return None
    try:
        d = json.loads(body)
        data = d.get('data', {})
        if data.get('name'):
            return {'platform': 'Reddit', 'url': f"https://www.reddit.com/user/{u}",
                    'nickname': data['name'],
                    'karma': data.get('total_karma', 0),
                    'created': data.get('created_utc', 0)}
    except: pass
    return None

def search_gitlab(u):
    code, body = http_get(f"https://gitlab.com/api/v4/users?username={u}")
    if code != 200: return None
    try:
        d = json.loads(body)
        for user in d:
            if user.get('username', '').lower() == u.lower():
                return {'platform': 'GitLab', 'url': f"https://gitlab.com/{u}",
                        'nickname': user.get('name', u),
                        'user_id': user.get('id')}
    except: pass
    return None

def search_spotify(u):
    # Spotify user profile check
    code, body = http_get(f"https://open.spotify.com/user/{u}")
    if code == 200 and '"profile"' in body:
        return {'platform': 'Spotify', 'url': f"https://open.spotify.com/user/{u}"}
    return None

def search_v2ex(u):
    code, body = http_get(f"https://www.v2ex.com/member/{u}")
    if code != 200: return None
    if '加入于' in body:
        return {'platform': 'V2EX', 'url': f"https://www.v2ex.com/member/{u}"}
    return None

def search_cnblogs(u):
    code, body = http_get(f"https://www.cnblogs.com/{u}/")
    if code != 200 or len(body) < 5000: return None
    if '博客园' in body:
        return {'platform': 'CNBlogs', 'url': f"https://www.cnblogs.com/{u}/"}
    return None

def search_steam(u):
    code, body = http_get(f"https://steamcommunity.com/id/{u}")
    if code != 200: return None
    if 'The specified profile could not be found' not in body:
        if 'steamid' in body or 'profile' in body:
            return {'platform': 'Steam', 'url': f"https://steamcommunity.com/id/{u}"}
    return None

def search_bing(username, site=None):
    """Search Bing for username, optionally restricted to a site."""
    if site:
        query = f'site:{site} "{username}"'
    else:
        query = f'"{username}"'
    url = f'https://www.bing.com/search?q={urllib.parse.quote(query)}'
    code, body = http_get(url)
    if code != 200:
        return []
    cites = re.findall(r'<cite>(.*?)</cite>', body)
    results = []
    for cite in cites:
        cite = cite.replace(' › ', '/').replace('https://', '').replace('http://', '')
        if username.lower() in cite.lower():
            results.append(cite)
    return results


def search_sogou(username, site=None):
    """Search Sogou for username."""
    if site:
        query = f'site:{site} "{username}"'
    else:
        query = f'"{username}"'
    url = f'https://www.sogou.com/web?query={urllib.parse.quote(query)}'
    code, body = http_get(url)
    if code != 200:
        return []
    results = []
    # Extract result URLs
    links = re.findall(r'href="(https?://[^"]+)"', body)
    for link in links:
        if username.lower() in link.lower():
            results.append(link)
    # Also check for username in text content
    text = re.sub(r'<[^>]+>', ' ', body)
    if username in text:
        # Find context
        for m in re.finditer(re.escape(username), text):
            ctx = text[max(0, m.start()-80):m.end()+80]
            if any(kw in ctx.lower() for kw in ['dianping', 'goofish', 'xianyu', 'amap', 'gaode', 'didi', 't3']):
                results.append(f'context:{ctx.strip()[:100]}')
    return results[:5]


def _load_cookie(platform):
    """Load saved cookie for a platform, auto-login if expired."""
    cookie_map = {
        'xianyu': 'xianyu.txt', 'goofish': 'xianyu.txt',
        'dianping': 'dianping.txt', 'gaode': 'gaode.txt', 'amap': 'gaode.txt',
        'didi': 'didi.txt', 't3': 't3.txt',
    }
    filename = cookie_map.get(platform)
    if not filename:
        return None

    # First try simple text cookie file
    cookie_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies', filename)
    if os.path.exists(cookie_file):
        with open(cookie_file) as f:
            cookie = f.read().strip()
        if cookie:
            return cookie

    # Try JSON cookie file from auto_platform
    json_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies', f'{platform}.json')
    if os.path.exists(json_file):
        try:
            with open(json_file) as f:
                data = json.load(f)
            if time.time() - data.get('saved_at', 0) < 86400:
                cookies = data.get('cookies', [])
                if cookies:
                    return '; '.join(f"{c['name']}={c['value']}" for c in cookies)
        except:
            pass

    # Try auto-login via auto_platform.py
    try:
        from auto_platform import ensure_login
        print(f"  [*] Auto-logging into {platform}...")
        cookies = asyncio.run(ensure_login(platform))
        if cookies:
            return '; '.join(f"{c['name']}={c['value']}" for c in cookies)
    except ImportError:
        pass
    except Exception as e:
        print(f"  [!] Auto-login failed for {platform}: {e}")

    return None


def search_xianyu(u):
    """闲鱼/Goofish: multi-method search with cookie support."""
    # Method 0: Cookie-based search
    cookie = _load_cookie('xianyu')
    if cookie:
        code, body = http_get(
            f'https://www.goofish.com/search?keyword={urllib.parse.quote(u)}',
            {'Cookie': cookie, 'Referer': 'https://www.goofish.com/'})
        if code == 200 and len(body) > 5000 and u.lower() in body.lower():
            text = re.sub(r'<[^>]+>', ' ', body)
            count = text.lower().count(u.lower())
            if count > 2:
                return {'platform': 'Xianyu',
                        'url': f'https://www.goofish.com/search?keyword={urllib.parse.quote(u)}',
                        '_needs_manual_verify': True,
                        'note': f'Found {count} mentions (cookie search)'}

    # Method 1: Direct Goofish web search page
    code, body = http_get(f'https://www.goofish.com/search?keyword={urllib.parse.quote(u)}')
    if code == 200 and len(body) > 5000:
        if u.lower() in body.lower():
            return {'platform': 'Xianyu',
                    'url': f'https://www.goofish.com/search?keyword={urllib.parse.quote(u)}',
                    '_needs_manual_verify': True,
                    'note': 'Username found in Goofish search page'}

    # Method 2: Bing site search
    results = search_bing(u, 'goofish.com')
    if results:
        return {'platform': 'Xianyu',
                'url': f'https://www.goofish.com/search?keyword={urllib.parse.quote(u)}',
                '_needs_manual_verify': True,
                'note': f'Found via Bing: {results[0]}'}

    # Method 3: Sogou site search
    results = search_sogou(u, 'goofish.com')
    if results:
        return {'platform': 'Xianyu',
                'url': f'https://www.goofish.com/search?keyword={urllib.parse.quote(u)}',
                '_needs_manual_verify': True,
                'note': f'Found via Sogou: {results[0][:50]}'}

    return None


def search_dianping(u):
    """大众点评: multi-method search with Playwright auto-login."""
    # Import Playwright automation
    try:
        import dianping_auto
    except ImportError:
        dianping_auto = None
    
    # Check if cookies are valid, auto-login if needed
    if dianping_auto:
        if not dianping_auto.cookies_valid():
            print("  [*] 大众点评Cookie已过期，启动自动登录...")
            try:
                asyncio.run(dianping_auto.login_with_playwright())
            except Exception as e:
                print(f"  [!] 自动登录失败: {e}")
    
    # Method 0: Cookie-based search (most reliable)
    cookie_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies', 'dianping.json')
    if os.path.exists(cookie_file):
        try:
            with open(cookie_file) as f:
                data = json.load(f)
            cookies = data.get('cookies', [])
            if cookies:
                cookie_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies])
                code, body = http_get(
                    f'https://www.dianping.com/search/keyword/1/0_{urllib.parse.quote(u)}',
                    {'Cookie': cookie_str, 'Referer': 'https://www.dianping.com/'})
                if code == 200 and len(body) > 10000 and '验证' not in body:
                    text = re.sub(r'<[^>]+>', ' ', body)
                    count = text.lower().count(u.lower())
                    if count > 2:
                        member_links = re.findall(r'/member/(\\d+)', body)
                        if member_links:
                            return {'platform': 'Dianping',
                                    'url': f'https://www.dianping.com/member/{member_links[0]}',
                                    'user_id': member_links[0]}
                        return {'platform': 'Dianping',
                                'url': f'https://www.dianping.com/search/keyword/1/0_{urllib.parse.quote(u)}',
                                '_needs_manual_verify': True,
                                'note': f'Found {count} mentions (cookie search)'}
        except Exception:
            pass

    # Method 1: Direct web search page
    code, body = http_get(f'https://www.dianping.com/search/keyword/1/0_{urllib.parse.quote(u)}')
    if code == 200 and u.lower() in body.lower():
        text = re.sub(r'<[^>]+>', ' ', body)
        user_ids = re.findall(r'/member/(\d+)', body)
        if user_ids:
            return {'platform': 'Dianping',
                    'url': f'https://www.dianping.com/member/{user_ids[0]}',
                    'user_id': user_ids[0]}
        count = text.lower().count(u.lower())
        if count > 2:
            return {'platform': 'Dianping',
                    'url': f'https://www.dianping.com/search/keyword/1/0_{urllib.parse.quote(u)}',
                    '_needs_manual_verify': True,
                    'note': f'Username appears {count} times in search results'}

    # Method 2: Mobile Dianping
    code, body = http_get(
        f'https://m.dianping.com/search/user?keyword={urllib.parse.quote(u)}',
        {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)'})
    if code == 200 and u.lower() in body.lower():
        return {'platform': 'Dianping',
                'url': f'https://www.dianping.com/search/keyword/1/0_{urllib.parse.quote(u)}',
                '_needs_manual_verify': True}

    # Method 3: Bing site search
    results = search_bing(u, 'dianping.com')
    if results:
        for r in results:
            user_match = re.search(r'member/(\d+)', r)
            if user_match:
                return {'platform': 'Dianping',
                        'url': f'https://www.dianping.com/member/{user_match.group(1)}',
                        'user_id': user_match.group(1)}
        return {'platform': 'Dianping',
                'url': f'https://www.dianping.com/search/keyword/1/0_{urllib.parse.quote(u)}',
                '_needs_manual_verify': True,
                'note': f'Found via Bing: {results[0]}'}

    return None


def search_gaode(u):
    """高德地图: multi-method search with cookie support."""
    # Method 0: Cookie-based search
    cookie = _load_cookie('gaode')
    if cookie:
        code, body = http_get(
            f'https://www.amap.com/search?query={urllib.parse.quote(u)}',
            {'Cookie': cookie})
        if code == 200 and len(body) > 5000:
            text = re.sub(r'<[^>]+>', ' ', body)
            if u.lower() in text.lower():
                return {'platform': 'Gaode',
                        'url': f'https://www.amap.com/search?query={urllib.parse.quote(u)}',
                        '_needs_manual_verify': True,
                        'note': 'Found via cookie search'}

    # Method 1: Amap web search
    code, body = http_get(f'https://www.amap.com/search?query={urllib.parse.quote(u)}')
    if code == 200 and len(body) > 5000:
        text = re.sub(r'<[^>]+>', ' ', body)
        if u.lower() in text.lower():
            return {'platform': 'Gaode',
                    'url': f'https://www.amap.com/search?query={urllib.parse.quote(u)}',
                    '_needs_manual_verify': True}

    # Method 2: Bing site search
    results = search_bing(u, 'amap.com')
    if results:
        return {'platform': 'Gaode',
                'url': f'https://www.amap.com/search?query={urllib.parse.quote(u)}',
                '_needs_manual_verify': True,
                'note': f'Found via Bing: {results[0]}'}

    return None


def search_didi(u):
    """滴滴出行: multi-method search with cookie support."""
    # Method 0: Cookie-based search
    cookie = _load_cookie('didi')
    if cookie:
        code, body = http_get(
            'https://www.didiglobal.com/',
            {'Cookie': cookie})
        if code == 200 and len(body) > 5000 and u.lower() in body.lower():
            return {'platform': 'DiDi',
                    'url': 'https://www.didiglobal.com/',
                    '_needs_manual_verify': True,
                    'note': 'Found via cookie search'}

    # Method 1: DiDi web presence check
    code, body = http_get(f'https://www.didiglobal.com/search?keyword={urllib.parse.quote(u)}')
    if code == 200 and u.lower() in body.lower():
        return {'platform': 'DiDi',
                'url': 'https://www.didiglobal.com/',
                '_needs_manual_verify': True}

    # Method 2: Bing site search across DiDi domains
    for domain in ['didiglobal.com', 'didi.com', 'didichuxing.com']:
        results = search_bing(u, domain)
        if results:
            return {'platform': 'DiDi',
                    'url': f'https://www.{domain}/',
                    '_needs_manual_verify': True,
                    'note': f'Found via Bing: {results[0]}'}

    # Method 3: Sogou search
    results = search_sogou(u, 'didi.com')
    if results:
        return {'platform': 'DiDi',
                'url': 'https://www.didiglobal.com/',
                '_needs_manual_verify': True,
                'note': f'Found via Sogou'}

    return None


def search_t3(u):
    """T3出行: multi-method search with cookie support."""
    # Method 0: Cookie-based search
    cookie = _load_cookie('t3')
    if cookie:
        code, body = http_get(
            'https://www.t3go.cn/',
            {'Cookie': cookie})
        if code == 200 and len(body) > 5000 and u.lower() in body.lower():
            return {'platform': 'T3',
                    'url': 'https://www.t3go.cn/',
                    '_needs_manual_verify': True,
                    'note': 'Found via cookie search'}

    # Method 1: T3 web presence check
    code, body = http_get(f'https://www.t3go.cn/')
    if code == 200 and u.lower() in body.lower():
        return {'platform': 'T3',
                'url': 'https://www.t3go.cn/',
                '_needs_manual_verify': True}

    # Method 2: Bing site search
    results = search_bing(u, 't3go.cn')
    if results:
        return {'platform': 'T3',
                'url': 'https://www.t3go.cn/',
                '_needs_manual_verify': True,
                'note': f'Found via Bing: {results[0]}'}

    # Method 3: Sogou search
    results = search_sogou(u, 't3go.cn')
    if results:
        return {'platform': 'T3',
                'url': 'https://www.t3go.cn/',
                '_needs_manual_verify': True,
                'note': f'Found via Sogou'}

    return None


def search_douyin(u):
    """Douyin has no public API, return search page for manual check."""
    return {'platform': 'Douyin', 'url': f"https://www.douyin.com/search/{urllib.parse.quote(u)}?type=user",
            '_needs_manual_verify': True}


def _get_weibo_visitor_cookie():
    """Get Weibo visitor SUB cookie (no login required)."""
    code, body = http_post('https://passport.weibo.com/visitor/genvisitor',
                           'cb=gen_callback&fp=%7B%7D')
    if code != 200:
        return None, None
    tid_m = re.search(r'"tid":"([^"]+)"', body)
    if not tid_m:
        return None, None
    tid = tid_m.group(1)
    code, body = http_get(
        f"https://passport.weibo.com/visitor/visitor?a=incarnate&t={tid}&w=2&c=095&gc=&cb=cross_domain&from=weibo&_rand=0.{int(time.time())}"
    )
    if code != 200:
        return None, None
    sub_m = re.search(r'"sub":"([^"]+)"', body)
    subp_m = re.search(r'"subp":"([^"]+)"', body)
    if not sub_m:
        return None, None
    return sub_m.group(1), subp_m.group(1) if subp_m else ''


def search_weibo(u):
    """Weibo: visitor token + s.weibo.com user search (no login required)."""
    sub, subp = _get_weibo_visitor_cookie()
    if not sub:
        return None
    cookie = f'SUB={sub}; SUBP={subp}'

    # Visit weibo.com first to establish session cookies
    http_get('https://weibo.com/', {'Cookie': cookie})

    # Use s.weibo.com user search (works with visitor cookie)
    code, body = http_get(
        f"https://s.weibo.com/user?q={urllib.parse.quote(u)}",
        {'Cookie': cookie})
    if code != 200 or len(body) < 1000:
        return None

    # Extract user cards: name -> profile URL
    cards = re.findall(
        r'<div class="info[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>([^<]*)</a>',
        body, re.DOTALL)
    for url, name in cards:
        if name.strip().lower() == u.lower():
            # Normalize URL
            if url.startswith('//'):
                url = 'https:' + url
            return {'platform': 'Weibo', 'url': url, 'nickname': name.strip()}

    return None


def search_xiaohongshu(u):
    """Search Xiaohongshu (Little Red Book) user profile."""
    # Try direct user profile page
    code, body = http_get(f'https://www.xiaohongshu.com/user/profile/{u}')
    if code == 200 and len(body) > 5000:
        if u.lower() in body.lower():
            return {'platform': 'Xiaohongshu', 'url': f'https://www.xiaohongshu.com/user/profile/{u}',
                    '_needs_manual_verify': True}
    return None


def search_kuaishou(u):
    """Search Kuaishou user profile."""
    # Try direct user profile page
    code, body = http_get(f'https://www.kuaishou.com/profile/{u}')
    if code == 200 and len(body) > 5000:
        if u.lower() in body.lower():
            return {'platform': 'Kuaishou', 'url': f'https://www.kuaishou.com/profile/{u}',
                    '_needs_manual_verify': True}
    return None


def search_qq(u):
    """Search QQ Zone user profile."""
    # Try QQ Zone profile page
    code, body = http_get(f'https://user.qzone.qq.com/{u}')
    if code == 200 and len(body) > 3000:
        if u in body:
            return {'platform': 'QQ', 'url': f'https://user.qzone.qq.com/{u}',
                    '_needs_manual_verify': True}
    return None


def search_tieba(u):
    """Search Baidu Tieba user profile."""
    # Try Tieba user profile page
    code, body = http_get(f'https://tieba.baidu.com/home/main?un={urllib.parse.quote(u)}')
    if code == 200 and len(body) > 5000:
        if u.lower() in body.lower():
            return {'platform': 'Tieba', 'url': f'https://tieba.baidu.com/home/main?un={urllib.parse.quote(u)}',
                    '_needs_manual_verify': True}
    return None


# ─── All searchers ───

ALL_SEARCHERS = [
    ('NeteaseCloudMusic', search_netease),
    ('Bilibili', search_bilibili),
    ('Zhihu', search_zhihu),
    ('GitHub', search_github),
    ('GitLab', search_gitlab),
    ('LeetCodeCN', search_leetcode_cn),
    ('CSDN', search_csdn),
    ('Juejin', search_juejin),
    ('Medium', search_medium),
    ('HackerNews', search_hackernews),
    ('Reddit', search_reddit),
    ('Gitee', search_gitee),
    ('OSChina', search_oschina),
    ('Keybase', search_keybase),
    ('Spotify', search_spotify),
    ('V2EX', search_v2ex),
    ('CNBlogs', search_cnblogs),
    ('Steam', search_steam),
    ('Weibo', search_weibo),
    ('Douyin', search_douyin),
    ('Xianyu', search_xianyu),
    ('Dianping', search_dianping),
    ('Gaode', search_gaode),
    ('DiDi', search_didi),
    ('T3', search_t3),
    ('Xiaohongshu', search_xiaohongshu),
    ('Kuaishou', search_kuaishou),
    ('QQ', search_qq),
    ('Tieba', search_tieba),
]


def deep_search(username):
    """Search username across all platforms using APIs."""
    print(f"\n{'='*55}")
    print(f"  Deep Search: {username}")
    print(f"  Searching {len(ALL_SEARCHERS)} platforms via API...")
    print(f"{'='*55}\n")

    found = []
    not_found = []

    for name, searcher in ALL_SEARCHERS:
        print(f"  [{name:20s}]", end=' ', flush=True)
        try:
            result = searcher(username)
            if result:
                if result.get('_needs_manual_verify'):
                    print(f"⚠️  {result['url']}")
                    found.append(result)
                else:
                    extras = {k: v for k, v in result.items()
                              if k not in ('platform', 'url', '_needs_manual_verify') and v}
                    extra_str = ' | '.join(f"{k}={v}" for k, v in extras.items())
                    print(f"✅ {result['url']}")
                    if extra_str:
                        print(f"  {'':22s}   {extra_str}")
                    found.append(result)
            else:
                print("❌")
                not_found.append(name)
        except Exception as e:
            print(f"⚠️  error: {e}")
            not_found.append(name)

    # Summary
    confirmed = [r for r in found if not r.get('_needs_manual_verify')]
    manual = [r for r in found if r.get('_needs_manual_verify')]

    # ─── Fallback: anti-detection layer for failed platforms ───
    if not_found:
        try:
            from anti_detect import CURL_CFFI_SEARCHERS, DRISSION_SEARCHERS
            all_fallback = {**CURL_CFFI_SEARCHERS, **DRISSION_SEARCHERS}
            retry_platforms = [n for n in not_found if n in all_fallback]

            if retry_platforms:
                print(f"\n  ─── Anti-Detection Fallback ({len(retry_platforms)} platforms) ───")
                still_failed = []
                for name in retry_platforms:
                    print(f"  [{name:20s}]", end=' ', flush=True)
                    try:
                        result = all_fallback[name](username)
                        if result:
                            if result.get('_needs_manual_verify'):
                                print(f"⚠️  {result['url']}  [anti-detect]")
                                found.append(result)
                                manual.append(result)
                            else:
                                extras = {k: v for k, v in result.items()
                                          if k not in ('platform', 'url', '_needs_manual_verify', '_method') and v}
                                extra_str = ' | '.join(f"{k}={v}" for k, v in extras.items())
                                print(f"✅ {result['url']}  [{result.get('_method','')}]")
                                if extra_str:
                                    print(f"  {'':22s}   {extra_str}")
                                found.append(result)
                                confirmed.append(result)
                            not_found.remove(name)
                        else:
                            print("❌")
                            still_failed.append(name)
                    except Exception as e:
                        print(f"⚠️  error: {e}")
                        still_failed.append(name)
        except ImportError:
            pass

    print(f"\n{'='*55}")
    print(f"  Results Summary")
    print(f"{'='*55}")
    print(f"  ✅ Confirmed (API verified): {len(confirmed)}")
    for r in confirmed:
        print(f"     {r['platform']}: {r['url']}")
    if manual:
        print(f"  ⚠️  Needs manual check:       {len(manual)}")
        for r in manual:
            print(f"     {r['platform']}: {r['url']}")
    print(f"  ❌ Not found:                 {len(not_found)}")
    print(f"{'='*55}\n")

    # Save report
    output_dir = os.path.dirname(os.path.abspath(__file__))
    results_dir = os.path.join(output_dir, 'results')
    os.makedirs(results_dir, exist_ok=True)

    report = {
        'username': username,
        'searched_platforms': len(ALL_SEARCHERS),
        'confirmed': confirmed,
        'needs_manual_check': manual,
        'not_found': not_found,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
    }

    json_path = os.path.join(results_dir, f'deep_{username}.json')
    with open(json_path, 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    txt_path = os.path.join(results_dir, f'deep_{username}.txt')
    with open(txt_path, 'w') as f:
        for r in found:
            f.write(f"{r['platform']}: {r['url']}\n")
        f.write(f"\nConfirmed: {len(confirmed)} | Manual check: {len(manual)} | Not found: {len(not_found)}\n")

    print(f"  Reports saved:")
    print(f"    {json_path}")
    print(f"    {txt_path}")
    print()

    return report


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 deep_search.py <username>")
        sys.exit(1)
    deep_search(sys.argv[1])
