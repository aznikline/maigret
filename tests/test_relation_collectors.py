import pytest

import relations
import maigret_extensions.models as models
from maigret_extensions.models import (
    Entity,
    Record,
    RelationResult,
    SocialEdge,
)


def test_relation_result_merges_collection_statuses_deterministically():
    assert hasattr(models, "CollectionStatus")
    CollectionStatus = models.CollectionStatus
    result = RelationResult(
        statuses=[
            CollectionStatus(
                "GitHub", "GitHub::alice", "followers", "complete", "public"
            )
        ]
    )
    result.merge(
        RelationResult(
            statuses=[
                CollectionStatus(
                    "GitHub", "GitHub::alice", "followers", "complete", "public"
                ),
                CollectionStatus(
                    "GitHub", "GitHub::alice", "following", "empty", "public"
                ),
            ]
        )
    )

    assert [status.capability for status in result.statuses] == [
        "followers",
        "following",
    ]


def test_relation_result_keeps_strongest_status_for_same_capability():
    CollectionStatus = models.CollectionStatus
    result = RelationResult(statuses=[CollectionStatus(
        "GitHub", "GitHub::alice", "followers", "error", "public"
    )])

    result.merge(RelationResult(statuses=[CollectionStatus(
        "GitHub", "GitHub::alice", "followers", "complete", "public"
    )]))

    assert result.statuses[0].state == "complete"


@pytest.mark.asyncio
async def test_github_collector_preserves_direction_and_mutual_state(monkeypatch):
    async def github_list(_session, _login, endpoint, _limit):
        if endpoint == "followers":
            return [
                {"login": "bob", "avatar_url": "bob.png"},
                {"login": "carol", "avatar_url": "carol.png"},
            ]
        return [
            {"login": "carol", "avatar_url": "carol.png"},
            {"login": "dave", "avatar_url": "dave.png"},
        ]

    monkeypatch.setattr(relations, "_github_list", github_list)

    result = await relations.collect_github_for(object(), "alice", limit=30)
    edges = {(edge.src, edge.dst): edge for edge in result.edges}

    assert set(edges) == {
        ("bob", "alice"),
        ("carol", "alice"),
        ("alice", "carol"),
        ("alice", "dave"),
    }
    assert edges[("carol", "alice")].mutual is True
    assert edges[("alice", "carol")].mutual is True
    assert edges[("bob", "alice")].mutual is False
    assert edges[("alice", "dave")].mutual is False
    assert {(s.capability, s.state) for s in result.statuses} == {
        ("followers", "complete"),
        ("following", "complete"),
    }


@pytest.mark.asyncio
async def test_netease_seed_bindings_are_collected_without_mutual_expansion(
    monkeypatch,
):
    async def no_follows(*_args, **_kwargs):
        return []

    async def bindings(*_args, **_kwargs):
        return [
            {
                "platform": "weibo",
                "id": "99",
                "url": "https://weibo.com/u/99",
            }
        ]

    monkeypatch.setattr(relations, "DETECT_MUTUAL", False)
    monkeypatch.setattr(relations, "_netease_follows", no_follows)
    monkeypatch.setattr(relations, "_netease_bindings", bindings)

    result = await relations.collect_netease_for(object(), "42", "Alice")

    assert result.nodes[0].cross_platform[0]["platform"] == "weibo"
    status = next(s for s in result.statuses if s.capability == "bindings")
    assert status.state == "complete"


@pytest.mark.parametrize("container", ["list", "items"])
def test_bilibili_relation_payload_shapes_are_normalized(container):
    payload = {
        "code": 0,
        "data": {
            container: [
                {"mid": 7, "uname": "seven", "face": "seven.png"},
                {"fid": 8, "name": "eight", "avatar": "eight.png"},
            ]
        },
    }

    assert relations._bilibili_entries(payload) == [
        {"id": "7", "name": "seven", "avatar": "seven.png"},
        {"id": "8", "name": "eight", "avatar": "eight.png"},
    ]


@pytest.mark.asyncio
async def test_dispatches_all_actionable_records_and_reports_unsupported(monkeypatch):
    called = []

    async def fake_collector(_session, uid, nickname="", limit=0):
        called.append((uid, nickname))
        return RelationResult(
            edges=[SocialEdge("Test", str(uid), "target")]
        )

    monkeypatch.setattr(relations, "collect_weibo_for", fake_collector)
    monkeypatch.setattr(relations, "collect_bilibili_for", fake_collector)
    monkeypatch.setattr(relations, "collect_xhs_for", fake_collector)
    monkeypatch.setattr(relations, "collect_github_for", fake_collector)
    records = [
        Record(
            "Weibo::99",
            "Weibo",
            [Entity("url", "https://weibo.com/u/99")],
        ),
        Record("Bilibili::88", "Bilibili", [Entity("username", "88")]),
        Record(
            "Xiaohongshu::red-user",
            "Xiaohongshu",
            [Entity("username", "red-user")],
        ),
        Record(
            "GitHub::alice",
            "GitHub",
            [Entity("url", "https://github.com/alice")],
        ),
        Record("Twitter::alice", "Twitter", [Entity("username", "alice")]),
    ]

    result = await relations.collect_relations(object(), records)

    assert {item[0] for item in called} == {"99", "88", "red-user", "alice"}
    unsupported = [s for s in result.statuses if s.site == "Twitter"]
    assert len(unsupported) == 1
    assert unsupported[0].state == "unsupported"
    assert unsupported[0].capability == "relationships"


@pytest.mark.asyncio
async def test_collector_failure_isolated_and_reported(monkeypatch):
    async def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(relations, "collect_github_for", broken)
    records = [
        Record("GitHub::alice", "GitHub", [Entity("username", "alice")]),
        Record("Twitter::bob", "Twitter", [Entity("username", "bob")]),
    ]

    result = await relations.collect_relations(object(), records)

    states = {(status.site, status.state) for status in result.statuses}
    assert ("GitHub", "error") in states
    assert ("Twitter", "unsupported") in states
