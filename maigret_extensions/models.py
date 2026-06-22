"""Stable data contracts shared by enrichment and relation collectors."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Tuple


class EvidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IdentityDecision(str, Enum):
    CONFIRMED = "confirmed"
    LIKELY = "likely"
    POSSIBLE = "possible"
    WEAK = "weak"


@dataclass(frozen=True)
class IdentityEvidence:
    kind: str
    source: str
    confidence: float
    detail: str
    fingerprint: str = ""

    def identity(self) -> Tuple[str, str, str]:
        return self.kind, self.source, self.fingerprint or self.detail


@dataclass
class IdentityLink:
    left: str
    right: str
    score: float
    decision: IdentityDecision
    evidence: List[IdentityEvidence] = field(default_factory=list)


@dataclass
class IdentityCluster:
    cluster_id: str
    accounts: List[str] = field(default_factory=list)


@dataclass
class SocialRelationship:
    left: str
    right: str
    relation: str
    scope: str
    score: float
    evidence: List[str] = field(default_factory=list)
    shared_neighbors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CollectionStatus:
    """Outcome of one platform capability collection attempt."""

    site: str
    account_id: str
    capability: str
    state: str
    access: str
    detail: str = ""

    def identity(self) -> Tuple[str, str, str]:
        return self.site, self.account_id, self.capability

    def quality(self) -> int:
        return {
            "error": 1,
            "unsupported": 2,
            "unavailable": 3,
            "rate_limited": 4,
            "auth_required": 5,
            "empty": 6,
            "partial": 7,
            "complete": 8,
        }.get(self.state, 0)


@dataclass(frozen=True)
class Evidence:
    """A reason for associating two observations."""

    source: str
    reason: str
    level: EvidenceLevel = EvidenceLevel.LOW

    def identity(self) -> Tuple[str, str, str]:
        return self.source, self.reason, self.level.value


def merge_evidence(items: Iterable[Evidence]) -> List[Evidence]:
    """Deduplicate evidence without changing first-seen order."""

    result = []
    seen = set()
    for item in items:
        if item.identity() in seen:
            continue
        seen.add(item.identity())
        result.append(item)
    return result


def normalize(kind: str, value: str) -> str:
    value = str(value).strip()
    if kind == "phone":
        return re.sub(r"[^\d+]", "", value)
    if kind in {"email", "url", "avatar", "name"}:
        value = value.lower()
    return value.rstrip("/") if kind == "url" else value


@dataclass
class Entity:
    """A single identity pivot discovered across the footprint."""

    kind: str
    value: str
    sources: List[str] = field(default_factory=list)
    pivots: List[str] = field(default_factory=list)
    exif: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def key(self) -> Tuple[str, str]:
        return self.kind, normalize(self.kind, self.value)


@dataclass
class Record:
    """One account: fields known to belong to the same profile."""

    rid: str
    site: str
    members: List[Entity] = field(default_factory=list)
    _seen: set = field(default_factory=set, repr=False)

    def __post_init__(self) -> None:
        self._seen.update(member.key() for member in self.members)

    def add_member(self, entity: Entity) -> None:
        if entity.key() in self._seen:
            return
        self._seen.add(entity.key())
        self.members.append(entity)


@dataclass
class SocialNode:
    """A person seen in a social relation."""

    site: str
    user_id: str
    nickname: str = ""
    avatar: str = ""
    degree: int = 1
    cross_platform: List[dict] = field(default_factory=list)

    def key(self) -> str:
        return f"{self.site}::{self.user_id}"


@dataclass
class SocialEdge:
    """A directed social relation."""

    site: str
    src: str
    dst: str
    src_name: str = ""
    dst_name: str = ""
    mutual: bool = False
    weight: int = 1
    via: str = ""

    def endpoints(self) -> str:
        return f"{self.site}::{self.src}>{self.dst}"

    def identity(self) -> Tuple[str, str]:
        """Deduplicate repeats without merging distinct observed actions."""
        action = self.via if self.via in {"mentions", "reposts"} else "follows"
        return self.endpoints(), action


@dataclass
class RelationResult:
    nodes: List[SocialNode] = field(default_factory=list)
    edges: List[SocialEdge] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    statuses: List[CollectionStatus] = field(default_factory=list)

    def merge(self, other: "RelationResult") -> None:
        """Merge another result without cross-platform identifier collisions."""
        nodes = {node.key(): node for node in self.nodes}
        edges = {edge.identity(): edge for edge in self.edges}
        for node in other.nodes:
            current = nodes.get(node.key())
            if current is None:
                nodes[node.key()] = node
                self.nodes.append(node)
                continue
            current.degree = min(current.degree, node.degree)
            current.nickname = current.nickname or node.nickname
            current.avatar = current.avatar or node.avatar
            for binding in node.cross_platform:
                if binding not in current.cross_platform:
                    current.cross_platform.append(binding)
        for edge in other.edges:
            current = edges.get(edge.identity())
            if current is None:
                edges[edge.identity()] = edge
                self.edges.append(edge)
                continue
            current.mutual = current.mutual or edge.mutual
            current.weight = max(current.weight, edge.weight)
            current.via = current.via or edge.via
        for error in other.errors:
            if error not in self.errors:
                self.errors.append(error)
        status_indexes = {
            status.identity(): index for index, status in enumerate(self.statuses)
        }
        for status in other.statuses:
            index = status_indexes.get(status.identity())
            if index is None:
                status_indexes[status.identity()] = len(self.statuses)
                self.statuses.append(status)
            elif status.quality() > self.statuses[index].quality():
                self.statuses[index] = status
