#!/usr/bin/env python3
"""
Weibo INTERACTION graph mining: who a user actually interacts with, not just
who they follow.

Why this beats the follow list: a follow list has hundreds of one-way ties,
most to big accounts the person will never meet. But @mentions, reposts, and
replies in someone's actual posts are a FILTERED interaction set — these are
the people they chose to address in content. Far stronger signal.

Sources (all public / login-visible, same m.weibo.cn API weibo.py already uses):
  - user post stream  107603_<uid>   -> each mblog's @mentions + repost target
  - (optional) replies/comments      -> who comments on their posts

Emits a RelationResult with edges labeled by interaction type and weighted by
frequency, so the graph can show 'mentions 5x' as a strong tie.

Scope: PUBLIC post content only. No DMs, no writes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Dict, List, Optional

import aiohttp

import weibo as WB
import relations as REL

logger = logging.getLogger("weibo_interactions")

# @ mention in m.weibo.cn post text is rendered as a plain "@screen_name".
MENTION_RE = re.compile(r"@([\w一-鿿·.\-_]+)")
POST_DELAY = 1.0  # m.weibo.cn throttles hard; stay slow to avoid captcha
DEFAULT_POST_PAGES = 5


class CaptchaServed(Exception):
    """m.weibo.cn returned a captcha challenge instead of post data."""


async def _post_stream_page(session: aiohttp.ClientSession, uid: str,
                            cookie: Optional[str], page: int) -> tuple:
    """One page of a user's posts. Returns (mblog_list, has_more)."""
    url = (f"https://m.weibo.cn/api/container/getIndex?"
           f"containerid=107603_{uid}&page={page}")
    data = await WB._get(session, url, cookie)
    if not data or data.get("_needs_login"):
        return [], False
    if data.get("_captcha"):
        # signal the caller to stop paging (m.weibo.cn served a captcha)
        raise CaptchaServed(data.get("url", ""))
    cards = (data.get("data", {}) or {}).get("cards", []) or []
    posts = [c["mblog"] for c in cards if c.get("mblog")]
    more = bool(data.get("data", {}).get("maxPage", 1) > page)
    return posts, more


def _mentions(mblog: dict) -> List[str]:
    """@-mentioned screen names in a post's plain text."""
    text = mblog.get("text", "") or ""
    return [m for m in MENTION_RE.findall(text) if m]


def _repost_target(mblog: dict) -> Optional[dict]:
    """If this post is a repost, return the target {uid, screen_name}."""
    rt = mblog.get("retweeted_status")
    if not rt:
        return None
    user = rt.get("user") or {}
    if not user.get("id"):
        return None
    return {"uid": str(user["id"]),
            "screen_name": user.get("screen_name", "")}


async def collect_interactions(session: aiohttp.ClientSession, uid: str,
                                owner_name: str = "",
                                pages: int = DEFAULT_POST_PAGES
                                ) -> REL.RelationResult:
    """Mine @mentions + repost targets from a user's posts.

    Edges: owner --mentions--> @-mentioned user (weight by count)
           owner --reposts--> reposted user
    """
    res = REL.RelationResult()
    cookie = WB.load_cookie_string()
    if not cookie:
        res.errors.append("weibo interactions: no login cookie")
        return res

    n_index = {n.user_id: n for n in res.nodes}
    e_index = {e.identity(): e for e in res.edges}

    def ensure_node(name: str, uid_hint: str = "") -> REL.SocialNode:
        # mentions only give screen_name (no uid), so key by name if no uid.
        # We still register it so it appears in the graph.
        key = uid_hint or f"name:{name}"
        n = n_index.get(key)
        if n is None:
            n = REL.SocialNode(site="Weibo", user_id=key, nickname=name)
            n_index[key] = n
            res.nodes.append(n)
        return n

    owner = ensure_node(owner_name or uid, uid)
    mention_counts: Dict[str, int] = {}
    total_posts_seen = 0

    captcha_hit = False
    for page in range(1, pages + 1):
        try:
            posts, _more = await _post_stream_page(session, uid, cookie, page)
        except Exception as e:
            logger.warning("weibo interactions captcha at page %d: %s", page, e)
            res.errors.append(f"weibo captcha at page {page}")
            captcha_hit = True
            break
        if not posts:
            break
        total_posts_seen += len(posts)
        for m in posts:
            # mentions
            for name in _mentions(m):
                if name == owner_name:
                    continue
                ensure_node(name, f"name:{name}")
                mention_counts[name] = mention_counts.get(name, 0) + 1
            # repost target (carries uid — stronger)
            tgt = _repost_target(m)
            if tgt:
                ensure_node(tgt["screen_name"], tgt["uid"])
                edge = REL.SocialEdge(
                    site="Weibo", src=str(uid), dst=str(tgt["uid"]),
                    src_name=owner.nickname, dst_name=tgt["screen_name"],
                    weight=2, via="reposts")
                if edge.identity() not in e_index:
                    e_index[edge.identity()] = edge
                    res.edges.append(edge)
        await asyncio.sleep(POST_DELAY)
        if captcha_hit:
            break

    # mention edges, weighted by frequency
    for name, cnt in mention_counts.items():
        edge = REL.SocialEdge(
            site="Weibo", src=str(uid), dst=f"name:{name}",
            src_name=owner.nickname, dst_name=name,
            weight=min(cnt, 5),  # cap so a mega-mentioner doesn't dominate
            via="mentions")
        edge.notes = f"mentions x{cnt}"  # type: ignore[attr-defined]
        if edge.identity() not in e_index:
            e_index[edge.identity()] = edge
            res.edges.append(edge)

    logger.info("weibo interactions %s: %d posts, %d mention-users, %d reposts",
                uid, total_posts_seen, len(mention_counts),
                sum(1 for e in res.edges if e.via == "reposts"))
    return res


if __name__ == "__main__":
    import sys, json

    async def _demo():
        target = sys.argv[1] if len(sys.argv) > 1 else "3842706324"
        async with aiohttp.ClientSession(timeout=WB.TIMEOUT) as s:
            res = await collect_interactions(s, target, "LaineyInnea", pages=3)
            print(f"nodes={len(res.nodes)} edges={len(res.edges)}")
            if res.errors:
                print("errors:", res.errors)
            for e in sorted(res.edges, key=lambda x: -x.weight)[:10]:
                n = f" (x{e.weight})" if e.weight > 1 else ""
                print(f"  {e.src_name} --mentions--> {e.dst_name}{n}")

    asyncio.run(_demo())
