"""Deterministic identity-aware analysis of a collected social graph."""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Tuple

import networkx as nx

from .models import IdentityCluster, RelationResult, SocialRelationship
from .social_inference import infer_social_relationships


def _aliases(clusters: Iterable[IdentityCluster]) -> Dict[str, str]:
    return {
        account: cluster.cluster_id
        for cluster in clusters
        for account in cluster.accounts
    }


def _account(site: str, user_id: str, aliases: Dict[str, str]) -> str:
    native = f"{site}::{user_id}"
    return aliases.get(native, native)


def _round(value) -> float:
    return round(float(value or 0.0), 6)


def _build_graph(relations: RelationResult, aliases: Dict[str, str]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for node in relations.nodes:
        graph.add_node(_account(node.site, node.user_id, aliases))
    for edge in relations.edges:
        left = _account(edge.site, edge.src, aliases)
        right = _account(edge.site, edge.dst, aliases)
        if left == right:
            continue
        graph.add_edge(left, right)
        if edge.mutual:
            graph.add_edge(right, left)
    return graph


def _communities(graph: nx.Graph) -> List[dict]:
    if not graph.nodes:
        return []
    if not graph.edges:
        groups = [{node} for node in graph.nodes]
    else:
        groups = list(nx.community.greedy_modularity_communities(graph))
    ordered = sorted((sorted(group) for group in groups), key=lambda group: group[0])
    return [
        {"community_id": f"community:{index}", "accounts": accounts}
        for index, accounts in enumerate(ordered, start=1)
    ]


def _platforms(evidence: Iterable[str]) -> List[str]:
    platforms = set()
    for item in evidence:
        if not item.startswith("observed:"):
            continue
        endpoint = item.split(":", 1)[1]
        if "::" in endpoint:
            platforms.add(endpoint.split("::", 1)[0])
    return sorted(platforms)


def _profiles(relationships: List[SocialRelationship]) -> List[dict]:
    grouped: Dict[Tuple[str, str, bool], dict] = {}
    for item in relationships:
        directed = item.relation not in {"mutual_follow", "shared_neighborhood"}
        left, right = item.left, item.right
        if not directed:
            left, right = sorted((left, right))
        key = left, right, directed
        profile = grouped.setdefault(key, {
            "left": left,
            "right": right,
            "directed": directed,
            "scopes": set(),
            "actions": set(),
            "platforms": set(),
            "evidence": set(),
            "families": {},
        })
        profile["scopes"].add(item.scope)
        profile["actions"].add(item.relation)
        profile["platforms"].update(_platforms(item.evidence))
        profile["evidence"].update(item.evidence)
        profile["families"][item.relation] = max(
            item.score, profile["families"].get(item.relation, 0.0)
        )

    result = []
    for profile in grouped.values():
        score = 1.0 - math.prod(
            1.0 - value for value in profile.pop("families").values()
        )
        scopes = profile.pop("scopes")
        actions = sorted(profile.pop("actions"))
        platforms = sorted(profile.pop("platforms"))
        evidence = sorted(profile.pop("evidence"))
        result.append({
            **profile,
            "scope": "mixed" if len(scopes) > 1 else next(iter(scopes)),
            "score": _round(score),
            "actions": actions,
            "platforms": platforms,
            "evidence": evidence,
        })
    return sorted(
        result,
        key=lambda item: (
            0 if item["scope"] == "observed" else 1,
            -item["score"], item["left"], item["right"], item["directed"],
        ),
    )


def analyze_network(
    relations: RelationResult,
    clusters: List[IdentityCluster],
    relationships: List[SocialRelationship] | None = None,
) -> dict:
    aliases = _aliases(clusters)
    graph = _build_graph(relations, aliases)
    undirected = graph.to_undirected()
    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()

    betweenness = nx.betweenness_centrality(graph) if node_count > 1 else {
        node: 0.0 for node in graph.nodes
    }
    one_way_denominator = max(node_count - 1, 1)
    total_denominator = 2 * one_way_denominator
    actors = [
        {
            "account": node,
            "in_degree": graph.in_degree(node),
            "out_degree": graph.out_degree(node),
            "degree": graph.degree(node),
            "degree_centrality": _round(
                graph.degree(node) / total_denominator if node_count > 1 else 0.0
            ),
            "in_degree_centrality": _round(
                graph.in_degree(node) / one_way_denominator
                if node_count > 1 else 0.0
            ),
            "out_degree_centrality": _round(
                graph.out_degree(node) / one_way_denominator
                if node_count > 1 else 0.0
            ),
            "betweenness": _round(betweenness.get(node, 0.0)),
        }
        for node in sorted(graph.nodes)
    ]
    bridges = [
        {"account": node, "betweenness": _round(score)}
        for node, score in sorted(
            betweenness.items(), key=lambda item: (-item[1], item[0])
        )
        if score > 0
    ]
    articulation = (
        sorted(nx.articulation_points(undirected))
        if undirected.number_of_nodes() > 1 else []
    )
    inferred = relationships
    if inferred is None:
        inferred = infer_social_relationships(relations, clusters)

    return {
        "metrics": {
            "nodes": node_count,
            "edges": edge_count,
            "density": _round(nx.density(graph)),
            "reciprocity": _round(nx.reciprocity(graph)) if edge_count else 0.0,
            "weak_components": nx.number_weakly_connected_components(graph)
            if node_count else 0,
            "strong_components": nx.number_strongly_connected_components(graph)
            if node_count else 0,
        },
        "actors": actors,
        "communities": _communities(undirected),
        "bridges": bridges,
        "articulation_points": articulation,
        "relationship_profiles": _profiles(inferred),
    }
