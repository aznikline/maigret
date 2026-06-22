#!/usr/bin/env python3
"""
Deep Weibo social-graph mining: pull a user's fans/follows WITH per-user
profile data, detect mutual ties, and emit a RelationResult the same graph
pipeline already consumes.

Why this module is separate from weibo.py:
  weibo.py is a thin reader (profile / one follow list). Mining a *network*
  needs richer per-user records (follower counts, gender, avatar, the
  follow_me/following flags that m.weibo.cn returns inline) plus mutual
  detection and optional second-degree expansion. That logic is heavier and
  belongs in its own small file.

Scope: PUBLIC / login-visible data only. No private messages, no writes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional

import aiohttp

import weibo as WB
import relations as REL

logger = logging.getLogger("weibo_relations")

# Politeness + cost knobs. m.weibo.cn throttles aggressively; small delays
# keep the login cookie alive longer and avoid the 418/empty-card wall.
PAGE_DELAY = 0.8
DEFAULT_LIMIT = 200   # fans can be huge; cap by default


class CaptchaError(Exception):
    """Raised when m.weibo.cn serves a captcha challenge instead of data."""


async def _follow_page(session: aiohttp.ClientSession, uid: str,
                       cookie: Optional[str], kind: str, page: int) -> tuple:
    """One page of fans/follows. Returns (user_records, has_more)."""
    cid = {"follows": f"2310517_-_follow_-_{uid}",
           "fans": f"2310517_-_fans_-_{uid}"}[kind]
    url = (f"https://m.weibo.cn/api/container/getIndex?"
           f"containerid={cid}&page={page}")
    data = await WB._get(session, url, cookie)
    if not data or data.get("_needs_login"):
        return [], False
    if data.get("_captcha"):
        # m.weibo.cn is demanding a captcha — further paging will keep failing.
        # Surface this so the caller reports it instead of recording empties.
        raise CaptchaError(data.get("url", ""))
    cards = (data.get("data", {}) or {}).get("cards", []) or []
    users: List[dict] = []
    for card in cards:
        for g in card.get("card_group", []) or []:
            u = g.get("user") or {}
            if u.get("id"):
                users.append(_user_record(u, kind, uid))
    return users, bool(data.get("data", {}).get("maxPage", 1) > page) or len(users) > 0


def _user_record(u: dict, kind: str, owner_uid: str) -> dict:
    """Normalize a weibo user card into a graph-friendly record."""
    return {
        "uid": str(u["id"]),
        "screen_name": u.get("screen_name", ""),
        "followers_count": u.get("followers_count", 0),
        "follow_count": u.get("follow_count", 0),
        "description": u.get("description", ""),
        "gender": u.get("gender", ""),
        "avatar": u.get("profile_image_url", ""),
        # follow_me = they follow the owner; following = owner follows them.
        # For a FANS entry, follow_me=True means the relationship is MUTUAL.
        "follow_me": bool(u.get("follow_me")),
        "following": bool(u.get("following")),
        "direction": kind,   # 'fans' or 'follows' relative to owner_uid
    }


async def collect_fans(session: aiohttp.ClientSession, uid: str,
                       limit: int = DEFAULT_LIMIT,
                       captcha: Optional[list] = None) -> List[dict]:
    """Who follows `uid` (fans), with profile + mutual flag."""
    return await _collect_list(session, uid, "fans", limit, captcha)


async def collect_follows(session: aiohttp.ClientSession, uid: str,
                           limit: int = DEFAULT_LIMIT,
                           captcha: Optional[list] = None) -> List[dict]:
    """Who `uid` follows, with profile + mutual flag."""
    return await _collect_list(session, uid, "follows", limit, captcha)


async def _collect_list(session, uid, kind, limit,
                        captcha_holder: Optional[list] = None) -> List[dict]:
    cookie = WB.load_cookie_string()
    if not cookie:
        logger.warning("weibo %s needs login cookie", kind)
        return []
    out: List[dict] = []
    page = 1
    while len(out) < limit:
        try:
            users, _more = await _follow_page(session, uid, cookie, kind, page)
        except CaptchaError as e:
            logger.warning("weibo %s captcha at page %d — stopping list", kind, page)
            if captcha_holder is not None:
                captcha_holder.append(f"{kind}:{page}:{e}")
            break
        if not users:
            break
        # de-dup by uid within this list
        seen = {u["uid"] for u in out}
        for u in users:
            if u["uid"] not in seen:
                out.append(u)
                seen.add(u["uid"])
        if len(users) < 20:
            break
        page += 1
        await asyncio.sleep(PAGE_DELAY)
    return out[:limit]


def _to_relation_result(owner_uid: str, owner_name: str, kind: str,
                        users: List[dict]) -> REL.RelationResult:
    """Turn mined users into a RelationResult (same shape as Netease/GitHub)."""
    res = REL.RelationResult()
    n_index = {n.user_id: n for n in res.nodes}
    e_index = {e.identity(): e for e in res.edges}

    def ensure(uid, name) -> REL.SocialNode:
        n = n_index.get(uid)
        if n is None:
            n = REL.SocialNode(site="Weibo", user_id=str(uid),
                               nickname=name or "")
            n_index[uid] = n
            res.nodes.append(n)
        return n

    owner = ensure(owner_uid, owner_name)
    for u in users:
        ensure(u["uid"], u["screen_name"])
        # mutual = follow_me true (they follow owner) on a fans entry, OR
        # following true on a follows entry (owner is followed back by them).
        mutual = u["follow_me"] if kind == "fans" else u["following"]
        # direction-aware edge: fans => they -> owner; follows => owner -> they
        if kind == "fans":
            src, dst, src_n, dst_n = u["uid"], owner_uid, u["screen_name"], owner_name
        else:
            src, dst, src_n, dst_n = owner_uid, u["uid"], owner_name, u["screen_name"]
        edge = REL.SocialEdge(site="Weibo", src=str(src), dst=str(dst),
                              src_name=src_n, dst_name=dst_n,
                              mutual=bool(mutual),
                              weight=3 if mutual else 1)
        if edge.identity() not in e_index:
            e_index[edge.identity()] = edge
            res.edges.append(edge)
    return res


async def mine(session: aiohttp.ClientSession, uid: str,
               nickname: str = "", *, fans: bool = True, follows: bool = True,
               limit: int = DEFAULT_LIMIT) -> REL.RelationResult:
    """Mine a Weibo user's fans + follows into one merged RelationResult.

    This is the 'keep digging' entry point. Fans entries carry follow_me so
    we get true mutual detection for free (no reverse lookups needed).
    """
    merged = REL.RelationResult()

    async def fold(kind: str, captcha: Optional[list] = None):
        fn = collect_fans if kind == "fans" else collect_follows
        users = await fn(session, uid, limit, captcha)
        sub = _to_relation_result(uid, nickname, kind, users)
        merged.merge(sub)
        return len(users)

    counts = {}
    captcha: List[str] = []
    if fans:
        counts["fans"] = await fold("fans", captcha)
    if follows:
        counts["follows"] = await fold("follows", captcha)
    if captcha:
        merged.errors.append("weibo captcha triggered: " + "; ".join(captcha))
    mutual = sum(1 for e in merged.edges if e.mutual)
    logger.info("weibo mine %s: %s (%d mutual)", uid, counts, mutual)
    return merged


if __name__ == "__main__":
    import sys, json

    async def _demo():
        target = sys.argv[1] if len(sys.argv) > 1 else "3842706324"
        async with aiohttp.ClientSession(timeout=WB.TIMEOUT) as s:
            res = await mine(s, target, "LaineyInnea", fans=True, follows=True, limit=50)
            print(REL.to_json(res) and "")  # avoid dumping huge
            print(f"nodes={len(res.nodes)} edges={len(res.edges)} "
                  f"mutual={sum(1 for e in res.edges if e.mutual)}")
            for e in sorted(res.edges, key=lambda x: -x.weight)[:10]:
                tag = "MUTUAL" if e.mutual else "follows"
                print(f"  {e.src_name} [{tag}] {e.dst_name}")

    asyncio.run(_demo())
