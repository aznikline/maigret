#!/usr/bin/env python3
"""Legacy standalone social analyzer.

The authoritative identity-aware pipeline is ``entity_enrich.py``. It emits
typed collection coverage and ``social_network_<user>.json``. This module stays
available for compatibility with older direct invocations.
"""

import json
import os
import sys
import ssl
import time
import re
import urllib.request
import urllib.parse
from pathlib import Path

import networkx as nx
from pyvis.network import Network

BASE_DIR = Path(__file__).parent
RESULTS_DIR = BASE_DIR / "results"
COOKIE_DIR = BASE_DIR / "cookies"
RESULTS_DIR.mkdir(exist_ok=True)
COOKIE_DIR.mkdir(exist_ok=True)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def http_get_json(url, headers=None, timeout=15):
    """GET request that returns parsed JSON."""
    # Try curl_cffi first (bypasses rate limits and TLS fingerprinting)
    try:
        from curl_cffi import requests as cffi_requests
        h = headers or {}
        resp = cffi_requests.get(url, headers=h, timeout=timeout, impersonate='chrome')
        if resp.status_code == 200:
            return resp.json()
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback to urllib
    h = {'User-Agent': UA}
    if headers:
        h.update(headers)

    # Add GitHub token if available
    if 'github.com' in url and 'Authorization' not in h:
        token = os.environ.get('GITHUB_TOKEN', '')
        if token:
            h['Authorization'] = f'token {token}'

    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode('utf-8', errors='ignore'))
    except Exception:
        return None


# ─── Platform Data Extractors ───

def extract_github(username, max_pages=3):
    """Extract GitHub followers and following."""
    print(f"  [GitHub] Extracting relationships for {username}...")
    result = {'followers': [], 'following': [], 'stars': [], 'repos': []}

    # Check user exists
    user_data = http_get_json(f'https://api.github.com/users/{username}')
    if not user_data or 'login' not in user_data:
        print(f"  [GitHub] User not found")
        return None

    result['profile'] = {
        'name': user_data.get('name', ''),
        'bio': user_data.get('bio', ''),
        'company': user_data.get('company', ''),
        'location': user_data.get('location', ''),
        'blog': user_data.get('blog', ''),
        'avatar': user_data.get('avatar_url', ''),
        'followers_count': user_data.get('followers', 0),
        'following_count': user_data.get('following', 0),
        'public_repos': user_data.get('public_repos', 0),
    }

    # Followers
    for page in range(1, max_pages + 1):
        data = http_get_json(f'https://api.github.com/users/{username}/followers?per_page=100&page={page}')
        if not data:
            break
        for u in data:
            result['followers'].append({
                'username': u['login'],
                'avatar': u.get('avatar_url', ''),
                'url': u.get('html_url', ''),
            })
        if len(data) < 100:
            break
        time.sleep(0.5)

    # Following
    for page in range(1, max_pages + 1):
        data = http_get_json(f'https://api.github.com/users/{username}/following?per_page=100&page={page}')
        if not data:
            break
        for u in data:
            result['following'].append({
                'username': u['login'],
                'avatar': u.get('avatar_url', ''),
                'url': u.get('html_url', ''),
            })
        if len(data) < 100:
            break
        time.sleep(0.5)

    # Starred repos (shows interests)
    data = http_get_json(f'https://api.github.com/users/{username}/starred?per_page=30')
    if data:
        for repo in data[:30]:
            result['stars'].append({
                'name': repo.get('full_name', ''),
                'language': repo.get('language', ''),
                'description': repo.get('description', ''),
            })

    print(f"  [GitHub] ✅ {len(result['followers'])} followers, {len(result['following'])} following")
    return result


def extract_bilibili(uid=None, username=None):
    """Extract Bilibili followers and following."""
    if uid is None and username:
        # Search for user by keyword
        search_url = f'https://api.bilibili.com/x/web-interface/search/type?search_type=bili_user&keyword={urllib.parse.quote(username)}'
        data = http_get_json(search_url, {'Referer': 'https://www.bilibili.com/'})
        if not data or data.get('code') != 0:
            return None
        results = data.get('data', {}).get('result', [])
        if not results:
            return None
        # Find exact match
        for r in results:
            if r.get('uname', '').lower() == username.lower():
                uid = r['mid']
                break
        if uid is None:
            uid = results[0]['mid']

    if not uid:
        return None

    print(f"  [Bilibili] Extracting relationships for UID {uid}...")
    result = {'followers': [], 'following': [], 'uid': uid}

    # User info
    info_data = http_get_json(f'https://api.bilibili.com/x/space/wbi/acc/info?mid={uid}',
                              {'Referer': 'https://www.bilibili.com/'})
    if info_data and info_data.get('code') == 0:
        info = info_data.get('data', {})
        result['profile'] = {
            'name': info.get('name', ''),
            'sign': info.get('sign', ''),
            'avatar': info.get('face', ''),
            'level': info.get('level', 0),
            'sex': info.get('sex', ''),
        }

    # Followers - try mobile API first (less restricted), then web API
    for api_url in [
        f'https://app.bilibili.com/x/v2/relation/follower?vmid={uid}&pn=1&ps=50',
        f'https://api.bilibili.com/x/relation/followers?vmid={uid}&pn=1&ps=50',
    ]:
        data = http_get_json(api_url, {'Referer': 'https://www.bilibili.com/'})
        if data and data.get('code') == 0:
            follower_list = data.get('data', {}).get('list', [])
            if not follower_list and 'items' in data.get('data', {}):
                follower_list = data['data']['items']
            for u in follower_list:
                result['followers'].append({
                    'username': u.get('uname', u.get('name', '')),
                    'uid': u.get('mid', u.get('fid', 0)),
                    'avatar': u.get('face', u.get('avatar', '')),
                    'sign': u.get('sign', ''),
                })
            if follower_list:
                break

    # Following - try mobile API first
    for api_url in [
        f'https://app.bilibili.com/x/v2/relation/following?vmid={uid}&pn=1&ps=50',
        f'https://api.bilibili.com/x/relation/followings?vmid={uid}&pn=1&ps=50',
    ]:
        data = http_get_json(api_url, {'Referer': 'https://www.bilibili.com/'})
        if data and data.get('code') == 0:
            following_list = data.get('data', {}).get('list', [])
            if not following_list and 'items' in data.get('data', {}):
                following_list = data['data']['items']
            for u in following_list:
                result['following'].append({
                    'username': u.get('uname', u.get('name', '')),
                    'uid': u.get('mid', u.get('fid', 0)),
                    'avatar': u.get('face', u.get('avatar', '')),
                    'sign': u.get('sign', ''),
                })
            if following_list:
                break

    # Also get recent videos (shows content/interests)
    vid_data = http_get_json(
        f'https://api.bilibili.com/x/space/wbi/arc/search?mid={uid}&ps=10&pn=1',
        {'Referer': 'https://www.bilibili.com/'})
    if vid_data and vid_data.get('code') == 0:
        for v in vid_data.get('data', {}).get('list', {}).get('vlist', [])[:10]:
            result.setdefault('videos', []).append({
                'title': v.get('title', ''),
                'bvid': v.get('bvid', ''),
                'play': v.get('play', 0),
                'description': v.get('description', ''),
            })

    print(f"  [Bilibili] ✅ {len(result['followers'])} followers, {len(result['following'])} following")
    return result


def extract_weibo(uid=None, username=None):
    """Extract Weibo followers and following using visitor cookie."""
    # Get visitor cookie
    sub, subp = _get_weibo_visitor_cookie()
    if not sub:
        print("  [Weibo] Failed to get visitor cookie")
        return None

    cookie = f'SUB={sub}; SUBP={subp}'
    headers = {'Cookie': cookie, 'Referer': 'https://weibo.com/'}

    # If we have username but no uid, search for uid
    if uid is None and username:
        found_uid = _weibo_search_user(username, cookie)
        if found_uid:
            uid = found_uid

    if not uid:
        return None

    print(f"  [Weibo] Extracting relationships for UID {uid}...")
    result = {'followers': [], 'following': [], 'uid': uid}

    # User info
    data = http_get_json(f'https://weibo.com/ajax/profile/info?uid={uid}', headers)
    if data and data.get('ok') == 1:
        user = data.get('data', {}).get('user', {})
        result['profile'] = {
            'name': user.get('screen_name', ''),
            'description': user.get('description', ''),
            'avatar': user.get('avatar_large', ''),
            'followers_count': user.get('followers_count', 0),
            'friends_count': user.get('friends_count', 0),
            'statuses_count': user.get('statuses_count', 0),
            'verified': user.get('verified', False),
            'verified_reason': user.get('verified_reason', ''),
        }

    # Followers (limited by visitor cookie)
    data = http_get_json(f'https://weibo.com/ajax/friendships/friends?page=1&uid={uid}', headers)
    if data and 'users' in data:
        for u in data['users'][:50]:
            result['followers'].append({
                'username': u.get('screen_name', ''),
                'uid': u.get('id', 0),
                'avatar': u.get('avatar_large', ''),
                'description': u.get('description', ''),
            })

    # Following
    data = http_get_json(f'https://weibo.com/ajax/friendships/friends?page=1&uid={uid}&type=friend', headers)
    if data and 'users' in data:
        for u in data['users'][:50]:
            result['following'].append({
                'username': u.get('screen_name', ''),
                'uid': u.get('id', 0),
                'avatar': u.get('avatar_large', ''),
                'description': u.get('description', ''),
            })

    print(f"  [Weibo] ✅ {len(result['followers'])} followers, {len(result['following'])} following")
    return result


def _get_weibo_visitor_cookie():
    """Get Weibo visitor SUB cookie."""
    h = {'User-Agent': UA}
    req = urllib.request.Request('https://passport.weibo.com/visitor/genvisitor',
                                 data=b'cb=gen_callback&fp=%7B%7D', headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            body = resp.read().decode()
    except:
        return None, None
    tid_m = re.search(r'"tid":"([^"]+)"', body)
    if not tid_m:
        return None, None
    tid = tid_m.group(1)
    req = urllib.request.Request(
        f"https://passport.weibo.com/visitor/visitor?a=incarnate&t={tid}&w=2&c=095&gc=&cb=cross_domain&from=weibo&_rand=0.{int(time.time())}",
        headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            body = resp.read().decode()
    except:
        return None, None
    sub_m = re.search(r'"sub":"([^"]+)"', body)
    subp_m = re.search(r'"subp":"([^"]+)"', body)
    if not sub_m:
        return None, None
    return sub_m.group(1), subp_m.group(1) if subp_m else ''


def _weibo_search_user(username, cookie):
    """Search for Weibo user by username, return uid."""
    headers = {'Cookie': cookie}
    req = urllib.request.Request(
        f'https://s.weibo.com/user?q={urllib.parse.quote(username)}',
        headers={**headers, 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        cards = re.findall(
            r'<div class="info[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>([^<]*)</a>',
            html, re.DOTALL)
        for url, name in cards:
            if name.strip().lower() == username.lower():
                uid_m = re.search(r'/u/(\d+)', url)
                if uid_m:
                    return int(uid_m.group(1))
        # Try to extract any uid
        uids = re.findall(r'/u/(\d+)', html)
        if uids:
            return int(uids[0])
    except:
        pass
    return None


def extract_netease(username):
    """Extract NetEase Cloud Music playlists and followers."""
    print(f"  [NetEase] Extracting data for {username}...")

    # Search for user
    data = None
    try:
        encoded = urllib.parse.urlencode({'s': username, 'type': '1002', 'limit': '5'}).encode()
        req = urllib.request.Request('https://music.163.com/api/search/get',
                                     data=encoded,
                                     headers={'User-Agent': UA, 'Referer': 'https://music.163.com/'})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except:
        pass

    if not data:
        return None

    users = data.get('result', {}).get('userprofiles', [])
    target = None
    for u in users:
        if u.get('nickname', '').lower() == username.lower():
            target = u
            break
    if not target and users:
        target = users[0]
    if not target:
        return None

    uid = target['userId']
    result = {
        'uid': uid,
        'profile': {
            'name': target.get('nickname', ''),
            'avatar': target.get('avatarUrl', ''),
            'signature': target.get('signature', ''),
            'followeds': target.get('followeds', 0),
            'follows': target.get('follows', 0),
        },
        'followers': [],
        'following': [],
        'playlists': [],
    }

    # User playlists
    try:
        req = urllib.request.Request(f'https://music.163.com/api/user/playlist?uid={uid}&limit=30&offset=0',
                                     headers={'User-Agent': UA, 'Referer': 'https://music.163.com/'})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            pl_data = json.loads(resp.read().decode('utf-8'))
        for pl in pl_data.get('playlist', [])[:20]:
            result['playlists'].append({
                'name': pl.get('name', ''),
                'id': pl.get('id', 0),
                'track_count': pl.get('trackCount', 0),
                'play_count': pl.get('playCount', 0),
            })
    except:
        pass

    print(f"  [NetEase] ✅ {result['profile'].get('followeds', 0)} followers, {len(result['playlists'])} playlists")
    return result


# ─── Graph Builder ───

def build_social_graph(target_username, platform_data):
    """Build a NetworkX graph from extracted platform data."""
    G = nx.DiGraph()

    # Central node: the target user
    # Build a rich title from all available profiles
    profile_parts = []
    for platform, data in platform_data.items():
        if data and data.get('profile'):
            p = data['profile']
            name = p.get('name', '')
            bio = p.get('bio', p.get('sign', p.get('signature', p.get('description', ''))))
            if name:
                profile_parts.append(f"{platform}: {name}")
            if bio:
                profile_parts.append(f"  └ {bio[:80]}")

    central_title = f"🔍 {target_username}\n" + "\n".join(profile_parts)

    G.add_node(target_username,
               type='target',
               platform='all',
               label=target_username,
               color='#FF4444',
               size=45,
               shape='star',
               title=central_title)

    for platform, data in platform_data.items():
        if not data:
            continue

        profile = data.get('profile', {})
        platform_node = f"{target_username}@{platform}"

        # Build rich tooltip
        tooltip_parts = [f"📌 {platform}"]
        for k, v in profile.items():
            if v and k not in ('avatar', 'avatarUrl', 'avatar_large'):
                label = k.replace('_', ' ').title()
                tooltip_parts.append(f"  {label}: {str(v)[:100]}")
        tooltip_parts.append(f"\n  Followers: {profile.get('followers_count', profile.get('followeds', len(data.get('followers', []))))}")
        tooltip_parts.append(f"  Following: {profile.get('following_count', profile.get('friends_count', profile.get('follows', len(data.get('following', [])))))}")

        display_name = profile.get('name', target_username)

        G.add_node(platform_node,
                   type='platform_account',
                   platform=platform,
                   label=f"{display_name}\n({platform})",
                   color=_platform_color(platform),
                   size=28,
                   shape='dot',
                   title="\n".join(tooltip_parts))

        # Link target to platform account
        G.add_edge(target_username, platform_node,
                   label='has account',
                   color='#AAAAAA',
                   weight=5,
                   width=2)

        # Add followers
        for i, f in enumerate(data.get('followers', [])[:30]):
            fname = f.get('username', f.get('name', ''))
            if not fname:
                continue
            node_id = f"{fname}@{platform}"
            if node_id not in G:
                G.add_node(node_id,
                           type='follower',
                           platform=platform,
                           label=fname,
                           color=_platform_color(platform, alpha=0.6),
                           size=12,
                           shape='dot',
                           title=f"👤 Follower on {platform}\n{json.dumps(f, ensure_ascii=False)[:200]}")
            G.add_edge(node_id, platform_node,
                       label='follows',
                       color=_platform_color(platform, alpha=0.3),
                       weight=1,
                       width=0.5)

        # Add following
        for i, f in enumerate(data.get('following', [])[:30]):
            fname = f.get('username', f.get('name', ''))
            if not fname:
                continue
            node_id = f"{fname}@{platform}"
            if node_id not in G:
                G.add_node(node_id,
                           type='following',
                           platform=platform,
                           label=fname,
                           color=_platform_color(platform, alpha=0.8),
                           size=14,
                           shape='triangle',
                           title=f"👤 Following on {platform}\n{json.dumps(f, ensure_ascii=False)[:200]}")
            G.add_edge(platform_node, node_id,
                       label='follows',
                       color=_platform_color(platform, alpha=0.3),
                       weight=1,
                       width=0.5)

        # Add playlists (NetEase)
        for pl in data.get('playlists', [])[:5]:
            pl_name = pl.get('name', 'Unknown')
            pl_node = f"playlist:{pl.get('id', pl_name)}"
            if pl_node not in G:
                G.add_node(pl_node,
                           type='content',
                           platform=platform,
                           label=f"🎵 {pl_name[:20]}",
                           color='#FF69B4',
                           size=10,
                           shape='diamond',
                           title=f"🎵 Playlist on {platform}\nName: {pl_name}\nTracks: {pl.get('track_count', '?')}\nPlays: {pl.get('play_count', '?')}")
                G.add_edge(platform_node, pl_node,
                           label='created',
                           color='#FF69B466',
                           weight=0.5,
                           width=0.5)

        # Add videos (Bilibili)
        for vid in data.get('videos', [])[:5]:
            vid_title = vid.get('title', 'Unknown')
            vid_node = f"video:{vid.get('bvid', vid_title)}"
            if vid_node not in G:
                G.add_node(vid_node,
                           type='content',
                           platform=platform,
                           label=f"📺 {vid_title[:15]}",
                           color='#00A1D6',
                           size=10,
                           shape='diamond',
                           title=f"📺 Video on {platform}\nTitle: {vid_title}\nPlays: {vid.get('play', '?')}")
                G.add_edge(platform_node, vid_node,
                           label='uploaded',
                           color='#00A1D666',
                           weight=0.5,
                           width=0.5)

        # Add starred repos (GitHub)
        for star in data.get('stars', [])[:10]:
            star_name = star.get('name', 'Unknown')
            star_node = f"star:{star_name}"
            if star_node not in G:
                lang = star.get('language', '')
                desc = star.get('description', '')
                G.add_node(star_node,
                           type='content',
                           platform=platform,
                           label=f"⭐ {star_name.split('/')[-1][:15]}",
                           color='#F0E68C',
                           size=8,
                           shape='diamond',
                           title=f"⭐ Starred on {platform}\nRepo: {star_name}\nLanguage: {lang}\n{desc[:100] if desc else ''}")
                G.add_edge(platform_node, star_node,
                           label='starred',
                           color='#F0E68C66',
                           weight=0.3,
                           width=0.3)

    # Detect cross-platform connections (same username on different platforms)
    nodes_by_name = {}
    for node in G.nodes():
        if '@' in node and node.count('@') == 1:
            name = node.split('@')[0]
            if name not in nodes_by_name:
                nodes_by_name[name] = []
            nodes_by_name[name].append(node)

    for name, nodes in nodes_by_name.items():
        if len(nodes) > 1:
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    G.add_edge(nodes[i], nodes[j],
                               label='same user?',
                               color='#FFAA00',
                               weight=3,
                               width=2,
                               dashes=True)

    # Detect mutual followers/following (bidirectional connections)
    for u, v in list(G.edges()):
        if G.has_edge(v, u) and u != target_username and v != target_username:
            # Mutual connection detected
            G.edges[u, v]['label'] = 'mutual'
            G.edges[u, v]['color'] = '#00FF88'
            G.edges[u, v]['width'] = 2

    return G


def _platform_color(platform, alpha=1.0):
    """Return color for a platform."""
    colors = {
        'GitHub': '#24292e',
        'Bilibili': '#00A1D6',
        'Weibo': '#E6162D',
        'NetEase': '#C20C0C',
        'Zhihu': '#0066FF',
        'Douyin': '#161823',
        'Xiaohongshu': '#FE2C55',
    }
    return colors.get(platform, '#666666')


def compute_graph_metrics(G):
    """Compute centrality and community metrics."""
    metrics = {}

    # Basic stats
    metrics['nodes'] = G.number_of_nodes()
    metrics['edges'] = G.number_of_edges()
    metrics['density'] = nx.density(G)

    if metrics['nodes'] > 1:
        # Degree centrality
        metrics['degree_centrality'] = nx.degree_centrality(G)

        # Betweenness centrality (bridge nodes)
        try:
            metrics['betweenness_centrality'] = nx.betweenness_centrality(G)
        except:
            pass

        # Top connected nodes
        top_degree = sorted(metrics.get('degree_centrality', {}).items(),
                            key=lambda x: x[1], reverse=True)[:10]
        metrics['top_nodes'] = [(node, round(score, 4)) for node, score in top_degree]

    return metrics


def visualize_graph(G, target_username, metrics=None):
    """Create interactive HTML visualization using pyvis."""
    net = Network(height="800px", width="100%",
                  bgcolor="#1a1a2e",
                  font_color="white",
                  directed=True,
                  notebook=False)

    # Configure physics
    net.barnes_hut(gravity=-3000,
                   central_gravity=0.3,
                   spring_length=150,
                   spring_strength=0.05,
                   damping=0.09,
                   overlap=0)

    # Add nodes with attributes
    for node in G.nodes():
        attrs = G.nodes[node]
        net.add_node(node,
                     label=attrs.get('label', node),
                     title=attrs.get('title', node),
                     color=attrs.get('color', '#666666'),
                     size=attrs.get('size', 15),
                     shape=attrs.get('shape', 'dot'))

    # Add edges with attributes
    for src, dst in G.edges():
        attrs = G.edges[src, dst]
        net.add_edge(src, dst,
                     label=attrs.get('label', ''),
                     color=attrs.get('color', '#444444'),
                     weight=attrs.get('weight', 1),
                     dashes=attrs.get('dashes', False))

    # Add info panel
    if metrics:
        info_html = f"""
        <div style="position:fixed; top:10px; left:10px; background:rgba(0,0,0,0.8);
                    padding:15px; border-radius:8px; color:white; font-size:13px;
                    max-width:350px; z-index:999;">
          <h3 style="margin:0 0 8px 0; color:#FF6B6B;">🔍 {target_username} 社会关系图谱</h3>
          <div>节点数: {metrics.get('nodes', 0)}</div>
          <div>连接数: {metrics.get('edges', 0)}</div>
          <div>密度: {round(metrics.get('density', 0), 4)}</div>
          <hr style="border-color:#444;">
          <b>关键节点 (度中心性):</b><br>
          {''.join(f'<div style="font-size:11px;">• {n[0]}: {n[1]}</div>' for n in metrics.get('top_nodes', [])[:5])}
        </div>
        """
        net.add_node('__info__',
                     label='',
                     shape='dot',
                     size=0,
                     color='transparent',
                     physics=False,
                     x=-9999, y=-9999)

    # Generate HTML
    output_path = RESULTS_DIR / f"graph_{target_username}.html"
    net.write_html(str(output_path), notebook=False)

    # Inject custom styles
    with open(output_path, 'r') as f:
        html = f.read()

    custom_css = """
    <style>
      body { background: #1a1a2e; margin: 0; }
      #info-panel {
        position: fixed; top: 10px; left: 10px;
        background: rgba(0,0,0,0.85); padding: 15px;
        border-radius: 8px; color: white; font-size: 13px;
        max-width: 350px; z-index: 999;
        border: 1px solid #333;
      }
      #info-panel h3 { margin: 0 0 8px 0; color: #FF6B6B; }
      #info-panel hr { border-color: #444; }
    </style>
    """

    info_panel = f"""
    <div id="info-panel">
      <h3>🔍 {target_username} 社会关系图谱</h3>
      <div>📊 节点: {metrics.get('nodes', 0)} | 连接: {metrics.get('edges', 0)}</div>
      <div>📈 密度: {round(metrics.get('density', 0), 4)}</div>
      <hr>
      <b>🔑 关键节点:</b><br>
      {''.join(f'<div style="font-size:11px; margin:2px 0;">• {n[0]}: {n[1]}</div>' for n in (metrics.get('top_nodes', []) or [])[:5])}
      <hr>
      <b>📌 图例:</b><br>
      <div style="font-size:11px;">
        <span style="color:#24292e;">●</span> GitHub &nbsp;
        <span style="color:#00A1D6;">●</span> B站 &nbsp;
        <span style="color:#E6162D;">●</span> 微博<br>
        <span style="color:#C20C0C;">●</span> 网易云 &nbsp;
        <span style="color:#FF4444;">★</span> 目标用户
      </div>
    </div>
    """

    html = html.replace('</head>', custom_css + '</head>')
    html = html.replace('<body>', '<body>' + info_panel)

    with open(output_path, 'w') as f:
        f.write(html)

    return str(output_path)


# ─── Main Analysis Pipeline ───

def analyze(username, platforms=None):
    """
    Full analysis pipeline:
    1. Find accounts across platforms
    2. Extract relationship data
    3. Build social graph
    4. Compute metrics
    5. Visualize
    """
    print(f"\n{'='*60}")
    print(f"  🔍 Social Relationship Analysis: {username}")
    print(f"{'='*60}\n")

    if platforms is None:
        platforms = ['github', 'bilibili', 'weibo', 'netease']

    platform_data = {}

    # Step 1: Extract data from each platform
    print("[Step 1] Extracting relationship data from platforms...\n")

    if 'github' in platforms:
        try:
            platform_data['GitHub'] = extract_github(username)
        except Exception as e:
            print(f"  [GitHub] ❌ Error: {e}")

    if 'bilibili' in platforms:
        try:
            platform_data['Bilibili'] = extract_bilibili(username=username)
        except Exception as e:
            print(f"  [Bilibili] ❌ Error: {e}")

    if 'weibo' in platforms:
        try:
            platform_data['Weibo'] = extract_weibo(username=username)
        except Exception as e:
            print(f"  [Weibo] ❌ Error: {e}")

    if 'netease' in platforms:
        try:
            platform_data['NetEase'] = extract_netease(username)
        except Exception as e:
            print(f"  [NetEase] ❌ Error: {e}")

    # Count found data
    total_followers = sum(len(d.get('followers', [])) for d in platform_data.values() if d)
    total_following = sum(len(d.get('following', [])) for d in platform_data.values() if d)
    total_profiles = sum(1 for d in platform_data.values() if d and d.get('profile'))

    print(f"\n[Step 2] Building social graph...")
    print(f"  Profiles found: {total_profiles}")
    print(f"  Total relationships: {total_followers} followers + {total_following} following\n")

    if total_profiles == 0 and total_followers + total_following == 0:
        print("  ⚠️  No data found on any platform.")
        print("    - Check if username is correct")
        print("    - GitHub: most reliable (public API)")
        print("    - Bilibili/Weibo: may need login cookies")
        return None

    # Step 2: Build graph
    G = build_social_graph(username, platform_data)

    # Step 3: Compute metrics
    print("[Step 3] Computing graph metrics...")
    metrics = compute_graph_metrics(G)
    print(f"  Nodes: {metrics['nodes']}, Edges: {metrics['edges']}, Density: {round(metrics['density'], 4)}")
    if metrics.get('top_nodes'):
        print(f"  Top nodes:")
        for node, score in metrics['top_nodes'][:5]:
            print(f"    {node}: {score}")

    # Step 4: Visualize
    print(f"\n[Step 4] Generating interactive visualization...")
    output_path = visualize_graph(G, username, metrics)
    print(f"  ✅ Saved to: {output_path}")

    # Step 5: Save raw data
    report = {
        'username': username,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'platforms': {k: v for k, v in platform_data.items() if v},
        'metrics': {k: v for k, v in metrics.items() if k != 'degree_centrality' and k != 'betweenness_centrality'},
    }

    json_path = RESULTS_DIR / f"social_{username}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  ✅ Raw data saved to: {json_path}")

    # Summary
    print(f"\n{'='*60}")
    print(f"  📊 Analysis Summary: {username}")
    print(f"{'='*60}")
    print(f"  Platforms analyzed: {sum(1 for v in platform_data.values() if v)}")
    print(f"  Total followers found: {total_followers}")
    print(f"  Total following found: {total_following}")
    print(f"  Graph nodes: {metrics['nodes']}")
    print(f"  Graph edges: {metrics['edges']}")
    print(f"  Graph density: {round(metrics['density'], 4)}")
    print(f"\n  Open the graph: open {output_path}")
    print(f"{'='*60}\n")

    return {
        'graph': output_path,
        'data': json_path,
        'metrics': metrics,
        'platform_data': platform_data,
    }


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Social Relationship Analyzer')
    parser.add_argument('username', help='Username to analyze')
    parser.add_argument('--platforms', nargs='+', default=['github', 'bilibili', 'weibo', 'netease'],
                        help='Platforms to analyze (default: github bilibili weibo netease)')
    args = parser.parse_args()

    analyze(args.username, args.platforms)
