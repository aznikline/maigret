#!/usr/bin/env python3
"""
Xiaohongshu (小红书) interaction mining SKELETON.

STATUS — partially ready. Unlike Weibo (cookie = full access), XHS web APIs
require an 'x-s' / 'x-t' request signature on every call (the 406 we saw).
A valid cookie alone is NOT enough.

This module is wired to drop into the same pipeline, but the signature step is
a deliberate PLUG POINT: set XHS_SIGN_FN (a callable(url, params) -> dict) to
a working x-s signer — e.g. the Xiaohongshu-Shield-Algorithm/shield_sdk.py — and
the collector works. Without it, it degrades cleanly and reports why, so it
never silently returns empty 'no interactions' data.

Scope: login-visible note text and its @mentions only. No private messages.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional

import aiohttp

import cookies as cookie_store

logger = logging.getLogger("xhs_interactions")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
TIMEOUT = aiohttp.ClientTimeout(total=20)
DEFAULT_NOTE_LIMIT = 20

# PLUG POINT: a callable (api_path, data_str, a1) -> {"x-s":..., "x-t":...}.
# Defaults to Spider_XHS's working web x-s signer (get_x_s) if available.
XHS_SIGN_FN = None

def _load_spider_xhs_signer():
    """Load Spider_XHS's web x-s signer if its JS + crypto-js are usable.

    Spider_XHS reverse-engineered the XHS web signature into static JS we call
    via execjs. Its Python glue calls the WRONG function name (get_xs); the JS
    actually exports get_x_s, so we call that directly.
    """
    import os as _os
    base = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                        "Spider_XHS")
    if not _os.path.isdir(base):
        return None
    # Spider_XHS's JS requires node 'crypto-js'; expose its node_modules.
    nm = _os.path.join(base, "node_modules")
    if _os.path.isdir(nm) and "NODE_PATH" not in _os.environ:
        _os.environ["NODE_PATH"] = nm
    # Spider_XHS imports xhs_utils as a package; put it on sys.path.
    import sys as _sys
    if base not in _sys.path:
        _sys.path.insert(0, base)
    try:
        import execjs
    except ImportError:
        logger.warning("execjs missing — pip install PyExecJS for xhs signing")
        return None
    try:
        from xhs_utils.xhs_util import generate_headers
    except Exception as e:
        logger.warning("could not import Spider_XHS generate_headers: %s", e)
        return None

    logger.info("xhs signer loaded via Spider_XHS generate_headers")

    def sign(api, data, a1, method="GET"):
        # generate_headers must run with cwd=Spider_XHS: its JS reads sibling
        # static/*.js via relative require paths.
        cwd = _os.getcwd()
        try:
            _os.chdir(base)
            result = generate_headers(a1, api, data or "", method)
        finally:
            _os.chdir(cwd)
        headers = result[0] if isinstance(result, tuple) else result
        return {k: v for k, v in headers.items() if v}

    logger.info("xhs signer loaded via Spider_XHS get_x_s")
    return sign


XHS_SIGN_FN = _load_spider_xhs_signer()


def load_cookie_string() -> Optional[str]:
    header, status = cookie_store.get_header("xiaohongshu")
    if status.ok:
        return header
    if status.reason == "expired":
        logger.warning("xhs cookie expired (%s) — re-extract",
                       cookie_store.age_label(status))
    elif status.reason == "no_file":
        logger.info("no cookies/xiaohongshu.json — xhs needs login")
    return None


def _headers(cookie: Optional[str], sig: Optional[dict]) -> Dict[str, str]:
    h = {"User-Agent": UA, "Origin": "https://www.xiaohongshu.com",
         "Referer": "https://www.xiaohongshu.com/"}
    if cookie:
        h["Cookie"] = cookie
    if sig:
        h.update(sig)  # x-s, x-t
    return h


async def collect_interactions(session: aiohttp.ClientSession, uid: str,
                                owner_name: str = "",
                                limit: int = DEFAULT_NOTE_LIMIT):
    """Collect @mentions from a XHS user's recent notes.

    Returns a relations.RelationResult. CURRENTLY DEGRADES: without XHS_SIGN_FN
    and a valid cookie, it returns an empty result with a clear error rather
    than fabricating data.
    """
    import relations as REL
    res = REL.RelationResult()
    cookie = load_cookie_string()
    if not cookie:
        res.errors.append("xhs: no login cookie (re-extract)")
        return res
    if XHS_SIGN_FN is None:
        res.errors.append("xhs: no x-s signer configured (set XHS_SIGN_FN "
                          "to a callable producing {x-s,x-t})")
        logger.warning("xhs interactions: signer not configured — skipping "
                       "(need x-s signature; cookie alone gets 406)")
        return res

    # a1 is part of the signing key — extract from the cookie
    a1 = ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "a1":
            a1 = v
            break
    if not a1:
        res.errors.append("xhs: cookie has no a1 value (need full login cookie)")
        return res

    api = "/api/sns/web/v1/user_posted"
    import urllib.parse as up
    params = {"num": str(limit), "cursor": "", "user_id": uid,
              "image_formats": "jpg,webp,avif", "need_filter": ""}
    qs = up.urlencode(params)
    try:
        # sign over api + query string; GET method
        sig = XHS_SIGN_FN(api + "?" + qs, "", a1, "GET")
    except Exception as e:
        res.errors.append(f"xhs: signing failed: {e}")
        return res

    url = "https://edith.xiaohongshu.com" + api + "?" + qs
    # sig is now the full header dict from generate_headers; add our cookie
    headers = dict(sig)
    headers["Cookie"] = cookie
    try:
        async with session.get(url, headers=headers,
                               timeout=TIMEOUT) as r:
            body = await r.text()
    except Exception as e:
        res.errors.append(f"xhs: request failed: {e}")
        return res

    import json
    try:
        j = json.loads(body)
    except Exception:
        res.errors.append(f"xhs: non-json response (len {len(body)})")
        return res
    if not j.get("success") and j.get("code") != 0:
        res.errors.append(f"xhs: api error code={j.get('code')} "
                          f"msg={str(j.get('msg',''))[:60]}")
        return res

    notes = (j.get("data", {}) or {}).get("notes", []) or []
    n_index = {n.user_id: n for n in res.nodes}
    e_index = {e.identity(): e for e in res.edges}

    def ensure(name):
        key = f"name:{name}"
        if key not in n_index:
            n = REL.SocialNode(site="Xiaohongshu", user_id=key, nickname=name)
            n_index[key] = n
            res.nodes.append(n)
        return n

    owner = ensure(owner_name or uid)
    import re
    mention_counts = {}
    for note in notes:
        desc = (note.get("display_title", "") + " "
                + note.get("desc", ""))
        for m in re.findall(r"@([\w一-鿿·.\-_]+)", desc):
            if m == owner_name:
                continue
            ensure(m)
            mention_counts[m] = mention_counts.get(m, 0) + 1
    for name, cnt in mention_counts.items():
        edge = REL.SocialEdge(site="Xiaohongshu", src=uid,
                              dst=f"name:{name}", src_name=owner.nickname,
                              dst_name=name, weight=min(cnt, 5), via="mentions")
        if edge.identity() not in e_index:
            e_index[edge.identity()] = edge
            res.edges.append(edge)
    logger.info("xhs interactions %s: %d notes, %d mention-users",
                uid, len(notes), len(mention_counts))
    return res


if __name__ == "__main__":
    import asyncio

    async def _demo():
        async with aiohttp.ClientSession(timeout=TIMEOUT) as s:
            r = await collect_interactions(s, "test")
            print("edges:", len(r.edges), "errors:", r.errors)

    asyncio.run(_demo())
