---
title: "feat: Complete the unified social network pipeline"
type: feat
status: completed
date: 2026-06-20
origin: docs/research/social-network-coverage-audit.md
---

# feat: Complete the unified social network pipeline

## Overview

Replace parallel and silent platform handling with one typed social-network
pipeline. Directly collect every currently actionable platform, record explicit
coverage for everything else, collapse only high-confidence identity aliases,
and emit explainable relationship profiles plus deterministic network metrics.

## Requirements Trace

- **R1. Coverage accounting:** Every discovered account must produce relationship
  data or a typed collection status; no platform may be silently skipped.
- **R2. Unified adapters:** GitHub, NetEase, Weibo, Bilibili, and Xiaohongshu must
  dispatch directly from account records through `RelationResult`.
- **R3. Direction correctness:** Followers point toward the account, following
  points away, and mutual state requires both directions or platform evidence.
- **R4. Availability truthfulness:** Empty, partial, auth-required, rate-limited,
  unsupported, unavailable, and error states must remain distinguishable.
- **R5. Identity-aware metrics:** Confirmed/likely account aliases must collapse
  before metrics; weak identity candidates must remain separate.
- **R6. Complete graph analysis:** Reports must include density, reciprocity,
  components, degree/centrality, betweenness bridges, articulation points, and
  deterministic communities.
- **R7. Relationship profiles:** Multiple actions between a pair must combine
  without double-counting an evidence family and retain direction, platforms,
  actions, score, scope, and evidence.
- **R8. Privacy and bounded traversal:** Raw phones/secrets never enter outputs;
  collection remains read-only, one-hop by default, and degree-2 only through
  existing bounded mutual expansion.
- **R9. Integration:** `entity_enrich` must emit a unified
  `social_network_<user>.json` alongside existing compatible reports.
- **R10. Documentation and verification:** Research limits, report schema, CLI
  behavior, focused tests, deterministic regression, and review must pass.

## Technical Decisions

| Decision | Rationale |
| --- | --- |
| Extend `RelationResult` with typed statuses | Prevents failed/blocked requests from masquerading as empty networks. |
| Keep platform adapters in `relations.py` initially | Matches the current dispatcher and avoids a broad module migration in a dirty worktree. |
| Reuse NetworkX | It is already a declared dependency and powers the disconnected analyzer. |
| Analyze identity-collapsed directed graph | Preserves direction while preventing the same person on two platforms from distorting metrics. |
| Deterministic sort/round rules | Makes JSON reviewable and tests stable. |
| Coverage manifest for unsupported platforms | Honest completeness is better than fabricated or silently absent data. |

## Implementation Units

- [x] **Unit 1: Typed collection status and coverage manifest**

**Requirements:** R1, R4, R8

**Files:** `maigret_extensions/models.py`, `relations.py`,
`tests/test_relation_collectors.py`

**Test-first scenarios:** merge/deduplicate statuses; distinguish empty from
error; unsupported discovered record emits status; serialization contains no
credentials or raw phone.

- [x] **Unit 2: Correct and complete platform adapters**

**Requirements:** R2, R3, R4

**Files:** `relations.py`, existing Weibo/XHS collectors,
`tests/test_relation_collectors.py`

**Test-first scenarios:** GitHub followers and following have correct directions
and mutual flags; direct Weibo record dispatches without a NetEase binding;
Bilibili normalizes both observed response shapes; XHS dispatch reports missing
auth/signer; NetEase reports followers as partial/unavailable rather than empty.

- [x] **Unit 3: Identity-aware relationship profiles and graph metrics**

**Requirements:** R5, R6, R7

**Files:** create `maigret_extensions/network_analysis.py`, update models and
exports, create `tests/test_network_analysis.py`

**Test-first scenarios:** aliases collapse; weak aliases do not; directed degrees,
reciprocity and components are correct; bridge/articulation/community output is
deterministic; independent actions combine once per evidence family; inferred
ties remain distinguishable from observed ties.

- [x] **Unit 4: Enrichment integration and compatibility**

**Requirements:** R8, R9

**Files:** `entity_enrich.py`, `relations.py`, existing integration tests

**Test-first scenarios:** unified report contains coverage, metrics and profiles;
old relation/social reports remain; raw phone sentinel is absent from every file
and console; loading prior relation JSON without statuses still works.

- [x] **Unit 5: Documentation, review, and completion audit**

**Requirements:** R10 and all prior requirements

**Files:** `README.md`, `README.zh-CN.md`, research and plan documents

**Verification:** focused tests, deterministic core suite, compile and shell
checks, `git diff --check`, full code review, and requirement-by-requirement audit.

Completed with `47` focused tests and `352` deterministic core tests passing.
Two pre-existing tests were skipped and five live-network tests were explicitly
deselected. Python compilation, wrapper shell syntax, sensitive-value scans, and
`git diff --check` passed. Black was unavailable in the active Python runtime.

## Completion Audit

| Requirement | Evidence | Result |
| --- | --- | --- |
| R1 | `CollectionStatus`, per-record dispatch fallback, unsupported/error tests | Met |
| R2 | Direct adapters and dispatcher tests for all five actionable platforms | Met |
| R3 | GitHub/Bilibili direction and mutual tests; existing Weibo direction tests | Met |
| R4 | Typed state model, endpoint error mapping, status precedence tests | Met |
| R5 | Identity-alias metric test; weak/unclustered account test | Met |
| R6 | Deterministic metric, bridge, articulation, and community tests | Met |
| R7 | Multi-action profile combination and inferred-scope tests | Met |
| R8 | Bounded adapters, existing raw-phone integration sentinel, secret-free statuses | Met |
| R9 | End-to-end `social_network_*` report integration test | Met |
| R10 | Research/spec, bilingual README, focused/core tests, review artifact | Met |


## Output Contract

`social_network_<user>.json`:

```json
{
  "seed": "user",
  "coverage": [{"site": "GitHub", "capability": "followers", "state": "complete"}],
  "metrics": {"nodes": 3, "edges": 2, "reciprocity": 0.5},
  "actors": [{"account": "GitHub::alice", "in_degree": 1, "out_degree": 1}],
  "communities": [{"community_id": "community:1", "accounts": ["GitHub::alice"]}],
  "bridges": [{"account": "GitHub::alice", "betweenness": 1.0}],
  "relationship_profiles": []
}
```

All arrays and object-derived lists are deterministically sorted. Floating-point
metrics are rounded to six decimal places.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Endpoint drift | Typed partial/unavailable states and bounded errors. |
| False mutual ties | Require both directions or explicit platform mutual metadata. |
| Large-graph cost | Bounded one-hop collection and exact metrics on the bounded graph. |
| Misleading completeness | Coverage status ships in the same report as metrics. |
| Identity over-collapse | Only confirmed/likely clusters provide aliases. |
| Sensitive leakage | Existing phone serializers plus sentinel scans and secret-free status details. |

## Post-Deploy Monitoring & Validation

- Search logs for `collection status`, `rate_limited`, `auth_required`, `captcha`,
  `network analysis`, and `platform collision`.
- Healthy: every discovered platform has statuses; GitHub follower directions are
  mixed as expected; metrics and profiles are deterministic across repeated runs.
- Failure/rollback: raw phone or secret in output, unsupported source omitted,
  all GitHub following edges marked mutual, or metrics computed without coverage.
- Validation window: deterministic CI plus one operator-authorized local run;
  owner is the local operator.
