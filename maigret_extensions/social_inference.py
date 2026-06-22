"""Explainable social-relationship derivation from collected platform edges."""

from __future__ import annotations

from dataclasses import asdict
from itertools import combinations
from typing import Dict, Iterable, List, Tuple

from .models import IdentityCluster, RelationResult, SocialEdge, SocialRelationship


def _account(site: str, user_id: str) -> str:
    return f"{site}::{user_id}"


def _aliases(clusters: Iterable[IdentityCluster]) -> Dict[str, str]:
    result = {}
    for cluster in clusters:
        for account in cluster.accounts:
            result[account] = cluster.cluster_id
    return result


def _edge_kind(edge: SocialEdge) -> Tuple[str, float]:
    if edge.mutual:
        return "mutual_follow", 0.95
    if edge.via == "mentions":
        return "mentions", round(min(0.45 + 0.08 * max(edge.weight, 1), 0.85), 2)
    if edge.via == "reposts":
        return "reposts", 0.75
    return "follows", 0.45


def infer_social_relationships(
    relations: RelationResult,
    clusters: List[IdentityCluster],
    *,
    min_shared: int = 2,
    min_jaccard: float = 0.20,
) -> List[SocialRelationship]:
    aliases = _aliases(clusters)
    observed: Dict[Tuple[str, str, str], SocialRelationship] = {}
    outgoing: Dict[str, set] = {}

    for edge in relations.edges:
        native_left = _account(edge.site, edge.src)
        native_right = _account(edge.site, edge.dst)
        left = aliases.get(native_left, native_left)
        right = aliases.get(native_right, native_right)
        if left == right:
            continue
        relation, score = _edge_kind(edge)
        if relation == "mutual_follow":
            left, right = sorted((left, right))
        key = (left, right, relation)
        evidence = f"observed:{edge.endpoints()}"
        current = observed.get(key)
        if current is None:
            observed[key] = SocialRelationship(
                left, right, relation, "observed", score, [evidence]
            )
        else:
            current.score = max(current.score, score)
            if evidence not in current.evidence:
                current.evidence.append(evidence)

        if relation in {"follows", "mutual_follow"}:
            outgoing.setdefault(left, set()).add(right)
            if relation == "mutual_follow":
                outgoing.setdefault(right, set()).add(left)

    directly_connected = {
        frozenset((item.left, item.right)) for item in observed.values()
    }
    inferred = []
    for left, right in combinations(sorted(outgoing), 2):
        if frozenset((left, right)) in directly_connected:
            continue
        shared = sorted(outgoing[left] & outgoing[right])
        if len(shared) < min_shared:
            continue
        union = outgoing[left] | outgoing[right]
        jaccard = len(shared) / len(union) if union else 0.0
        if jaccard < min_jaccard:
            continue
        score = round(min(0.35 + 0.05 * len(shared) + 0.30 * jaccard, 0.65), 2)
        inferred.append(
            SocialRelationship(
                left,
                right,
                "shared_neighborhood",
                "inferred",
                score,
                [f"shared_outgoing:{len(shared)}", f"jaccard:{jaccard:.3f}"],
                shared,
            )
        )

    return sorted(
        [*observed.values(), *inferred],
        key=lambda item: (
            0 if item.scope == "observed" else 1,
            -item.score,
            item.left,
            item.right,
            item.relation,
        ),
    )


def social_report(relationships: List[SocialRelationship]) -> dict:
    return {"relationships": [asdict(item) for item in relationships]}
