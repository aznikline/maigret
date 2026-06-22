# Unified Social Network Review

Mode: autofix-compatible inline review

Scope: unified relationship models, platform adapters, network analysis,
enrichment integration, tests, research, spec, and bilingual documentation.

Intent: complete the repository's actionable social-network coverage without
silently treating blocked sources as empty, and produce identity-aware,
explainable relationship and network analysis.

## Review Lenses

- Correctness: edge direction, mutual detection, status precedence, identity
  collapse, metric normalization, deterministic ordering.
- Security/privacy: raw phone and credential exclusion, bounded read-only
  traversal, safe error/status details.
- Reliability: rate limits, authentication, endpoint drift, per-platform failure
  isolation, partial-versus-empty semantics.
- API contract: additive `statuses` field and new `social_network_*` output while
  preserving existing relation/inference outputs.
- Testing/maintainability: synthetic platform contracts, integration sentinel,
  no new dependency, reuse of existing models and NetworkX.

## Applied Findings

1. Fixed GitHub follower direction and false mutual weighting.
2. Made NetEase seed bindings independent of mutual-expansion mode.
3. Added URL-derived account IDs for real deep-search records.
4. Replaced first-wins collection status merging with quality precedence.
5. Isolated unexpected platform failures so later accounts still receive status.
6. Normalized directed total degree centrality to the `[0, 1]` range and added
   separate in/out centralities.
7. Marked bounded/full-page collections partial rather than complete.
8. Corrected stale phone-default and Xiaohongshu commenter documentation.

## Residual Risks

- Bilibili, NetEase, Weibo Web, and Xiaohongshu endpoints are undocumented or
  authorization-bound and may drift. Typed states expose this at runtime.
- Exact centrality is appropriate for current bounded graphs; substantially
  larger future traversal limits would require approximate metrics.
- Black was not installed in the active runtime; compilation, diff checks, and
  tests provide the available formatting/syntax evidence.

## Verdict

Ready. No unresolved actionable findings after the autofix pass.

