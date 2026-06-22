import json

import networkx as nx
import pytest

import entity_enrich
import relations
from maigret_extensions.correlation import correlate_identities, identity_report
from maigret_extensions.models import (
    CollectionStatus,
    Entity,
    Record,
    RelationResult,
    SocialEdge,
    SocialNode,
)
from maigret_extensions.privacy import (
    PhonePrivacyError,
    normalize_phone,
    phone_fingerprint,
    safe_entity_dict,
)


def test_platform_qualified_node_and_edge_keys_do_not_collide():
    weibo = SocialNode(site="Weibo", user_id="123", nickname="Wei")
    netease = SocialNode(site="NeteaseCloudMusic", user_id="123", nickname="Music")
    weibo_edge = SocialEdge(site="Weibo", src="123", dst="456")
    netease_edge = SocialEdge(site="NeteaseCloudMusic", src="123", dst="456")

    result = RelationResult()
    result.merge(RelationResult(nodes=[weibo], edges=[weibo_edge]))
    result.merge(RelationResult(nodes=[netease], edges=[netease_edge]))

    assert [node.key() for node in result.nodes] == [
        "Weibo::123",
        "NeteaseCloudMusic::123",
    ]
    assert [edge.endpoints() for edge in result.edges] == [
        "Weibo::123>456",
        "NeteaseCloudMusic::123>456",
    ]


def test_social_graph_preserves_platform_qualified_degree():
    relation = RelationResult(
        nodes=[
            SocialNode(site="Weibo", user_id="123", nickname="Source", degree=2),
            SocialNode(site="Weibo", user_id="456", nickname="Target", degree=1),
        ],
        edges=[SocialEdge(site="Weibo", src="123", dst="456")],
    )
    graph = nx.DiGraph()

    entity_enrich.add_social_edges(graph, relation)

    assert graph.nodes["social:Weibo:123"]["degree"] == 2
    assert graph.nodes["social:Weibo:456"]["degree"] == 1


def test_phone_normalization_and_fingerprint_are_private():
    key = "operator-controlled-test-key"
    assert normalize_phone("138 0013 8000") == "+8613800138000"
    assert normalize_phone("+86-138-0013-8000") == "+8613800138000"
    assert phone_fingerprint("13800138000", key) == phone_fingerprint(
        "+8613800138000", key
    )
    assert "13800138000" not in phone_fingerprint("13800138000", key)


@pytest.mark.parametrize("value", ["138****8000", "1380", "", "+1234567890123456"])
def test_masked_or_invalid_phones_are_rejected(value):
    assert normalize_phone(value) is None


def test_phone_fingerprint_requires_operator_key():
    with pytest.raises(PhonePrivacyError, match="MAIGRET_PHONE_HASH_KEY"):
        phone_fingerprint("13800138000", "")


def test_entity_and_binding_serializers_never_emit_raw_phone():
    raw = "13800138000"
    key = "operator-controlled-test-key"
    entity = Entity(kind="phone", value=raw, sources=["PublicProfile"])
    relation = RelationResult(
        nodes=[
            SocialNode(
                site="NeteaseCloudMusic",
                user_id="42",
                cross_platform=[{"platform": "phone", "id": raw, "url": raw}],
            )
        ]
    )

    entity_data = safe_entity_dict(entity, allow_phone=True, phone_key=key)
    relation_data = relations.to_json(
        relation, allow_phone=True, phone_key=key
    )
    encoded = json.dumps({"entity": entity_data, "relation": relation_data})

    assert raw not in encoded
    assert entity_data["value"] == "[redacted-phone]"
    assert entity_data["phone_fingerprint"].startswith("hmac-sha256:v1:")
    binding = relation_data["nodes"][0]["cross_platform"][0]
    assert binding["id"] is None
    assert binding["url"] == ""
    assert binding["phone_redacted"] is True
    assert binding["phone_fingerprint"].startswith("hmac-sha256:v1:")


def test_phone_graph_nodes_are_redacted_and_matching_is_enabled_by_default():
    raw = "13800138000"
    records = [
        Record("A::1", "A", [Entity("phone", raw, ["A"])]),
        Record("B::2", "B", [Entity("phone", raw, ["B"])]),
    ]

    default_graph = entity_enrich.build_graph({}, "seed", records=records)
    private_graph = entity_enrich.build_graph(
        {},
        "seed",
        records=records,
        allow_phone=True,
        phone_key="operator-controlled-test-key",
    )
    disabled_graph = entity_enrich.build_graph(
        {}, "seed", records=records, allow_phone=False
    )

    assert raw not in json.dumps(nx.node_link_data(default_graph))
    assert any(
        data.get("via") == "shared phone"
        for _, _, data in default_graph.edges(data=True)
    )
    assert any(
        data.get("via") == "shared phone"
        for _, _, data in private_graph.edges(data=True)
    )
    assert not any(
        data.get("via") == "shared phone"
        for _, _, data in disabled_graph.edges(data=True)
    )
    assert raw not in json.dumps(nx.node_link_data(private_graph))


def test_explicit_netease_weibo_binding_creates_confirmed_identity_cluster():
    records = [Record("NeteaseCloudMusic::42", "NeteaseCloudMusic")]
    relation = RelationResult(
        nodes=[
            SocialNode(
                "NeteaseCloudMusic",
                "42",
                cross_platform=[
                    {
                        "platform": "weibo",
                        "type": 2,
                        "id": "123456",
                        "url": "https://weibo.com/u/123456",
                    }
                ],
            )
        ]
    )

    links, clusters = correlate_identities(records, relation)

    assert len(links) == 1
    assert links[0].decision.value == "confirmed"
    assert links[0].score == 0.99
    assert {links[0].left, links[0].right} == {
        "NeteaseCloudMusic::42",
        "Weibo::123456",
    }
    assert clusters[0].accounts == ["NeteaseCloudMusic::42", "Weibo::123456"]


@pytest.mark.parametrize(
    "entities,expected_decision,expected_score",
    [
        (
            [Entity("email", "Alice@Example.com")],
            "confirmed",
            0.9,
        ),
        (
            [Entity("avatar", "https://a.example/1", exif={"phash": "abc"})],
            "likely",
            0.8,
        ),
        (
            [Entity("username", "same-handle")],
            "weak",
            0.25,
        ),
    ],
)
def test_record_evidence_has_documented_strength(
    entities, expected_decision, expected_score
):
    records = [
        Record("A::1", "A", entities),
        Record(
            "B::2",
            "B",
            [
                Entity(e.kind, e.value.lower(), exif=dict(e.exif))
                for e in entities
            ],
        ),
    ]

    links, clusters = correlate_identities(records, RelationResult())

    assert links[0].decision.value == expected_decision
    assert links[0].score == expected_score
    assert bool(clusters) is (expected_decision != "weak")


def test_phone_identity_match_is_strong_by_default_and_never_serializes_raw_value():
    raw = "13800138000"
    records = [
        Record("A::1", "A", [Entity("phone", raw)]),
        Record("B::2", "B", [Entity("phone", "+8613800138000")]),
    ]

    default_links, default_clusters = correlate_identities(records, RelationResult())
    disabled_links, _ = correlate_identities(
        records, RelationResult(), allow_phone=False
    )
    private_links, private_clusters = correlate_identities(
        records,
        RelationResult(),
        allow_phone=True,
        phone_key="operator-controlled-test-key",
    )
    encoded = json.dumps(identity_report(private_links, private_clusters))
    default_encoded = json.dumps(identity_report(default_links, default_clusters))

    assert default_links[0].decision.value == "confirmed"
    assert default_links[0].score == 0.95
    assert default_clusters
    assert disabled_links == []
    assert raw not in default_encoded
    assert private_links[0].decision.value == "confirmed"
    assert private_links[0].score == 0.95
    assert raw not in encoded
    assert "hmac-sha256:v1:" in encoded


def test_same_evidence_family_is_counted_once():
    records = [
        Record("A::1", "A", [Entity("username", "same")]),
        Record("B::2", "B", [Entity("username", "same")]),
    ]
    relation = RelationResult(
        nodes=[
            SocialNode(
                "A",
                "1",
                cross_platform=[
                    {"platform": "weibo", "url": "https://weibo.com/u/9"},
                    {"platform": "weibo", "url": "https://weibo.com/u/9"},
                ],
            )
        ]
    )

    links, _ = correlate_identities(records, relation)
    explicit = next(link for link in links if link.right == "Weibo::9")

    assert explicit.score == 0.99
    assert len(explicit.evidence) == 1


def test_netease_phone_binding_can_match_authorized_record_without_raw_output():
    raw = "13800138000"
    records = [Record("Other::7", "Other", [Entity("phone", raw)])]
    relation = RelationResult(
        nodes=[
            SocialNode(
                "NeteaseCloudMusic",
                "42",
                cross_platform=[{"platform": "phone", "id": raw}],
            )
        ]
    )

    links, clusters = correlate_identities(
        records,
        relation,
        allow_phone=True,
        phone_key="operator-controlled-test-key",
    )
    encoded = json.dumps(identity_report(links, clusters))

    assert links[0].score == 0.95
    assert {links[0].left, links[0].right} == {
        "NeteaseCloudMusic::42",
        "Other::7",
    }
    assert raw not in encoded


@pytest.mark.asyncio
async def test_enrichment_writes_private_identity_and_social_reports(
    tmp_path, monkeypatch, capsys
):
    raw_phone = "13800138000"
    records = [
        Record(
            "NeteaseCloudMusic::42",
            "NeteaseCloudMusic",
            [Entity("phone", raw_phone), Entity("username", "alice")],
        )
    ]
    relation = RelationResult(
        nodes=[
            SocialNode(
                "NeteaseCloudMusic",
                "42",
                nickname="Alice",
                cross_platform=[
                    {
                        "platform": "weibo",
                        "id": "99",
                        "url": "https://weibo.com/u/99",
                    },
                    {"platform": "phone", "id": raw_phone, "url": raw_phone},
                ],
            ),
            SocialNode("NeteaseCloudMusic", "7", nickname="Bob"),
        ],
        edges=[
            SocialEdge(
                "NeteaseCloudMusic",
                "42",
                "7",
                src_name="Alice",
                dst_name="Bob",
                mutual=True,
            )
        ],
        statuses=[
            CollectionStatus(
                "NeteaseCloudMusic",
                "NeteaseCloudMusic::42",
                "following",
                "complete",
                "public",
            )
        ],
    )

    async def no_avatar_enrichment(*_args, **_kwargs):
        return None

    async def collect_relations(*_args, **_kwargs):
        return relation

    monkeypatch.setattr(entity_enrich, "_find", lambda *_args: None)
    monkeypatch.setattr(entity_enrich, "group_by_record", lambda *_args: records)
    monkeypatch.setattr(entity_enrich, "enrich_avatars", no_avatar_enrichment)
    monkeypatch.setattr(relations, "collect_relations", collect_relations)
    monkeypatch.setattr(entity_enrich, "render_graph", lambda *_args: None)

    result = await entity_enrich.main_async(
        None,
        "private-integration",
        str(tmp_path),
        allow_phone=True,
        phone_key="operator-controlled-test-key",
    )

    outputs = list(tmp_path.glob("*.json"))
    encoded = "\n".join(path.read_text(encoding="utf-8") for path in outputs)
    console = capsys.readouterr().out
    identity = json.loads((tmp_path / "identity_links_private-integration.json").read_text())
    social = json.loads((tmp_path / "social_inferences_private-integration.json").read_text())
    network = json.loads((tmp_path / "social_network_private-integration.json").read_text())

    assert raw_phone not in encoded
    assert raw_phone not in console
    assert identity["links"][0]["decision"] == "confirmed"
    assert social["relationships"][0]["scope"] == "observed"
    assert network["coverage"][0]["state"] == "complete"
    assert network["metrics"]["nodes"] == 2
    assert network["relationship_profiles"][0]["scope"] == "observed"
    assert result["identity_links"] == 1
    assert result["social_inferences"] == 1


@pytest.mark.asyncio
async def test_enrichment_allows_default_phone_matching_without_key(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(entity_enrich, "_find", lambda *_args: None)
    monkeypatch.setattr(entity_enrich, "group_by_record", lambda *_args: [])

    async def no_avatar_enrichment(*_args, **_kwargs):
        return None

    async def no_relations(*_args, **_kwargs):
        return RelationResult()

    monkeypatch.setattr(entity_enrich, "enrich_avatars", no_avatar_enrichment)
    monkeypatch.setattr(relations, "collect_relations", no_relations)
    monkeypatch.setattr(entity_enrich, "render_graph", lambda *_args: None)

    result = await entity_enrich.main_async(
        None, "no-key-required", str(tmp_path)
    )

    assert result["identity_links"] == 0
