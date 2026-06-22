"""Shared contracts and security helpers for local Maigret extensions."""

from .models import (
    CollectionStatus,
    Entity,
    Evidence,
    EvidenceLevel,
    IdentityCluster,
    IdentityDecision,
    IdentityEvidence,
    IdentityLink,
    Record,
    RelationResult,
    SocialEdge,
    SocialNode,
    SocialRelationship,
    merge_evidence,
    normalize,
)

__all__ = [
    "CollectionStatus",
    "Entity",
    "Evidence",
    "EvidenceLevel",
    "IdentityCluster",
    "IdentityDecision",
    "IdentityEvidence",
    "IdentityLink",
    "Record",
    "RelationResult",
    "SocialEdge",
    "SocialNode",
    "SocialRelationship",
    "merge_evidence",
    "normalize",
]
