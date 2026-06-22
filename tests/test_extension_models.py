from dataclasses import asdict

import entity_enrich
import relations
from maigret_extensions.models import (
    Entity,
    Evidence,
    EvidenceLevel,
    Record,
    RelationResult,
    SocialEdge,
    SocialNode,
    merge_evidence,
)


def test_entity_and_relation_serialization_shapes_are_stable():
    entity = Entity(kind="email", value="Alice@Example.com", sources=["GitHub"])
    node = SocialNode(site="GitHub", user_id="alice", nickname="Alice")
    edge = SocialEdge(site="GitHub", src="alice", dst="bob", mutual=True, weight=3)

    assert asdict(entity) == {
        "kind": "email",
        "value": "Alice@Example.com",
        "sources": ["GitHub"],
        "pivots": [],
        "exif": {},
        "notes": [],
    }
    assert relations.to_json(RelationResult([node], [edge])) == {
        "nodes": [asdict(node)],
        "edges": [asdict(edge)],
        "errors": [],
        "statuses": [],
    }


def test_record_and_evidence_deduplicate_in_first_seen_order():
    entity = Entity(kind="email", value="Alice@Example.com")
    duplicate = Entity(kind="email", value="alice@example.com")
    record = Record("GitHub::alice", "GitHub", [entity])
    record.add_member(duplicate)

    high = Evidence("GitHub", "shared profile email", EvidenceLevel.HIGH)
    low = Evidence("Search", "same username")

    assert record.members == [entity]
    assert merge_evidence([high, low, high]) == [high, low]


def test_root_extension_modules_share_the_contract_classes():
    assert entity_enrich.Entity is Entity
    assert entity_enrich.Record is Record
    assert relations.SocialNode is SocialNode
    assert relations.SocialEdge is SocialEdge
    assert relations.RelationResult is RelationResult


def test_entity_graph_uses_typed_evidence_without_changing_reason_text():
    left = Entity(kind="email", value="Alice@example.com")
    right = Entity(kind="email", value="alice@example.com")

    evidence = entity_enrich._evidence(left, right)

    assert evidence == Evidence(
        "entity_enrich", "shared email", EvidenceLevel.HIGH
    )
