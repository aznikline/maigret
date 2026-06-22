from maigret_extensions.models import (
    IdentityCluster,
    RelationResult,
    SocialEdge,
)
from maigret_extensions.social_inference import (
    infer_social_relationships,
    social_report,
)


def _by_relation(result):
    return {item.relation: item for item in result}


def test_observed_relationships_have_documented_scores():
    relation = RelationResult(
        edges=[
            SocialEdge("Weibo", "1", "2", mutual=True),
            SocialEdge("Weibo", "1", "3"),
            SocialEdge("Weibo", "1", "4", via="mentions", weight=4),
            SocialEdge("Weibo", "1", "5", via="reposts", weight=2),
        ]
    )

    observed = _by_relation(infer_social_relationships(relation, []))

    assert observed["mutual_follow"].score == 0.95
    assert observed["follows"].score == 0.45
    assert observed["mentions"].score == 0.77
    assert observed["reposts"].score == 0.75
    assert all(item.scope == "observed" for item in observed.values())


def test_relation_merge_preserves_distinct_observed_actions():
    result = RelationResult(
        edges=[SocialEdge("Weibo", "1", "2", via="", weight=1)]
    )
    result.merge(
        RelationResult(
            edges=[SocialEdge("Weibo", "1", "2", via="mentions", weight=3)]
        )
    )

    relationships = infer_social_relationships(result, [])

    assert [item.relation for item in relationships] == ["mentions", "follows"]


def test_shared_neighborhood_requires_overlap_and_jaccard_threshold():
    relation = RelationResult(
        edges=[
            SocialEdge("GitHub", "a", "x"),
            SocialEdge("GitHub", "a", "y"),
            SocialEdge("GitHub", "a", "z"),
            SocialEdge("GitHub", "b", "x"),
            SocialEdge("GitHub", "b", "y"),
            SocialEdge("GitHub", "c", "x"),
        ]
    )

    inferred = [
        item
        for item in infer_social_relationships(relation, [])
        if item.scope == "inferred"
    ]

    assert len(inferred) == 1
    assert inferred[0].relation == "shared_neighborhood"
    assert {inferred[0].left, inferred[0].right} == {"GitHub::a", "GitHub::b"}
    assert inferred[0].shared_neighbors == ["GitHub::x", "GitHub::y"]
    assert inferred[0].score <= 0.65


def test_confirmed_aliases_collapse_but_unclustered_aliases_remain_separate():
    relation = RelationResult(
        edges=[
            SocialEdge("NeteaseCloudMusic", "42", "target"),
            SocialEdge("Weibo", "99", "target"),
            SocialEdge("GitHub", "same", "target"),
        ]
    )
    clusters = [
        IdentityCluster(
            "identity:subject", ["NeteaseCloudMusic::42", "Weibo::99"]
        )
    ]

    relationships = infer_social_relationships(relation, clusters)
    follows = [item for item in relationships if item.relation == "follows"]

    assert sum(item.left == "identity:subject" for item in follows) == 2
    assert any(item.left == "GitHub::same" for item in follows)


def test_collapsed_self_relationships_and_duplicates_are_removed():
    relation = RelationResult(
        edges=[
            SocialEdge("Weibo", "1", "2", mutual=True),
            SocialEdge("Weibo", "1", "2", mutual=True),
        ]
    )
    clusters = [IdentityCluster("identity:same", ["Weibo::1", "Weibo::2"])]

    assert infer_social_relationships(relation, clusters) == []


def test_social_report_is_deterministic_and_never_calls_inference_friendship():
    relation = RelationResult(edges=[SocialEdge("Weibo", "1", "2")])
    relationships = infer_social_relationships(relation, [])

    report = social_report(relationships)

    assert report["relationships"][0]["scope"] == "observed"
    assert "friend" not in str(report).lower()
