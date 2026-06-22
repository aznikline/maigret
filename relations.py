#!/usr/bin/env python3
"""
Public social-relationship collector for entity_enrich.

Gathers *who-follows-whom* edges from endpoints that are reachable WITHOUT
login (pure public data). Returns a list of directed, weighted social edges.

Scope (this is what you get, no more):
  - Netease Cloud Music `getfollows` — public, returns nickname/userId/avatar
    AND a `mutual` flag (true follow-back). The richest source available here.
  - GitHub followers / following — public, but anonymous calls are rate
    limited (60/h). Set GITHUB_TOKEN for 5000/h.

Explicitly NOT implemented:
  - Anything requiring login state to read (private friends lists, "mutual
    friends only I can see", etc.). Reading those is not public data.

Edge model (directed, weighted):
  A --follow--> B          weight 1
  A <--> B (mutual=True)   weight 3   (strongest — a real reciprocal tie)
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import aiohttp

from maigret_extensions.models import (
    CollectionStatus,
    RelationResult,
    SocialEdge,
    SocialNode,
)
from maigret_extensions.privacy import sanitize_binding

logger = logging.getLogger("relations")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
TIMEOUT = aiohttp.ClientTimeout(total=20)


# ---------------------------------------------------------------------------
# Netease — the one truly-public source available without login
# ---------------------------------------------------------------------------

NETEASE_FOLLOW_PAGE = 50
# Global knobs so callers can trade completeness vs. politeness.
#   DETECT_MUTUAL=True  -> for each followed user, fetch THEIR follow list to
#   check whether they follow back. This is the only way to get true reciprocal
#   ties on Netease (its getfollows payload does not report mutual reliably).
#   Cost: one extra request per followed user. Cap with MUTUAL_CHECK_MAX.
DETECT_MUTUAL = os.environ.get("DETECT_MUTUAL", "0") == "1"
MUTUAL_CHECK_MAX = int(os.environ.get("MUTUAL_CHECK_MAX", "50"))


async def _netease_follows_page(session: aiohttp.ClientSession, uid: str,
                                offset: int, limit: int) -> tuple:
    """One page of who `uid` follows. Returns (follow_list, has_more, code)."""
    uid = str(uid).strip()
    if not uid or uid == "None":
        return [], False, 0
    params = urllib.parse.urlencode(
        {"offset": offset, "limit": min(limit, NETEASE_FOLLOW_PAGE), "order": "true"})
    url = f"https://music.163.com/api/user/getfollows/{uid}?{params}"
    headers = {"User-Agent": UA, "Referer": "https://music.163.com/"}
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return [], False, resp.status
            data = await resp.json(content_type=None)
            if data.get("code") != 200:
                logger.debug("netease %s code=%s", uid, data.get("code"))
                return [], False, data.get("code")
            return data.get("follow", []) or [], bool(data.get("more")), data.get("code")
    except Exception as e:
        logger.debug("netease follows %s failed: %s", uid, e)
        return [], False, 0


async def _netease_follows(session: aiohttp.ClientSession, uid: str,
                           limit: int = 500) -> List[dict]:
    """Pull the full follow list of `uid`, paging on the `more` flag."""
    out: List[dict] = []
    offset = 0
    while offset < limit:
        page, more, code = await _netease_follows_page(
            session, uid, offset, NETEASE_FOLLOW_PAGE)
        if not page:
            break
        out.extend(page)
        if len(page) < NETEASE_FOLLOW_PAGE or not more:
            break
        offset += NETEASE_FOLLOW_PAGE
    return out[:limit]


async def _netease_follows_back(session: aiohttp.ClientSession, uid: str,
                                target: str) -> bool:
    """Does `uid` follow `target` back? True = a reciprocal (mutual) tie.

    Kept for external callers / tests; the collector now inlines this logic so
    it can reuse the fetched list for second-degree expansion.
    """
    follows = await _netease_follows(session, uid, limit=MUTUAL_CHECK_MAX)
    return str(target) in {str(f.get("userId")) for f in follows}


# Netease 'bindings' type -> human label. Only type 2 reliably carries a
# usable cross-platform URL (Weibo). Others may resolve to a numeric id that
# is itself a valid pivot on that platform.
NETEASE_BINDING_TYPES = {
    1: "phone",
    2: "weibo",
    3: "wechat",
    4: "qq",
    5: "qq",
    9: "apple",
    10: "weibo_legacy",
    11: "netease_mail",
}


async def _netease_bindings(session: aiohttp.ClientSession, uid: str,
                             ) -> List[dict]:
    """Pull a Netease user's cross-platform account bindings (public v1 API).

    Returns a list of {platform, url, id, binding_time}. type:2 (Weibo) is the
    prize: it frequently carries a real weibo.com/u/<id> URL.
    """
    uid = str(uid).strip()
    if not uid or uid == "None":
        return []
    url = f"https://music.163.com/api/v1/user/detail/{uid}"
    headers = {"User-Agent": UA, "Referer": "https://music.163.com/"}
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            if data.get("code") != 200:
                return []
    except Exception as e:
        logger.debug("netease bindings %s failed: %s", uid, e)
        return []
    out = []
    for b in data.get("bindings", []) or []:
        t = b.get("type")
        label = NETEASE_BINDING_TYPES.get(t, f"type_{t}")
        entry = {"platform": label, "type": t, "id": b.get("id"),
                 "url": b.get("url") or "",
                 "binding_time": b.get("bindingTime")}
        out.append(entry)
    return out


def collect_netease(site_uid: str, site_name: str, owner_name: str,
                    follow_list: List[dict], result: RelationResult,
                    degree: int = 1) -> None:
    """Turn one page of getfollows into directed social edges + nodes.

    degree: 1 for the seed's own follows, 2 for follows pulled through a
    mutual contact (second-degree expansion).
    """
    node_index = {n.user_id: n for n in result.nodes}
    edge_index = {e.identity(): e for e in result.edges}

    def ensure_node(uid: str, nickname: str, avatar: str,
                    deg: int = degree) -> SocialNode:
        n = node_index.get(uid)
        if n is None:
            n = SocialNode(site="NeteaseCloudMusic", user_id=str(uid),
                           nickname=nickname or "", avatar=avatar or "",
                           degree=deg)
            node_index[uid] = n
            result.nodes.append(n)
        elif deg < n.degree:
            # upgrade: a node seen at degree 2 then reached at degree 1 is
            # actually a direct follow — keep the smaller (closer) degree.
            n.degree = deg
        return n

    owner = ensure_node(site_uid, owner_name, "", deg=1)
    for f in follow_list:
        fid = f.get("userId")
        if not fid:
            continue
        mutual = bool(f.get("mutual") or f.get("followed"))
        ensure_node(str(fid), f.get("nickname", ""), f.get("avatarUrl", ""))
        edge = SocialEdge(
            site="NeteaseCloudMusic",
            src=str(site_uid), dst=str(fid),
            src_name=owner.nickname, dst_name=f.get("nickname", ""),
            mutual=mutual,
            weight=3 if mutual else 1,
        )
        if edge.identity() not in edge_index:
            edge_index[edge.identity()] = edge
            result.edges.append(edge)


async def collect_netease_for(session, uid: str, nickname: str,
                               limit: int = 500) -> RelationResult:
    res = RelationResult()
    follows = await _netease_follows(session, uid, limit)
    collect_netease(uid, "NeteaseCloudMusic", nickname, follows, res)

    mutual = sum(1 for f in follows if f.get("mutual") or f.get("followed"))
    # API rarely reports mutual accurately — optionally verify by reverse lookups.
    # Only check a capped subset of followed users to stay polite.
    # BONUS: when a mutual is found, we ALREADY fetched their follow list —
    # so fold those second-degree accounts into the graph too. That is the
    # "follow the mutual, dig their associations" expansion: the people a
    # real contact follows are far more likely to be in the subject's real
    # social circle than a random followed star.
    if DETECT_MUTUAL and follows:
        checked = 0
        second_degree = 0
        bridged = 0
        for f in follows[:MUTUAL_CHECK_MAX]:
            fid = f.get("userId")
            if not fid:
                continue
            # fetch this user's follows ONCE — reuse for mutual check + expansion.
            their = await _netease_follows(session, str(fid),
                                           limit=MUTUAL_CHECK_MAX)
            checked += 1
            if uid in {str(x.get("userId")) for x in their}:
                mutual += 1
                key = (f"NeteaseCloudMusic::{uid}>{fid}", "follows")
                for e in res.edges:
                    if e.identity() == key:
                        e.mutual = True
                        e.weight = 3
                        break
                # expand: add mutual's own follows as 2nd-degree nodes+edges
                collect_netease(str(fid), "NeteaseCloudMusic",
                               f.get("nickname", ""), their, res, degree=2)
                second_degree += len(their)
                # bridge: fetch this mutual's cross-platform bindings (Weibo,
                # QQ, etc.) and attach them to their node. This is the platform
                # hop: Netease -> Weibo, enabling the multi-platform traversal.
                bindings = await _netease_bindings(session, str(fid))
                if bindings:
                    bridged += 1
                    # attach to the node we just created
                    for n in res.nodes:
                        if n.user_id == str(fid):
                            n.cross_platform = bindings
                            break
        logger.info("netease %s: %d follows (%d mutual, %d reverse-checked, "
                    "%d 2nd-degree, %d cross-platform bridged)", uid,
                    len(follows), mutual, checked, second_degree, bridged)
    else:
        logger.info("netease %s: %d follows (%d mutual per API)",
                    uid, len(follows), mutual)
    seed_bindings = await _netease_bindings(session, uid)
    if seed_bindings:
        for node in res.nodes:
            if node.user_id == str(uid):
                node.cross_platform = seed_bindings
                break
    account = f"NeteaseCloudMusic::{uid}"
    res.statuses.extend([
        CollectionStatus(
            "NeteaseCloudMusic", account, "following",
            "partial" if len(follows) >= limit else (
                "complete" if follows else "empty"
            ), "public",
        ),
        CollectionStatus(
            "NeteaseCloudMusic", account, "followers", "unavailable",
            "public", "anonymous follower endpoint unavailable",
        ),
        CollectionStatus(
            "NeteaseCloudMusic", account, "bindings",
            "complete" if any(n.cross_platform for n in res.nodes) else "empty",
            "public",
        ),
    ])
    return res


# ---------------------------------------------------------------------------
# GitHub — public, rate-limited anonymously
# ---------------------------------------------------------------------------

def _github_headers() -> Dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _github_list(session, login: str, ep: str,
                      limit: int = 30):
    page_size = min(limit, 100)
    url = f"https://api.github.com/users/{login}/{ep}?per_page={page_size}"
    try:
        async with session.get(url, headers=_github_headers()) as resp:
            if resp.status == 403:
                rl = resp.headers.get("X-RateLimit-Remaining", "?")
                logger.warning("github 403 rate-limited (remaining=%s). "
                               "Set GITHUB_TOKEN for 5000/h.", rl)
                return [], "rate_limited", f"remaining={rl}"
            if resp.status != 200:
                return [], "unavailable", f"http={resp.status}"
            items = await resp.json(content_type=None)
            state = "partial" if len(items) >= page_size else (
                "complete" if items else "empty"
            )
            return items, state, ""
    except Exception as e:
        logger.debug("github %s/%s failed: %s", login, ep, e)
        return [], "error", type(e).__name__


def _collection_payload(value) -> tuple:
    """Accept typed fetch results and legacy/test list results."""
    if isinstance(value, tuple) and len(value) == 3:
        return value
    items = value if isinstance(value, list) else []
    return items, "complete" if items else "empty", ""


async def collect_github_for(session, login: str,
                              limit: int = 30) -> RelationResult:
    res = RelationResult()
    node_index = {n.user_id: n for n in res.nodes}
    edge_index = {e.identity(): e for e in res.edges}

    def ensure(login_id: str, name: str) -> SocialNode:
        n = node_index.get(login_id)
        if n is None:
            n = SocialNode(site="GitHub", user_id=login_id, nickname=name or login_id)
            node_index[login_id] = n
            res.nodes.append(n)
        return n

    owner = ensure(login, login)
    followers, followers_state, followers_detail = _collection_payload(
        await _github_list(session, login, "followers", limit)
    )
    following, following_state, following_detail = _collection_payload(
        await _github_list(session, login, "following", limit)
    )
    follower_ids = {x.get("login") for x in followers if x.get("login")}
    following_ids = {x.get("login") for x in following if x.get("login")}
    for x in followers:
        source = x.get("login")
        if not source:
            continue
        ensure(source, source)
        mutual = source in following_ids
        edge = SocialEdge(
            site="GitHub", src=source, dst=login,
            src_name=source, dst_name=login,
            mutual=mutual, weight=3 if mutual else 1,
        )
        if edge.identity() not in edge_index:
            edge_index[edge.identity()] = edge
            res.edges.append(edge)
    for x in following:
        target = x.get("login")
        if not target:
            continue
        ensure(target, x.get("login"))
        mutual = target in follower_ids
        edge = SocialEdge(site="GitHub", src=login, dst=target,
                          src_name=login, dst_name=target,
                          mutual=mutual, weight=3 if mutual else 1)
        if edge.identity() not in edge_index:
            edge_index[edge.identity()] = edge
            res.edges.append(edge)
    account = f"GitHub::{login}"
    res.statuses.extend([
        CollectionStatus(
            "GitHub", account, "followers", followers_state, "public",
            followers_detail,
        ),
        CollectionStatus(
            "GitHub", account, "following", following_state, "public",
            following_detail,
        ),
    ])
    return res


# ---------------------------------------------------------------------------
# Bilibili — observed public Web/mobile endpoints, best effort
# ---------------------------------------------------------------------------

def _bilibili_entries(payload: dict) -> List[dict]:
    data = payload.get("data") or {}
    raw = data.get("list") or data.get("items") or []
    result = []
    for item in raw:
        user_id = item.get("mid") or item.get("fid")
        if not user_id:
            continue
        result.append({
            "id": str(user_id),
            "name": item.get("uname") or item.get("name") or str(user_id),
            "avatar": item.get("face") or item.get("avatar") or "",
        })
    return result


async def _bilibili_list(session, urls: List[str], limit: int = 50):
    last_state, last_detail = "unavailable", "no endpoint succeeded"
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    for url in urls:
        try:
            async with session.get(url, headers=headers, timeout=TIMEOUT) as resp:
                if resp.status == 429:
                    last_state, last_detail = "rate_limited", "http=429"
                    continue
                if resp.status != 200:
                    last_state, last_detail = "unavailable", f"http={resp.status}"
                    continue
                payload = await resp.json(content_type=None)
        except Exception as exc:
            last_state, last_detail = "error", type(exc).__name__
            continue
        if payload.get("code") != 0:
            code = payload.get("code")
            last_state = "rate_limited" if code in {-412, -509} else "unavailable"
            last_detail = f"code={code}"
            continue
        entries = _bilibili_entries(payload)
        state = "partial" if len(entries) >= limit else (
            "complete" if entries else "empty"
        )
        return entries, state, ""
    return [], last_state, last_detail


async def collect_bilibili_for(session, uid: str, nickname: str = "",
                                limit: int = 50) -> RelationResult:
    res = RelationResult()
    size = min(max(limit, 1), 50)
    followers, followers_state, followers_detail = await _bilibili_list(
        session,
        [
            f"https://app.bilibili.com/x/v2/relation/follower?vmid={uid}&pn=1&ps={size}",
            f"https://api.bilibili.com/x/relation/followers?vmid={uid}&pn=1&ps={size}",
        ],
        size,
    )
    following, following_state, following_detail = await _bilibili_list(
        session,
        [
            f"https://app.bilibili.com/x/v2/relation/following?vmid={uid}&pn=1&ps={size}",
            f"https://api.bilibili.com/x/relation/followings?vmid={uid}&pn=1&ps={size}",
        ],
        size,
    )
    follower_ids = {item["id"] for item in followers}
    following_ids = {item["id"] for item in following}
    people = {item["id"]: item for item in [*followers, *following]}
    res.nodes.append(SocialNode("Bilibili", str(uid), nickname or str(uid)))
    for item in people.values():
        res.nodes.append(SocialNode(
            "Bilibili", item["id"], item["name"], item["avatar"]
        ))
    for item in followers:
        mutual = item["id"] in following_ids
        res.edges.append(SocialEdge(
            "Bilibili", item["id"], str(uid), item["name"], nickname,
            mutual, 3 if mutual else 1,
        ))
    for item in following:
        mutual = item["id"] in follower_ids
        res.edges.append(SocialEdge(
            "Bilibili", str(uid), item["id"], nickname, item["name"],
            mutual, 3 if mutual else 1,
        ))
    account = f"Bilibili::{uid}"
    res.statuses.extend([
        CollectionStatus(
            "Bilibili", account, "followers", followers_state, "public",
            followers_detail,
        ),
        CollectionStatus(
            "Bilibili", account, "following", following_state, "public",
            following_detail,
        ),
    ])
    return res


async def collect_weibo_for(session, uid: str, nickname: str = "",
                             limit: int = 200) -> RelationResult:
    import weibo as WB

    account = f"Weibo::{uid}"
    if not WB.load_cookie_string():
        return RelationResult(statuses=[CollectionStatus(
            "Weibo", account, "relationships", "auth_required", "authorized",
            "login cookie unavailable",
        )])
    try:
        import weibo_relations as WBR
        result = await WBR.mine(
            session, str(uid), nickname=nickname, fans=True, follows=True,
            limit=limit,
        )
        state = "partial" if result.errors or len(result.edges) >= limit else (
            "complete" if result.edges else "empty"
        )
        result.statuses.extend([
            CollectionStatus("Weibo", account, "followers", state, "authorized"),
            CollectionStatus("Weibo", account, "following", state, "authorized"),
        ])
        try:
            import weibo_interactions as WBI
            interactions = await WBI.collect_interactions(
                session, str(uid), owner_name=nickname,
                pages=WBI.DEFAULT_POST_PAGES,
            )
            result.merge(interactions)
            interaction_state = (
                "partial" if interactions.errors
                else ("complete" if interactions.edges else "empty")
            )
            result.statuses.append(CollectionStatus(
                "Weibo", account, "interactions", interaction_state,
                "authorized",
            ))
        except Exception as exc:
            result.statuses.append(CollectionStatus(
                "Weibo", account, "interactions", "error", "authorized",
                type(exc).__name__,
            ))
        return result
    except Exception as exc:
        return RelationResult(statuses=[CollectionStatus(
            "Weibo", account, "relationships", "error", "authorized",
            type(exc).__name__,
        )])


async def collect_xhs_for(session, uid: str, nickname: str = "",
                          limit: int = 20) -> RelationResult:
    import xhs_interactions as XHS

    result = await XHS.collect_interactions(
        session, str(uid), owner_name=nickname, limit=limit
    )
    detail = "; ".join(result.errors)[:160]
    if any("no login cookie" in item for item in result.errors):
        state = "auth_required"
    elif any("signer" in item or "signing" in item for item in result.errors):
        state = "unavailable"
    elif result.errors:
        state = "partial"
    else:
        state = "partial" if result.edges else "empty"
    result.statuses.append(CollectionStatus(
        "Xiaohongshu", f"Xiaohongshu::{uid}", "interactions", state,
        "authorized", detail,
    ))
    return result


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def collect_relations(session: aiohttp.ClientSession,
                            records: List[Any]) -> RelationResult:
    """Collect each discovered account or emit an explicit coverage status."""
    merged = RelationResult()
    collected_accounts = set()

    for rec in records:
        usernames = [m.value for m in rec.members if m.kind == "username"]
        nickname = next((m.value for m in rec.members if m.kind == "name"), "")
        urls = [m.value for m in rec.members if m.kind == "url"]

        site = rec.site
        if site in {"NeteaseCloudMusic", "Bilibili", "Weibo"}:
            uid = next((u for u in usernames if str(u).isdigit()), None) \
                or _numeric_id_from_urls(urls)
        elif site == "GitHub":
            uid = next((u for u in usernames if not str(u).isdigit()),
                       usernames[-1] if usernames else _last_url_segment(urls))
        elif site == "Xiaohongshu":
            uid = usernames[-1] if usernames else _last_url_segment(urls)
        else:
            uid = None

        supported = {
            "NeteaseCloudMusic", "GitHub", "Weibo", "Bilibili", "Xiaohongshu"
        }
        if site not in supported:
            merged.statuses.append(CollectionStatus(
                site, rec.rid, "relationships", "unsupported", "unsupported",
                "no typed relationship adapter",
            ))
            continue

        if not uid:
            merged.statuses.append(CollectionStatus(
                site, rec.rid, "relationships", "unavailable", "public",
                "stable account identifier unavailable",
            ))
            continue

        try:
            if site == "NeteaseCloudMusic":
                sub = await collect_netease_for(session, str(uid), nickname)
            elif site == "GitHub":
                sub = await collect_github_for(session, str(uid))
            elif site == "Weibo":
                sub = await collect_weibo_for(session, str(uid), nickname)
            elif site == "Bilibili":
                sub = await collect_bilibili_for(session, str(uid), nickname)
            else:
                sub = await collect_xhs_for(session, str(uid), nickname)
        except Exception as exc:
            access = "authorized" if site in {"Weibo", "Xiaohongshu"} else "public"
            merged.statuses.append(CollectionStatus(
                site, f"{site}::{uid}", "relationships", "error", access,
                type(exc).__name__,
            ))
            merged.errors.append(f"{site} collector failed: {type(exc).__name__}")
            continue

        merged.merge(sub)
        collected_accounts.add(f"{site}::{uid}")

    # Bound traversal to explicit NetEase -> Weibo bindings.
    for node in list(merged.nodes):
        for binding in node.cross_platform:
            if binding.get("platform") not in {"weibo", "weibo_legacy"}:
                continue
            match = re.search(
                r"/u/(\d+)|/(\d{6,})$", str(binding.get("url") or "")
            )
            wuid = (match.group(1) or match.group(2)) if match else None
            account = f"Weibo::{wuid}" if wuid else ""
            if not wuid or account in collected_accounts:
                continue
            logger.info("platform hop: %s -> Weibo/%s", node.nickname, wuid)
            merged.merge(await collect_weibo_for(
                session, wuid, nickname=node.nickname
            ))
            collected_accounts.add(account)

    return merged


def _numeric_id_from_urls(urls: List[str]) -> Optional[str]:
    for url in urls:
        match = re.search(
            r"(?:id=|/u/|space\.bilibili\.com/)(\d+)|/(\d{6,})/?$",
            str(url),
        )
        if match:
            return match.group(1) or match.group(2)
    return None


def _last_url_segment(urls: List[str]) -> Optional[str]:
    for url in urls:
        path = urllib.parse.urlparse(str(url)).path.rstrip("/")
        if path:
            return path.rsplit("/", 1)[-1]
    return None


def find_bridges(per_seed: Dict[str, RelationResult],
                 min_overlap: int = 1) -> List[dict]:
    """Find shared-followed accounts across seeds -> real social bridges.

    If seed A and seed B both follow the same account X (on the same site),
    X is a bridge node tying A and B together. Returns one bridge record per
    shared node, listing the seeds that reach it. This is the strongest
    cross-target signal available from public data: two people who both follow
    the same niche account are far more likely connected than a generic tie.
    """
    # node_key(site:user_id) -> {seed: nickname-followed}
    shared: Dict[str, Dict[str, str]] = {}
    for seed, rel in per_seed.items():
        # for each seed, which nodes does it point TO?
        followed = {}
        for e in rel.edges:
            nk = f"{e.site}:{e.dst}"
            followed.setdefault(nk, e.dst_name or e.dst)
        for nk, name in followed.items():
            shared.setdefault(nk, {})[seed] = name

    bridges = []
    for nk, reachers in shared.items():
        if len(reachers) < 2:
            continue
        # find the canonical name
        name = next(iter(reachers.values()), nk)
        bridges.append({
            "node": nk, "name": name,
            "seeds": list(reachers),
            "shared_by": len(reachers),
        })
    bridges.sort(key=lambda b: (-b["shared_by"], b["name"]))
    return [b for b in bridges if b["shared_by"] >= max(2, min_overlap)]


def to_json(
    rel: RelationResult, *, allow_phone: bool = False, phone_key: Optional[str] = None
) -> Dict[str, Any]:
    nodes = []
    for node in rel.nodes:
        data = asdict(node)
        data["cross_platform"] = [
            sanitize_binding(
                binding, allow_phone=allow_phone, phone_key=phone_key
            )
            for binding in node.cross_platform
        ]
        nodes.append(data)
    return {
        "nodes": nodes,
        "edges": [asdict(e) for e in rel.edges],
        "errors": rel.errors,
        "statuses": [asdict(status) for status in rel.statuses],
    }
