"""Evidence-driven account correlation without network lookups."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Tuple

from .models import (
    Entity,
    IdentityCluster,
    IdentityDecision,
    IdentityEvidence,
    IdentityLink,
    Record,
    RelationResult,
    normalize,
)
from .privacy import normalize_phone, phone_fingerprint


WEIGHTS = {
    "explicit_binding": 0.99,
    "phone": 0.95,
    "email": 0.90,
    "avatar_phash": 0.80,
    "avatar_url": 0.75,
    "username": 0.25,
}


def _opaque(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decision(score: float) -> IdentityDecision:
    if score >= 0.90:
        return IdentityDecision.CONFIRMED
    if score >= 0.70:
        return IdentityDecision.LIKELY
    if score >= 0.40:
        return IdentityDecision.POSSIBLE
    return IdentityDecision.WEAK


def _dedupe_evidence(items: Iterable[IdentityEvidence]) -> List[IdentityEvidence]:
    strongest: Dict[str, IdentityEvidence] = {}
    for item in items:
        family = "avatar" if item.kind.startswith("avatar_") else item.kind
        current = strongest.get(family)
        if current is None or item.confidence > current.confidence:
            strongest[family] = item
    return sorted(
        strongest.values(), key=lambda item: (-item.confidence, item.kind, item.source)
    )


def _score(items: Iterable[IdentityEvidence]) -> Tuple[float, List[IdentityEvidence]]:
    evidence = _dedupe_evidence(items)
    remaining = math.prod(1.0 - item.confidence for item in evidence)
    return round(1.0 - remaining, 6), evidence


def _values(record: Record, kind: str) -> List[Entity]:
    return [entity for entity in record.members if entity.kind == kind]


def _record_evidence(
    left: Record,
    right: Record,
    *,
    allow_phone: bool,
    phone_key: Optional[str],
) -> List[IdentityEvidence]:
    evidence = []
    left_emails = {normalize("email", item.value) for item in _values(left, "email")}
    right_emails = {normalize("email", item.value) for item in _values(right, "email")}
    for value in sorted(left_emails & right_emails):
        evidence.append(
            IdentityEvidence(
                "email", "public_profile", WEIGHTS["email"], "same public email", _opaque(value)
            )
        )

    left_avatars = _values(left, "avatar")
    right_avatars = _values(right, "avatar")
    left_hashes = {str(item.exif.get("phash")) for item in left_avatars if item.exif.get("phash")}
    right_hashes = {str(item.exif.get("phash")) for item in right_avatars if item.exif.get("phash")}
    for value in sorted(left_hashes & right_hashes):
        evidence.append(
            IdentityEvidence(
                "avatar_phash", "public_profile", WEIGHTS["avatar_phash"],
                "same avatar perceptual hash", _opaque(value),
            )
        )
    left_urls = {normalize("avatar", item.value) for item in left_avatars}
    right_urls = {normalize("avatar", item.value) for item in right_avatars}
    for value in sorted(left_urls & right_urls):
        evidence.append(
            IdentityEvidence(
                "avatar_url", "public_profile", WEIGHTS["avatar_url"],
                "same avatar URL", _opaque(value),
            )
        )

    left_users = {item.value.strip().casefold() for item in _values(left, "username")}
    right_users = {item.value.strip().casefold() for item in _values(right, "username")}
    for value in sorted((left_users & right_users) - {""}):
        evidence.append(
            IdentityEvidence(
                "username", "public_profile", WEIGHTS["username"],
                "same username", _opaque(value),
            )
        )

    if allow_phone:
        left_phones = {
            normalize_phone(item.value)
            for item in _values(left, "phone")
            if normalize_phone(item.value)
        }
        right_phones = {
            normalize_phone(item.value)
            for item in _values(right, "phone")
            if normalize_phone(item.value)
        }
        for value in sorted(left_phones & right_phones):
            evidence.append(
                IdentityEvidence(
                    "phone", "authorized_observation", WEIGHTS["phone"],
                    "same complete phone number",
                    phone_fingerprint(value, phone_key) if phone_key else "",
                )
            )
    return evidence


def _binding_target(binding: dict) -> Optional[str]:
    platform = str(binding.get("platform") or "").lower()
    if platform == "phone":
        return None
    target_id = str(binding.get("id") or "").strip()
    url = str(binding.get("url") or "")
    if platform in {"weibo", "weibo_legacy"}:
        match = re.search(r"/u/(\d+)|/(\d{6,})$", url)
        target_id = (match.group(1) or match.group(2)) if match else target_id
        platform = "Weibo"
    else:
        platform = {
            "qq": "QQ",
            "wechat": "WeChat",
            "netease_mail": "NeteaseMail",
        }.get(platform, platform.title())
    return f"{platform}::{target_id}" if platform and target_id else None


def correlate_identities(
    records: List[Record],
    relations: Optional[RelationResult] = None,
    *,
    allow_phone: bool = True,
    phone_key: Optional[str] = None,
) -> Tuple[List[IdentityLink], List[IdentityCluster]]:
    evidence_by_pair: Dict[Tuple[str, str], List[IdentityEvidence]] = {}

    def add(left: str, right: str, evidence: IdentityEvidence) -> None:
        if not left or not right or left == right:
            return
        pair = tuple(sorted((left, right)))
        evidence_by_pair.setdefault(pair, []).append(evidence)

    for left, right in combinations(sorted(records, key=lambda r: r.rid), 2):
        if left.site == right.site:
            continue
        for evidence in _record_evidence(
            left,
            right,
            allow_phone=allow_phone,
            phone_key=phone_key,
        ):
            add(left.rid, right.rid, evidence)

    for node in (relations or RelationResult()).nodes:
        for binding in node.cross_platform:
            target = _binding_target(binding)
            if target:
                add(
                    node.key(),
                    target,
                    IdentityEvidence(
                        "explicit_binding",
                        f"{node.site}_binding",
                        WEIGHTS["explicit_binding"],
                        f"explicit binding to {target.split('::', 1)[0]}",
                    ),
                )

    if allow_phone:
        phones_by_account: Dict[str, set] = {}
        for record in records:
            for entity in _values(record, "phone"):
                if normalize_phone(entity.value):
                    phones_by_account.setdefault(record.rid, set()).add(
                        normalize_phone(entity.value)
                    )
        for node in (relations or RelationResult()).nodes:
            for binding in node.cross_platform:
                if str(binding.get("platform", "")).lower() != "phone":
                    continue
                raw = binding.get("id") or binding.get("url")
                if normalize_phone(raw):
                    phones_by_account.setdefault(node.key(), set()).add(
                        normalize_phone(raw)
                    )
        for left, right in combinations(sorted(phones_by_account), 2):
            for fingerprint in sorted(
                phones_by_account[left] & phones_by_account[right]
            ):
                add(
                    left,
                    right,
                    IdentityEvidence(
                        "phone",
                        "authorized_observation",
                        WEIGHTS["phone"],
                        "same complete phone number",
                        phone_fingerprint(fingerprint, phone_key)
                        if phone_key else "",
                    ),
                )

    links = []
    for (left, right), raw_evidence in sorted(evidence_by_pair.items()):
        score, evidence = _score(raw_evidence)
        links.append(IdentityLink(left, right, score, _decision(score), evidence))

    parent: Dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        if parent[value] != value:
            parent[value] = find(parent[value])
        return parent[value]

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for link in links:
        if link.decision in {IdentityDecision.CONFIRMED, IdentityDecision.LIKELY}:
            union(link.left, link.right)

    grouped: Dict[str, List[str]] = {}
    for account in sorted(parent):
        grouped.setdefault(find(account), []).append(account)
    clusters = []
    for accounts in sorted(grouped.values()):
        if len(accounts) < 2:
            continue
        digest = hashlib.sha256("|".join(accounts).encode("utf-8")).hexdigest()[:12]
        clusters.append(IdentityCluster(f"identity:{digest}", accounts))
    return links, clusters


def identity_report(
    links: List[IdentityLink], clusters: List[IdentityCluster]
) -> dict:
    return {
        "links": [asdict(link) for link in links],
        "clusters": [asdict(cluster) for cluster in clusters],
    }
