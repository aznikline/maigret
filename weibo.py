#!/usr/bin/env python3
"""
Weibo public-data collector for the enrichment pipeline.

SCOPE — read-only, public/visible data only:
  - Any Weibo user's profile, follows (following), fans (followers), and
    public posts (statuses), using the calling account's login state.
  - NO private messages, NO writes, NO scraping of non-public content.

AUTH:
  - Cookies are loaded from cookies/weibo.json (same schema as the
    xiaohongshu.json convention: {"cookies":[{"name","value","domain",
    "path"}], "saved_at": ...}).
  - Without cookies, Weibo returns 432 / login-walls for nearly all identity
    data — every function then degrades gracefully and reports "needs login".

NOTE on the legacy visitor cookie flow: Weibo's public `genvisitor`/`incarnate`
endpoint no longer returns a tid anonymously, so there is no working
anonymous path. A login cookie is genuinely required.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import aiohttp

# Shared, long-term-reuse cookie store (expiry-aware, format-flexible).
import cookies as cookie_store

logger = logging.getLogger("weibo")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
TIMEOUT = aiohttp.ClientTimeout(total=20)


def load_cookie_string(domain: str = "weibo.cn") -> Optional[str]:
    """Return the Weibo cookie header for a target API domain, or None.

    Weibo's login state is split across .weibo.cn / .weibo.com / .sina.com.cn.
    The mobile API (m.weibo.cn) needs its OWN .weibo.cn cookies — sending the
    .weibo.com ones gets treated as logged-out. So we filter to the matching
    domain family.

    domain: 'weibo.cn' (mobile API) | 'weibo.com' (desktop) | '*' (all)
    """
    status = cookie_store.load("weibo")
    if status.ok:
        cookies = status.cookies
    else:
        if status.reason == "expired":
            logger.warning("weibo cookie expired (%s) — re-extract from Chrome",
                           cookie_store.age_label(status))
        elif status.reason == "no_file":
            logger.info("no cookies/weibo.json — run decrypt_chrome_cookies.py "
                        "(reads your logged-in Chrome)")
        elif status.reason == "empty":
            logger.warning("cookies/weibo.json present but empty")
        return None
    logger.debug("weibo cookie ok (%s)", cookie_store.age_label(status))
    if domain == "*":
        sel = cookies
    else:
        # match .weibo.cn or weibo.cn when domain='weibo.cn'
        sel = [c for c in cookies
               if c["domain"].lstrip(".").endswith(domain)]
    if not sel:
        logger.warning("no %s-domain cookies in store (have %s)",
                       domain, {c["domain"] for c in cookies})
        return None
    return "; ".join(f"{c['name']}={c['value']}" for c in sel)


def _headers(cookie: Optional[str]) -> Dict[str, str]:
    h = {"User-Agent": UA, "Accept": "application/json",
         "Referer": "https://m.weibo.cn/"}
    if cookie:
        h["Cookie"] = cookie
    return h


async def _get(session: aiohttp.ClientSession, url: str,
               cookie: Optional[str]) -> Optional[dict]:
    try:
        async with session.get(url, headers=_headers(cookie)) as resp:
            status = resp.status
            text = await resp.text()
    except Exception as e:
        logger.debug("weibo GET failed %s: %s", url, e)
        return None
    if status == 432:
        logger.warning("weibo 432 (login required) for %s", url)
        return {"_needs_login": True}
    if status != 200:
        logger.debug("weibo %s status %s", url, status)
        return None
    try:
        data = json.loads(text)
    except Exception:
        return {"_raw": text[:200]}
    # m.weibo.cn throttles list endpoints with a captcha challenge rather than
    # a rate-limit header: ok=-100 + a captcha URL. Detect it so callers stop
    # instead of treating the empty result as "user has no fans".
    if isinstance(data, dict) and data.get("ok") == -100 or data.get("errno") == "-100":
        logger.warning("weibo captcha triggered for %s — back off / slow down", url)
        return {"_captcha": True, "url": data.get("url", "")}
    return data


async def resolve_uid(session: aiohttp.ClientSession, login_or_uid: str,
                      cookie: Optional[str]) -> Optional[str]:
    """Turn a login name / URL into a numeric uid. Returns uid or None."""
    s = str(login_or_uid).strip()
    if s.isdigit():
        return s
    # /u/<id> or weibo.com/<id>
    tail = s.rstrip("/").split("/")[-1]
    if tail.isdigit():
        return tail
    # /<screen_name> -> resolve via search (needs login for full results,
    # but the search endpoint returns the matched uid).
    url = (f"https://m.weibo.cn/api/container/getIndex?"
           f"containerid=100103type%3D3%26q%3D{tail}&page_type=searchall")
    data = await _get(session, url, cookie)
    if not data or data.get("_needs_login"):
        return None
    for card in (data.get("data", {}) or {}).get("cards", []) or []:
        for g in card.get("card_group", []) or []:
            uid = (g.get("user", {}) or {}).get("id")
            if uid:
                return str(uid)
    return None


async def get_profile(session: aiohttp.ClientSession, uid: str,
                      cookie: Optional[str]) -> Optional[dict]:
    """Public profile: screen name, bio, follower/follow counts, location."""
    # profile containerid = 100505 + uid
    cid = uid if uid.startswith("100505") else f"100505{uid}"
    url = f"https://m.weibo.cn/api/container/getIndex?containerid={cid}"
    data = await _get(session, url, cookie)
    if not data or data.get("_needs_login"):
        return None
    info = (data.get("data", {}) or {}).get("userInfo", {}) or {}
    if not info:
        return None
    return {
        "uid": uid,
        "screen_name": info.get("screen_name", ""),
        "bio": info.get("description", ""),
        "location": info.get("location", ""),
        "gender": info.get("gender", ""),
        "followers_count": info.get("followers_count", 0),
        "follow_count": info.get("follow_count", 0),
        "statuses_count": info.get("statuses_count", 0),
        "profile_url": f"https://weibo.com/u/{uid}",
        "avatar": info.get("profile_image_url", ""),
    }


async def _get_follow_list(session: aiohttp.ClientSession, uid: str,
                            cookie: Optional[str], kind: str,
                            limit: int = 100) -> List[dict]:
    """kind = 'follows' (following) or 'fans' (followers). Public lists.

    Returns [{uid, screen_name}]. Needs login — degrades to [] otherwise.
    """
    cid = {"follows": f"2310517_-_follow_-_{uid}",
           "fans": f"2310517_-_fans_-_{uid}"}[kind]
    out: List[dict] = []
    page = 1
    while len(out) < limit:
        url = (f"https://m.weibo.cn/api/container/getIndex?"
               f"containerid={cid}&page={page}")
        data = await _get(session, url, cookie)
        if not data or data.get("_needs_login"):
            break
        cards = (data.get("data", {}) or {}).get("cards", []) or []
        added = 0
        for card in cards:
            # the card_group holds the user cards
            for g in card.get("card_group", []) or []:
                u = g.get("user") or {}
                if u.get("id"):
                    out.append({"uid": str(u["id"]),
                                "screen_name": u.get("screen_name", "")})
                    added += 1
            # some payloads put the user directly on card_group items
            if not card.get("card_group") and card.get("user", {}).get("id"):
                u = card["user"]
                out.append({"uid": str(u["id"]),
                            "screen_name": u.get("screen_name", "")})
                added += 1
        if added == 0:
            break
        page += 1
        time.sleep(0.3)  # be polite
    return out[:limit]


def get_follows(session, uid, cookie, limit=100):
    return _get_follow_list(session, uid, cookie, "follows", limit)


def get_fans(session, uid, cookie, limit=100):
    return _get_follow_list(session, uid, cookie, "fans", limit)


async def collect(session: aiohttp.ClientSession, uid: str,
                   limit: int = 100) -> Dict[str, Any]:
    """Collect a Weibo user's public footprint. Returns a result dict.

    If no cookie is present, only the fields that survive the login-wall are
    filled; everything else carries needs_login=True so callers can report it.
    """
    cookie = load_cookie_string()
    result: Dict[str, Any] = {"uid": uid, "has_login_cookie": bool(cookie)}

    profile = await get_profile(session, uid, cookie)
    if profile:
        result["profile"] = profile
    elif cookie is None:
        result["profile_needs_login"] = True

    if cookie:
        result["follows"] = await _get_follow_list(session, uid, cookie,
                                                    "follows", limit)
        result["fans"] = await _get_follow_list(session, uid, cookie,
                                                  "fans", limit)
    else:
        result["follows_needs_login"] = True
        result["fans_needs_login"] = True

    return result


async def verify_login(session: aiohttp.ClientSession) -> bool:
    """Health check: is the stored Weibo cookie actually logged in?

    This is what makes the tool honest about cookie state over the long run —
    it won't silently treat an expired session as 'no data found'.
    """
    cookie = load_cookie_string()
    if not cookie:
        return False
    # /api/config returns login status anonymously, but with a cookie we can
    # confirm by reading the self profile endpoint.
    data = await _get(session,
                      "https://m.weibo.cn/api/config", cookie)
    if not data or data.get("_needs_login"):
        return False
    login = (data.get("data", {}) or {}).get("login", False)
    return bool(login)


async def collect_relations(session: aiohttp.ClientSession, uid: str,
                             nickname: str = "", limit: int = 100):
    """Return a RelationResult (same shape as relations.py) for a Weibo user.

    This lets Weibo feed the SAME graph pipeline as Netease/GitHub: a person
    node with follows/fans as directed social edges. Importable by
    entity_enrich without rewriting the graph layer.
    """
    import relations as REL
    cookie = load_cookie_string()
    res = REL.RelationResult()
    if not cookie:
        res.errors.append("weibo: no login cookie (run weibo_login.py)")
        return res

    node_index = {n.user_id: n for n in res.nodes}
    edge_index = {e.endpoints(): e for e in res.edges}

    def ensure_node(wuid: str, name: str) -> REL.SocialNode:
        n = node_index.get(wuid)
        if n is None:
            n = REL.SocialNode(site="Weibo", user_id=str(wuid),
                               nickname=name or "")
            node_index[wuid] = n
            res.nodes.append(n)
        return n

    owner = ensure_node(uid, nickname)
    follows = await _get_follow_list(session, uid, cookie, "follows", limit)
    for f in follows:
        ensure_node(f["uid"], f["screen_name"])
        edge = REL.SocialEdge(site="Weibo", src=str(uid), dst=str(f["uid"]),
                              src_name=owner.nickname,
                              dst_name=f["screen_name"], weight=1)
        if edge.endpoints() not in edge_index:
            edge_index[edge.endpoints()] = edge
            res.edges.append(edge)
    return res


if __name__ == "__main__":
    import asyncio
    import sys

    async def _demo():
        target = sys.argv[1] if len(sys.argv) > 1 else "3842706324"
        cookie = load_cookie_string()
        print(f"cookie present: {bool(cookie)}")
        async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
            uid = await resolve_uid(s, target, cookie) or target
            print(f"resolved uid: {uid}")
            res = await collect(s, uid)
            print(json.dumps(res, ensure_ascii=False, indent=2))

    asyncio.run(_demo())
