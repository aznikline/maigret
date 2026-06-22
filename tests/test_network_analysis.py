import importlib
import importlib.util

import pytest

from maigret_extensions.models import (
    IdentityCluster,
    RelationResult,
    SocialEdge,
    SocialNode,
)


def _module():
    assert importlib.util.find_spec("maigret_extensions.network_analysis") is not None
    return importlib.import_module("maigret_extensions.network_analysis")


def _actor(report, account):
    return next(item for item in report["actors"] if item["account"] == account)


def test_identity_aliases_collapse_before_directed_metrics():
    relation = RelationResult(
        nodes=[
            SocialNode("GitHub", "alice"),
            SocialNode("GitHub", "bob"),
            SocialNode("Weibo", "99"),
            SocialNode("Weibo", "carol"),
        ],
        edges=[
            SocialEdge("GitHub", "alice", "bob"),
            SocialEdge("GitHub", "bob", "alice"),
            SocialEdge("Weibo", "99", "carol"),
        ],
    )
    clusters = [
        IdentityCluster("identity:subject", ["GitHub::alice", "Weibo::99"])
    ]

    report = _module().analyze_network(relation, clusters)

    assert report["metrics"]["nodes"] == 3
    assert report["metrics"]["edges"] == 3
    assert report["metrics"]["reciprocity"] == pytest.approx(2 / 3, abs=1e-6)
    subject = _actor(report, "identity:subject")
    assert subject["in_degree"] == 1
    assert subject["out_degree"] == 2
    assert subject["degree_centrality"] == 0.75
    assert subject["in_degree_centrality"] == 0.5
    assert subject["out_degree_centrality"] == 1.0


def test_unclustered_accounts_remain_separate():
    relation = RelationResult(
        edges=[
            SocialEdge("GitHub", "same", "target"),
            SocialEdge("Weibo", "same", "target"),
        ]
    )

    report = _module().analyze_network(relation, [])

    accounts = {actor["account"] for actor in report["actors"]}
    assert "GitHub::same" in accounts
    assert "Weibo::same" in accounts
    assert report["metrics"]["nodes"] == 4


def test_bridge_articulation_and_communities_are_deterministic():
    relation = RelationResult(
        edges=[
            SocialEdge("GitHub", "a", "b"),
            SocialEdge("GitHub", "b", "c"),
            SocialEdge("GitHub", "c", "d"),
        ]
    )

    first = _module().analyze_network(relation, [])
    second = _module().analyze_network(relation, [])

    assert first == second
    assert first["articulation_points"] == ["GitHub::b", "GitHub::c"]
    assert [item["account"] for item in first["bridges"]] == [
        "GitHub::b",
        "GitHub::c",
    ]
    community_accounts = {
        account
        for community in first["communities"]
        for account in community["accounts"]
    }
    assert community_accounts == {
        "GitHub::a",
        "GitHub::b",
        "GitHub::c",
        "GitHub::d",
    }


def test_relationship_profile_combines_independent_actions_once():
    relation = RelationResult(
        edges=[
            SocialEdge("Weibo", "1", "2"),
            SocialEdge("Weibo", "1", "2", via="mentions", weight=2),
            SocialEdge("Weibo", "1", "2", via="mentions", weight=2),
        ]
    )

    report = _module().analyze_network(relation, [])
    profile = report["relationship_profiles"][0]

    assert profile["left"] == "Weibo::1"
    assert profile["right"] == "Weibo::2"
    assert profile["directed"] is True
    assert profile["scope"] == "observed"
    assert profile["actions"] == ["follows", "mentions"]
    assert profile["platforms"] == ["Weibo"]
    assert profile["score"] == pytest.approx(0.7855, abs=1e-6)


def test_inferred_relationship_profile_remains_inferred_and_undirected():
    relation = RelationResult(
        edges=[
            SocialEdge("GitHub", "a", "x"),
            SocialEdge("GitHub", "a", "y"),
            SocialEdge("GitHub", "b", "x"),
            SocialEdge("GitHub", "b", "y"),
        ]
    )

    report = _module().analyze_network(relation, [])
    inferred = next(
        item for item in report["relationship_profiles"]
        if item["scope"] == "inferred"
    )

    assert inferred["directed"] is False
    assert inferred["actions"] == ["shared_neighborhood"]
    assert inferred["score"] <= 0.65
