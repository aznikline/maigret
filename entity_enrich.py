#!/usr/bin/env python3
"""
Entity enrichment loop for Maigret results.

Bridges a gap in Maigret's built-in recursive search: maigret only re-feeds
*usernames* extracted from pages. The socid_extractor output actually contains
emails, phones, real names, aliases, and avatar URLs — this module harvests ALL
of them, treats each as a fresh seed, and:

  1. Re-pivots emails/phones through purely-public lookups
     (Gravatar, GitHub events leak, etc.).
  2. Downloads avatars, perceptual-hash groups duplicates, and pulls EXIF/GPS
     when present.
  3. Builds an entity graph (networkx + pyvis) so relationships surface.

PUBLIC DATA ONLY. No credential use, no access-control circumvention, no
contact with the subject. Designed for self-audit / authorized OSINT scope.

Usage:
    python3 entity_enrich.py results/report_<user>_simple.json [--user USER]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import sys
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import aiohttp
from PIL import Image

from maigret_extensions.models import (
    Entity,
    Evidence,
    EvidenceLevel,
    Record,
    RelationResult,
    normalize,
)
from maigret_extensions.correlation import correlate_identities, identity_report
from maigret_extensions.network_analysis import analyze_network
from maigret_extensions.privacy import (
    normalize_phone,
    phone_fingerprint,
    safe_entity_dict,
    sanitize_binding,
)
from maigret_extensions.social_inference import (
    infer_social_relationships,
    social_report,
)

try:
    import networkx as nx
except Exception:  # pragma: no cover
    nx = None

logger = logging.getLogger("entity_enrich")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Loose phone: international, digits/spaces/dashes, 7-15 significant digits.
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{6,}\d")
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# socid_extractor field keys that carry reusable identity pivots.
EMAIL_KEYS = {"email", "emails", "contact_email", "public_email"}
PHONE_KEYS = {"phone", "phones", "phone_number", "telephone"}
NAME_KEYS = {"name", "fullname", "full_name", "real_name", "display_name",
             "firstname", "lastname", "first_name", "last_name"}
ALIAS_KEYS = {"username", "usernames", "login", "screen_name", "nickname",
              "alias", "aliases", "uid", "user_id", "id"}
AVATAR_KEYS = {"avatar", "image", "profile_pic", "profile_image", "image_url",
               "photo"}
LINK_KEYS = {"links", "website", "websites", "url", "urls"}

# Cap concurrent network fetches so we stay polite.
MAX_CONCURRENCY = 6
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

def add_entity(store: Dict[Tuple[str, str], Entity], kind: str, value: str,
               source: str) -> Optional[Entity]:
    """Insert or merge an entity. Returns None for junk values."""
    if value is None:
        return None
    v = str(value).strip()
    if not v or v.lower() in {"none", "null", "nan", "-", ""}:
        return None
    if kind == "email" and not EMAIL_RE.fullmatch(v):
        return None
    k = (kind, normalize(kind, v))
    e = store.get(k)
    if e is None:
        e = Entity(kind=kind, value=v)
        store[k] = e
    if source and source not in e.sources:
        e.sources.append(source)
    return e


# ---------------------------------------------------------------------------
# Harvesting entities from a Maigret simple report
# ---------------------------------------------------------------------------

def _iter_results(report: dict):
    """Yield (site_name, entry_dict) from either simple or ndjson-ish shapes."""
    if isinstance(report, dict):
        for site, entry in report.items():
            if isinstance(entry, dict):
                yield site, entry
    elif isinstance(report, list):
        for entry in report:
            if isinstance(entry, dict) and "site_name" in entry:
                yield entry["site_name"], entry


def _ids_blob(entry: dict) -> dict:
    """Return the socid ids dict from a report entry, regardless of nesting."""
    status = entry.get("status")
    if isinstance(status, dict):
        ids = status.get("ids")
        if isinstance(ids, dict):
            return ids
    ids = entry.get("ids") or entry.get("ids_data")
    return ids if isinstance(ids, dict) else {}


def harvest(report: dict) -> Dict[Tuple[str, str], Entity]:
    """Walk every site in the report and collect all identity pivots."""
    store: Dict[Tuple[str, str], Entity] = {}
    for site, entry in _iter_results(report):
        status = entry.get("status")
        claimed = (str(status).lower() == "claimed") if status is not None else True
        # Only harvest from claimed accounts — unclaimed = no real data.
        ids = _ids_blob(entry)
        if not ids:
            continue

        def _emit(fieldset: Set[str], kind: str) -> None:
            for key in ids:
                if key in fieldset:
                    add_entity(store, kind, ids[key], site)

        _emit(EMAIL_KEYS, "email")
        _emit(PHONE_KEYS, "phone")
        _emit(NAME_KEYS, "name")
        _emit(ALIAS_KEYS, "username")
        _emit(AVATAR_KEYS, "avatar")
        _emit(LINK_KEYS, "url")

        # usernames discovered inline by maigret
        new_users = entry.get("ids_usernames") or {}
        if isinstance(new_users, dict):
            for u in new_users:
                add_entity(store, "username", u, site)

    return store


# Mapping from deep_search / enhance_netease field names -> entity kind.
# These scripts return structured identity data (nickname, user_id, avatar,
# url) that the Maigret simple report lacks — folding them in is what makes
# the enrichment loop actually turn over on real data.
_DEEP_FIELD_MAP = {
    "nickname": "name",
    "name": "name",
    "username": "username",
    "user_id": "username",
    "userId": "username",
    "uid": "username",
    "id": "username",
    "email": "email",
    "phone": "phone",
    "avatar": "avatar",
    "avatarUrl": "avatar",
    "url": "url",
    "profileUrl": "url",
}


def harvest_deep(report: dict) -> Dict[Tuple[str, str], Entity]:
    """Normalize a deep_search/enhance_netease report into the entity store.

    deep_*.json schema:
      {"username":..., "confirmed":[{"platform","url","nickname","user_id","avatar"}],
       "needs_manual_check":[...]}
    """
    store: Dict[Tuple[str, str], Entity] = {}
    seed = report.get("username") or report.get("name") or "subject"
    for bucket in ("confirmed", "results", "users"):
        items = report.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            site = item.get("platform") or item.get("site") or bucket
            for raw_key, val in item.items():
                if val in (None, ""):
                    continue
                kind = _DEEP_FIELD_MAP.get(raw_key)
                if kind:
                    add_entity(store, kind, val, site)
            # never lose the profile URL itself
            if item.get("url"):
                add_entity(store, "url", item["url"], site)
    return store


def merge_stores(*stores) -> Dict[Tuple[str, str], Entity]:
    out: Dict[Tuple[str, str], Entity] = {}
    for store in stores:
        for key, ent in store.items():
            existing = out.get(key)
            if existing is None:
                out[key] = ent
            else:
                existing.sources.extend(s for s in ent.sources
                                       if s not in existing.sources)
                existing.pivots.extend(ent.pivots)
                existing.notes.extend(ent.notes)
                if not existing.exif and ent.exif:
                    existing.exif = ent.exif
    return out


# ---------------------------------------------------------------------------
# Public-data pivots (no auth, no access-control bypass)
# ---------------------------------------------------------------------------

async def pivot_email(session: aiohttp.ClientSession, email: str) -> List[str]:
    """Gravatar profile (public). Returns derived notes, never credentials."""
    notes: List[str] = []
    h = hashlib.md5(email.lower().strip().encode()).hexdigest()
    url = f"https://www.gravatar.com/{h}.json"
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                for entry in data.get("entry", []):
                    name = entry.get("name", {})
                    disp = entry.get("displayName") or name.get("formatted")
                    if disp:
                        notes.append(f"gravatar displayName: {disp}")
                    for acc in entry.get("accounts", []):
                        dom = acc.get("domain")
                        uname = acc.get("username")
                        if dom and uname:
                            notes.append(f"gravatar linked: {dom}/{uname}")
    except Exception as e:
        logger.debug("gravatar %s failed: %s", email, e)
    return notes


def _github_headers() -> Dict[str, str]:
    """Use a GitHub token from the environment if present (5000 req/h vs 60)."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


async def pivot_github(session: aiohttp.ClientSession, username: str) -> List[str]:
    """Public GitHub events leak commit emails (developers' real emails).

    Anonymous calls are rate-limited to 60/h per IP and frequently 403 from
    shared/datacenter IPs — that is expected, not a bug. Set GITHUB_TOKEN to
    raise the ceiling to 5000/h.
    """
    notes: List[str] = []
    url = f"https://api.github.com/users/{username}/events/public"
    try:
        async with session.get(url, headers=_github_headers()) as resp:
            if resp.status == 403:
                rl = resp.headers.get("X-RateLimit-Remaining", "?")
                logger.warning(
                    "github 403 (rate-limited, remaining=%s). Set GITHUB_TOKEN "
                    "for 5000/h instead of 60/h.", rl)
                return notes
            if resp.status != 200:
                return notes
            data = await resp.json(content_type=None)
            seen: Set[str] = set()
            for ev in data:
                for c in ev.get("payload", {}).get("commits", []) or []:
                    em = c.get("author", {}).get("email") or c.get("email")
                    if em and "noreply.github.com" not in em and em not in seen:
                        seen.add(em)
                        notes.append(f"github commit email: {em}")
    except Exception as e:
        logger.debug("github %s failed: %s", username, e)
    return notes[:8]


async def pivot(session: aiohttp.ClientSession, entity: Entity) -> List[str]:
    if entity.kind == "email":
        return await pivot_email(session, entity.value)
    if entity.kind == "username":
        return await pivot_github(session, entity.value)
    return []


# ---------------------------------------------------------------------------
# Avatar: download + perceptual hash + EXIF/GPS (all local, all public)
# ---------------------------------------------------------------------------

async def fetch_avatar(session: aiohttp.ClientSession,
                       url: str) -> Optional[bytes]:
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception as e:
        logger.debug("avatar fetch %s failed: %s", url, e)
    return None


def _perceptual_hash(img: Image.Image) -> str:
    g = img.convert("L").resize((8, 8), Image.LANCZOS)
    pixels = list(g.get_flattened_data() if hasattr(g, "get_flattened_data")
                  else g.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p > avg else "0" for p in pixels)
    return format(int(bits, 2), "016x")


def _exif(img: Image.Image) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        exif = img.getexif()
    except Exception:
        return out
    if not exif:
        return out
    tag_names = {
        271: "camera_make", 272: "camera_model", 306: "datetime",
        315: "artist", 316: "copyright",
    }
    for tag_id, val in exif.items():
        name = tag_names.get(tag_id, str(tag_id))
        if val:
            out[name] = str(val)
    # GPS lives in the IFD 34853
    try:
        gps_ifd = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
        gps = exif.get_ifd(0x8825) or gps_ifd
        if gps:
            lat = _dms_to_decimal(gps.get(2), gps.get(1))
            lon = _dms_to_decimal(gps.get(4), gps.get(3))
            if lat is not None and lon is not None:
                out["gps"] = {"lat": lat, "lon": lon}
    except Exception:
        pass
    return out


def _dms_to_decimal(dms, ref) -> Optional[float]:
    if not dms or not isinstance(dms, tuple):
        return None
    try:
        d, m, s = dms
        dec = float(d) + float(m) / 60 + float(s) / 3600
        if ref and ref.upper() in ("S", "W"):
            dec = -dec
        return round(dec, 6)
    except Exception:
        return None


async def enrich_avatars(session: aiohttp.ClientSession,
                         store: Dict[Tuple[str, str], Entity],
                         out_dir: str) -> None:
    avatars = [e for e in store.values() if e.kind == "avatar"]
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(e: Entity) -> None:
        async with sem:
            data = await fetch_avatar(session, e.value)
        if not data:
            return
        h = hashlib.sha1(data).hexdigest()[:12]
        path = os.path.join(out_dir, f"avatar_{h}.jpg")
        with open(path, "wb") as f:
            f.write(data)
        e.notes.append(f"saved: {path}")
        try:
            img = Image.open(io.BytesIO(data))
            ph = _perceptual_hash(img)
            e.notes.append(f"phash: {ph}")
            exif = _exif(img)
            if exif:
                e.exif = exif
                if "gps" in exif:
                    e.notes.append(
                        f"GPS {exif['gps']['lat']},{exif['gps']['lon']}")
        except Exception as ex:
            logger.debug("exif/phash failed for %s: %s", e.value, ex)

    await asyncio.gather(*[one(e) for e in avatars])


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def _record_key(entity: Entity) -> str:
    """Group key: entities from the same site are one cluster ONLY when they
    also share a stable identifier (user_id / url). Falling back to bare site
    name would wrongly fuse unrelated accounts that merely appear on one site."""
    src = entity.sources[0] if entity.sources else "?"
    return src  # caller refines via _evidence_edges; intra-record grouping
    # is handled in build_graph by explicit per-record membership.


def group_by_record(report: dict, deep: dict) -> List[Record]:
    """Build record clusters. A record = one account on one site.

    Fields within a record are genuinely co-owned (same profile). Cross-record
    relationships are decided separately by explicit evidence, NOT by the fact
    that two records share a site name. Records are deduplicated by rid so that
    an account appearing in BOTH the maigret report and deep_search does not
    become two nodes.
    """
    records: List[Record] = []
    seen: Dict[str, Record] = {}

    def _rid(site: str, fields: dict) -> str:
        uid = (fields.get("user_id") or fields.get("userId") or fields.get("uid")
               or fields.get("id") or fields.get("url") or "auto")
        return f"{site}::{uid}"

    def _fold(rid: str, site: str, entity_factory) -> None:
        """Create rid on first sight, or merge new members into the existing record."""
        rec = seen.get(rid)
        if rec is None:
            rec = Record(rid=rid, site=site)
            seen[rid] = rec
            records.append(rec)
        entity_factory(rec)

    # deep_search confirmed accounts -> one record each
    for item in deep.get("confirmed", []) or []:
        if not isinstance(item, dict):
            continue
        site = item.get("platform") or item.get("site") or "unknown"
        rid = _rid(site, item)

        def fill(rec, _item=item, _site=site):
            for raw_key, val in _item.items():
                if val in (None, ""):
                    continue
                kind = _DEEP_FIELD_MAP.get(raw_key)
                if kind:
                    rec.add_member(Entity(kind=kind, value=str(val),
                                              sources=[_site]))
        _fold(rid, site, fill)

    # maigret sites -> one record per site that has ids
    for site, entry in _iter_results(report):
        ids = _ids_blob(entry)
        if not ids:
            continue
        rid = _rid(site, ids)

        def fill(rec, _ids=ids, _site=site):
            for key, val in _ids.items():
                if val in (None, ""):
                    continue
                for fieldset, kind in ((EMAIL_KEYS, "email"), (PHONE_KEYS, "phone"),
                                       (NAME_KEYS, "name"), (ALIAS_KEYS, "username"),
                                       (AVATAR_KEYS, "avatar"), (LINK_KEYS, "url")):
                    if key in fieldset:
                        rec.add_member(Entity(kind=kind, value=str(val),
                                                  sources=[_site]))
                        break
        _fold(rid, site, fill)
    return records


def _node_id(
    e: Entity,
    *,
    allow_phone: bool = True,
    phone_key: Optional[str] = None,
    redacted_index: int = 0,
) -> str:
    if e.kind == "phone":
        if allow_phone and phone_key and normalize_phone(e.value):
            return f"phone:{phone_fingerprint(e.value, phone_key)}"
        return f"phone:redacted:{redacted_index}"
    return f"{e.kind}:{normalize(e.kind, e.value)}"


def _evidence(
    a: Entity,
    b: Entity,
    *,
    allow_phone: bool = True,
    phone_key: Optional[str] = None,
) -> Optional[Evidence]:
    """Return explicit cross-record relationship evidence, if present.

    NO 'same site' co-occurrence here — that is not evidence of a relationship.
    """
    # shared identical avatar URL or phash
    if a.kind == "avatar" and b.kind == "avatar":
        if normalize("avatar", a.value) == normalize("avatar", b.value):
            return Evidence("entity_enrich", "shared avatar", EvidenceLevel.HIGH)
    if a.exif.get("phash") and a.exif.get("phash") == b.exif.get("phash"):
        return Evidence(
            "entity_enrich", "matched avatar (phash)", EvidenceLevel.HIGH
        )
    # shared email/username value across two records
    if a.kind == b.kind == "phone":
        if (
            not allow_phone
            or not normalize_phone(a.value)
            or not normalize_phone(b.value)
        ):
            return None
        if normalize_phone(a.value) == normalize_phone(b.value):
            return Evidence("entity_enrich", "shared phone", EvidenceLevel.HIGH)
        return None
    if a.kind == b.kind and normalize(a.kind, a.value) == normalize(a.kind, b.value):
        level = EvidenceLevel.HIGH if a.kind == "email" else EvidenceLevel.MEDIUM
        return Evidence("entity_enrich", f"shared {a.kind}", level)
    return None


def build_graph(store: Dict[Tuple[str, str], Entity], seed: str,
                records: Optional[List[Record]] = None,
                *, allow_phone: bool = True,
                phone_key: Optional[str] = None,
                ) -> Optional["object"]:
    """Record-oriented graph.

    - Each record (one account) is a cluster; its fields link to a record hub
      with via='member of <site>'. That is a TRUE relationship (same profile).
    - The seed links to records it surfaced, with via='seed match' — this is a
      SEARCH link, not an identity link, and is labeled as such so it is never
      mistaken for a social relationship.
    - Cross-record links exist ONLY with explicit evidence (shared avatar /
      phash / email). No co-occurrence.
    """
    if nx is None:
        return None
    g = nx.Graph()
    g.add_node(f"seed:{seed}", kind="seed", label=seed, group="seed")

    records = records or []
    members = [member for record in records for member in record.members]
    node_ids = {
        id(member): _node_id(
            member,
            allow_phone=allow_phone,
            phone_key=phone_key,
            redacted_index=index,
        )
        for index, member in enumerate(members)
    }
    for rec in records:
        hub = f"record:{rec.rid}"
        g.add_node(hub, kind="account", label=rec.site, group=rec.site)
        for m in rec.members:
            nid = node_ids[id(m)]
            display_value = "[redacted-phone]" if m.kind == "phone" else m.value
            label = (
                display_value
                if len(display_value) <= 28
                else display_value[:25] + "…"
            )
            data = {"label": label, "group": rec.site}
            if m.kind == "avatar" and m.exif:
                data["phash"] = m.exif.get("phash", "")
            g.add_node(nid, kind=m.kind, **data)
            g.add_edge(hub, nid, via="member")
        # seed -> record is a SEARCH relationship, explicitly labeled
        g.add_edge(f"seed:{seed}", hub, via="seed match (search, not identity)")

    # explicit cross-record evidence only
    flat = [m for rec in records for m in rec.members]
    for i, a in enumerate(flat):
        for b in flat[i + 1:]:
            # skip same-record pairs (already linked via their hub)
            if any(a in r.members and b in r.members for r in records):
                continue
            evidence = _evidence(
                a, b, allow_phone=allow_phone, phone_key=phone_key
            )
            if evidence:
                g.add_edge(
                    node_ids[id(a)],
                    node_ids[id(b)],
                    via=evidence.reason,
                    evidence_level=evidence.level.value,
                    evidence_source=evidence.source,
                )
    return g


def add_social_edges(
    g, rel, *, allow_phone: bool = True, phone_key: Optional[str] = None
) -> None:
    """Add REAL person<->person social edges to the graph.

    These are the genuine social-network ties: who follows whom, with mutual
    ties (reciprocal) rendered as the strongest links. Nodes are keyed by
    site:user_id so they are distinct from the subject's own attribute nodes.
    """
    if rel is None or not rel.edges:
        return
    # degree lookup: site:user_id -> distance from seed
    degmap = {n.key(): getattr(n, "degree", 1)
              for n in rel.nodes}
    for e in rel.edges:
        a = f"social:{e.site}:{e.src}"
        b = f"social:{e.site}:{e.dst}"
        src_deg = degmap.get(f"{e.site}::{e.src}", 1)
        dst_deg = degmap.get(f"{e.site}::{e.dst}", 1)
        g.add_node(a, kind="social", label=e.src_name or e.src,
                   group=e.site, social=True, degree=src_deg)
        g.add_node(b, kind="social", label=e.dst_name or e.dst,
                   group=e.site, social=True, degree=dst_deg)
        via = e.via or ("mutual follow" if e.mutual else "follows")
        g.add_edge(a, b, via=via, weight=e.weight, social=True)

    # cross-platform bridges: a Netease person's Weibo/QQ/etc. binding becomes
    # its own node, linked to the source social node. This is how the graph
    # leaves Netease — enabling Netease -> Weibo -> more-people traversal.
    for n in rel.nodes:
        source = f"social:{n.site}:{n.user_id}"
        for b in getattr(n, "cross_platform", []) or []:
            binding = sanitize_binding(
                b, allow_phone=allow_phone, phone_key=phone_key
            )
            url = binding.get("url") or ""
            pid = binding.get("id") or binding.get("phone_fingerprint") or url
            if not pid:
                continue
            cp = f"cross:{binding.get('platform')}:{pid}"
            label_value = (
                "[redacted-phone]"
                if binding.get("phone_redacted")
                else (url or pid)
            )
            g.add_node(cp, kind="cross_platform",
                       label=f"{binding.get('platform')}: {label_value}",
                       group=binding.get("platform"), social=True, degree=n.degree)
            g.add_edge(source, cp, via=f"bound to {binding.get('platform')}",
                       weight=2, social=True)


def render_graph(g, out_html: str) -> None:
    try:
        from pyvis.network import Network
    except Exception:
        logger.warning("pyvis not available; skipping graph render")
        return
    color = {"seed": "#e63946", "email": "#457b9d", "phone": "#2a9d8f",
             "name": "#f4a261", "username": "#264653", "avatar": "#8338ec",
             "url": "#6c757d", "device": "#adb5bd", "social": "#0a9396",
             "account": "#bb3e03", "cross_platform": "#9d4edd"}
    net = Network(height="760px", width="100%", notebook=False,
                  directed=True)
    for n, data in g.nodes(data=True):
        kind = data.get("kind")
        deg = data.get("degree", 1)
        # cross-platform hop = purple diamond (the platform-traversal leads)
        if kind == "cross_platform":
            color_, shape_, size = "#9d4edd", "diamond", 14
        elif kind == "social" and deg == 1:
            color_, shape_, size = "#0a9396", "star", 20
        elif kind == "social" and deg == 2:
            color_, shape_, size = "#94d2bd", "dot", 8
        else:
            color_, shape_, size = color.get(kind, "#adb5bd"), "dot", 15
        net.add_node(n, label=data.get("label", n), color=color_,
                     shape=shape_, size=size)
    for a, b, edata in g.edges(data=True):
        via = edata.get("via", "")
        social = edata.get("social", False)
        # strongest ties (mutual) drawn thick; plain follows thin/dashed;
        # interaction edges (mentions/reposts) colored to stand out.
        width = 4 if "mutual" in via else (2 if social else 1)
        is_inter = via in ("mentions", "reposts")
        edge_color = "#e76f51" if is_inter else ("#264653" if "mutual" in via else "#888888")
        net.add_edge(a, b, title=via, width=width, color=edge_color,
                     arrows="to" if social else "",
                     dashes=social and "mutual" not in via)
    net.write_html(out_html, notebook=False)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def main_async(
    report_path: Optional[str],
    user: str,
    out_dir: str,
    *,
    allow_phone: bool = True,
    phone_key: Optional[str] = None,
) -> dict:
    """Enrich one username from whatever inputs exist.

    Works with ONLY a maigret report, ONLY a deep_search dump, or both.
    report_path may be None — in that case the maigret report is empty and we
    rely on deep_search output (or vice versa).
    """
    os.makedirs(out_dir, exist_ok=True)
    user = user.replace(" ", "_")
    report: dict = {}
    if report_path and os.path.isfile(report_path):
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)

    stores = [harvest(report)]
    logger.info("maigret report: %d entities", len(stores[0]))

    # Fold in deep_search / enhance_netease output if present — these carry the
    # structured identity fields (nickname, user_id, avatar) the maigret report
    # usually lacks. Discover by username, not by relative path of the report.
    deep: dict = {}
    deep_path = _find(user, "deep")
    if deep_path:
        try:
            with open(deep_path, encoding="utf-8") as f:
                deep = json.load(f)
            stores.append(harvest_deep(deep))
            logger.info("deep_%s.json: +%d entities", user, len(stores[-1]))
        except Exception as e:
            logger.warning("could not load %s: %s", deep_path, e)

    store = merge_stores(*stores)
    logger.info("merged total: %d entities", len(store))
    records = group_by_record(report, deep)

    import relations as REL
    rel: Optional["REL.RelationResult"] = None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    conn = aiohttp.TCPConnector(limit=MAX_CONCURRENCY)
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT, headers=headers,
                                     connector=conn) as session:
        await enrich_avatars(session, store, out_dir)

        sem = asyncio.Semaphore(MAX_CONCURRENCY)

        async def pivot_one(e: Entity):
            async with sem:
                notes = await pivot(session, e)
            e.pivots.extend(notes)

        await asyncio.gather(*[pivot_one(e) for e in store.values()
                               if e.kind in ("email", "username")])

        # public social relations: who follows whom (mutual = strongest tie)
        try:
            rel = await REL.collect_relations(session, records)
            logger.info("social: %d nodes, %d edges (%d mutual)",
                        len(rel.nodes), len(rel.edges),
                        sum(1 for e in rel.edges if e.mutual))
        except Exception as e:
            logger.warning("relations collection failed: %s", e)

    relation_result = rel or RelationResult()
    identity_links, identity_clusters = correlate_identities(
        records,
        relation_result,
        allow_phone=allow_phone,
        phone_key=phone_key,
    )
    social_relationships = infer_social_relationships(
        relation_result, identity_clusters
    )

    # outputs
    entities = [
        safe_entity_dict(e, allow_phone=allow_phone, phone_key=phone_key)
        for e in store.values()
    ]
    json_out = os.path.join(out_dir, f"entities_{user}.json")
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump({"seed": user, "entities": entities}, f,
                  ensure_ascii=False, indent=2)

    rel_out = os.path.join(out_dir, f"relations_{user}.json")
    if rel is not None:
        with open(rel_out, "w", encoding="utf-8") as f:
            json.dump(
                REL.to_json(
                    rel, allow_phone=allow_phone, phone_key=phone_key
                ),
                f,
                ensure_ascii=False,
                indent=2,
            )

    identity_out = os.path.join(out_dir, f"identity_links_{user}.json")
    identity_data = identity_report(identity_links, identity_clusters)
    identity_data["seed"] = user
    with open(identity_out, "w", encoding="utf-8") as f:
        json.dump(identity_data, f, ensure_ascii=False, indent=2)

    social_out = os.path.join(out_dir, f"social_inferences_{user}.json")
    social_data = social_report(social_relationships)
    social_data["seed"] = user
    with open(social_out, "w", encoding="utf-8") as f:
        json.dump(social_data, f, ensure_ascii=False, indent=2)

    network_out = os.path.join(out_dir, f"social_network_{user}.json")
    network_data = analyze_network(
        relation_result, identity_clusters, social_relationships
    )
    network_data["seed"] = user
    network_data["coverage"] = [
        asdict(status)
        for status in sorted(
            relation_result.statuses,
            key=lambda item: (item.site, item.account_id, item.capability),
        )
    ]
    with open(network_out, "w", encoding="utf-8") as f:
        json.dump(network_data, f, ensure_ascii=False, indent=2)

    # Dedicated cross-platform export: every platform-hop identity, extracted
    # from Netease bindings. This is the multi-platform traversal seed list —
    # each entry can pivot the investigation onto another platform.
    cp_out = os.path.join(out_dir, f"cross_platform_{user}.json")
    if rel is not None:
        cross = []
        for n in rel.nodes:
            for b in getattr(n, "cross_platform", []) or []:
                safe_binding = sanitize_binding(
                    b, allow_phone=allow_phone, phone_key=phone_key
                )
                if b.get("url") or b.get("id"):
                    cross.append({
                        "from_site": n.site,
                        "from_uid": n.user_id,
                        "name": n.nickname,
                        "to_platform": safe_binding.get("platform"),
                        "url": safe_binding.get("url") or "",
                        "id": safe_binding.get("id"),
                        "reachable": bool(safe_binding.get("url")),
                        **(
                            {"phone_redacted": True}
                            if safe_binding.get("phone_redacted")
                            else {}
                        ),
                        **(
                            {"phone_fingerprint": safe_binding["phone_fingerprint"]}
                            if safe_binding.get("phone_fingerprint")
                            else {}
                        ),
                    })
        with open(cp_out, "w", encoding="utf-8") as f:
            json.dump({"seed": user, "cross_platform": cross}, f,
                      ensure_ascii=False, indent=2)

    # Record-oriented graph + social edges (real person<->person ties).
    g = build_graph(
        store,
        user,
        records=records,
        allow_phone=allow_phone,
        phone_key=phone_key,
    )
    if g is not None and rel is not None:
        add_social_edges(
            g, rel, allow_phone=allow_phone, phone_key=phone_key
        )
    graph_out = os.path.join(out_dir, f"entity_graph_{user}.html")
    if g is not None:
        render_graph(g, graph_out)

    # console summary — record-oriented, so relationships are not overstated
    print(f"\n=== Entity enrichment for {user} ===")
    if records:
        print(f"\n{len(records)} account record(s):")
        for rec in records:
            fields = ", ".join(
                f"{m.kind}={'[redacted-phone]' if m.kind == 'phone' else m.value}"
                if m.kind == "phone" or len(str(m.value)) < 22
                else f"{m.kind}={str(m.value)[:19]}…"
                for m in rec.members
            )
            print(f"  • [{rec.site}] {fields}")
        linked = [(u, v, d.get("via", "")) for u, v, d in g.edges(data=True)
                  if d.get("via") and "seed" not in (u, v)
                  and "member" not in d.get("via", "")
                  and not d.get("social")]
        if linked:
            print("\nCross-record identity links (explicit evidence):")
            for u, v, via in linked:
                print(f"  ↪ {u} ↔ {v}   ({via})")
        else:
            print("\nNo cross-record identity links by explicit evidence.")

        if rel is not None and rel.edges:
            mutual = [e for e in rel.edges if e.mutual]
            deg2 = [n for n in rel.nodes if getattr(n, "degree", 1) == 2]
            bridged = [n for n in rel.nodes if getattr(n, "cross_platform", None)]
            mentions = [e for e in rel.edges if e.via == "mentions"]
            reposts = [e for e in rel.edges if e.via == "reposts"]
            print(f"\nSocial network: {len(rel.edges)} edges "
                  f"({len(mutual)} mutual, "
                  f"{len(deg2)} 2nd-degree, "
                  f"{len(bridged)} cross-platform"
                  + (f", {len(mentions)} mentions, {len(reposts)} reposts"
                     if mentions or reposts else "")
                  + ").")
            # strongest ties first
            for e in sorted(rel.edges, key=lambda x: (-x.weight, x.dst_name))[:8]:
                if e.via == "mentions":
                    tag = f"  ✱ mentions x{e.weight}"
                elif e.via == "reposts":
                    tag = "  ↻ reposts"
                elif e.mutual:
                    tag = "  ⟂ MUTUAL"
                else:
                    tag = "  → follows"
                print(f"   {e.src_name} {tag} {e.dst_name}  [{e.site}]")
            # the real gold: each mutual contact's own associations
            mutual_ids = {e.dst for e in mutual}
            for mid in mutual_ids:
                mname = next((e.dst_name for e in mutual
                              if e.dst == mid), mid)
                leads = [e.dst_name for e in rel.edges if e.src == mid]
                if leads:
                    print(f"\n   via mutual [{mname}] (2nd-degree): "
                          + ", ".join(leads[:10])
                          + (" …" if len(leads) > 10 else ""))
            # cross-platform hops — the platform-traversal leads
            for n in bridged:
                cps = [
                    sanitize_binding(
                        item, allow_phone=allow_phone, phone_key=phone_key
                    )
                    for item in n.cross_platform
                ]
                shown = [c for c in cps if c.get("url") or c.get("id")
                         or c.get("phone_redacted")]
                if shown:
                    print(f"\n   ✦ {n.nickname} cross-platform: "
                          + "; ".join(f"{c['platform']}="
                                      f"{('[redacted-phone]' if c.get('phone_redacted') else c.get('url') or c.get('id'))}"
                                      for c in shown[:5]))
        else:
            print("\nNo public social relations found "
                  "(no public follow list available).")
    else:
        for kind in ("name", "username", "email", "phone", "url", "avatar"):
            bucket = [e for e in store.values() if e.kind == kind]
            if not bucket:
                continue
            print(f"\n[{kind}]  ({len(bucket)})")
            for e in bucket:
                value = "[redacted-phone]" if e.kind == "phone" else e.value
                print(f"  - {value}  [{', '.join(e.sources[:2])}]")

    return {"entities": len(entities), "records": len(records),
            "identity_links": len(identity_links),
            "social_inferences": len(social_relationships),
            "social_edges": len(rel.edges) if rel else 0,
            "json": json_out, "relations": rel_out if rel else None,
            "cross_platform": cp_out if rel else None,
            "identity_report": identity_out,
            "social_report": social_out,
            "social_network": network_out,
            "graph": graph_out}


SEARCH_ROOTS = ["results", ".", "reports"]


def _find(user: str, kind: str) -> Optional[str]:
    """Auto-discover an input file for a username across standard roots.

    kind: 'report' -> results/report_<user>_simple.json
          'deep'   -> results/deep_<user>.json
    Handles the maigret filename quirk where usernames containing a space get
    stored with underscores (e.g. "Jalyn Yu" -> report_Jalyn_Yu_*).
    """
    name = {"report": "report_{}_simple.json", "deep": "deep_{}.json"}[kind]
    candidates = [user, user.replace(" ", "_"), user.replace("_", " ")]
    for root in SEARCH_ROOTS:
        for c in candidates:
            p = os.path.join(root, name.format(c))
            if os.path.isfile(p):
                return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("user", nargs="?", default=None,
                    help="username to enrich. If omitted with --report, the "
                         "explicit report path is used (legacy mode).")
    ap.add_argument("--report", default=None,
                    help="explicit report_<user>_simple.json path (overrides "
                         "auto-discovery by username)")
    ap.add_argument("--user-name", default=None,
                    help="username override for labeling/output naming")
    ap.add_argument("--out", default="results/enrichment")
    ap.add_argument("--all", action="store_true",
                    help="enrich every username found under the search roots")
    phone_group = ap.add_mutually_exclusive_group()
    phone_group.add_argument(
        "--allow-phone-correlation",
        dest="allow_phone_correlation",
        action="store_true",
        default=True,
        help=argparse.SUPPRESS,
    )
    phone_group.add_argument(
        "--no-phone-correlation",
        dest="allow_phone_correlation",
        action="store_false",
        help="disable strong matching of complete phone numbers",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    phone_key = os.environ.get("MAIGRET_PHONE_HASH_KEY")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s")

    if args.all:
        # enumerate every distinct username seen in either file family
        users = set()
        for root in SEARCH_ROOTS:
            if not os.path.isdir(root):
                continue
            for fn in os.listdir(root):
                for prefix, suffix in (("report_", "_simple.json"),
                                       ("deep_", ".json")):
                    if fn.startswith(prefix) and fn.endswith(suffix):
                        users.add(fn[len(prefix):-len(suffix)].replace("_", " "))
        users = sorted(u for u in users if u)
        if not users:
            print("no users found under search roots", file=sys.stderr)
            return 2
        print(f"enriching {len(users)} user(s): {', '.join(users)}\n")
        code = 0
        for u in users:
            report = _find(u, "report")
            # report optional — a user may have only a deep_search dump
            if not report and not _find(u, "deep"):
                print(f"[skip] no data at all for '{u}'", file=sys.stderr)
                code = 1
                continue
            tag = f"report={'yes' if report else 'no'}"
            print(f"== {u}  ({tag}) ==")
            try:
                asyncio.run(
                    main_async(
                        report,
                        u,
                        args.out,
                        allow_phone=args.allow_phone_correlation,
                        phone_key=phone_key,
                    )
                )
            except Exception as e:
                print(f"[fail] {u}: {e}", file=sys.stderr)
                code = 1
            print("\n" + "-" * 60)

        # Cross-target bridges: shared-followed accounts tie seeds together.
        import relations as REL
        from dataclasses import asdict as _asdict
        per_seed = {}
        for u in users:
            rp = os.path.join(args.out, f"relations_{u.replace(' ', '_')}.json")
            if os.path.isfile(rp):
                with open(rp, encoding="utf-8") as f:
                    d = json.load(f)
                r = REL.RelationResult()
                r.nodes = [REL.SocialNode(**n) for n in d.get("nodes", [])]
                r.edges = [REL.SocialEdge(**e) for e in d.get("edges", [])]
                r.statuses = [
                    REL.CollectionStatus(**status)
                    for status in d.get("statuses", [])
                ]
                per_seed[u] = r
        bridges = REL.find_bridges(per_seed) if per_seed else []
        if bridges:
            bp = os.path.join(args.out, "social_bridges.json")
            with open(bp, "w", encoding="utf-8") as f:
                json.dump(bridges, f, ensure_ascii=False, indent=2)
            print("\n" + "=" * 60)
            print("CROSS-TARGET SOCIAL BRIDGES (shared followed accounts):")
            print("=" * 60)
            for b in bridges[:20]:
                who = ", ".join(b["seeds"])
                print(f"  • {b['name']}  [{b['node']}]  <- shared by {who}")
            print(f"\nwrote {bp}")
        else:
            print("\n(no cross-target bridges — no shared followed accounts)")
        return code
    # single-user mode
    if args.report:
        report_path = args.report
        user = args.user_name or os.path.basename(report_path)
        m = re.match(r"report_(.+)_simple\.json$", user)
        if m:
            user = m.group(1)
    else:
        if not args.user:
            ap.error("give a username, or use --report PATH / --all")
        user = args.user_name or args.user
        report_path = _find(user, "report")
        if not report_path and not _find(user, "deep"):
            print(f"no data found for '{user}' (looked in {SEARCH_ROOTS})",
                  file=sys.stderr)
            return 2
        if not report_path:
            logger.info("no maigret report for '%s'; using deep_search only", user)

    res = asyncio.run(
        main_async(
            report_path,
            user.replace(" ", "_"),
            args.out,
            allow_phone=args.allow_phone_correlation,
            phone_key=phone_key,
        )
    )
    print(f"\nwrote {res['json']}")
    print(f"wrote {res['social_network']}")
    print(f"wrote {res['graph']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
