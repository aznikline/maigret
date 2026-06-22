# Social Network Coverage Audit

Date: 2026-06-20

## Objective

Audit every social source already discoverable by the local Maigret workflow,
separate identity discovery from relationship collection, and define what a
complete unified social-network report can honestly claim. "Complete" means no
discovered account is silently ignored: a source either contributes typed nodes
and edges or reports a machine-readable availability state.

## Current-System Findings

1. `relations.py` is the unified pipeline, but directly dispatches only NetEase
   Cloud Music and GitHub. Weibo is reached only through a NetEase binding.
2. `social_analyzer.py` separately implements GitHub, Bilibili, Weibo, and
   NetEase extraction plus basic centrality. Its data never enters identity
   clustering, social inference, or `entity_enrich` reports.
3. `xhs_interactions.py` produces the shared `RelationResult` contract but is
   never called by the unified dispatcher.
4. GitHub's unified collector requests only `following`; it therefore omits
   follower-to-owner edges and incorrectly treats every following edge as
   reciprocated for weighting purposes.
5. Collector failures are free-form strings. A downstream operator cannot tell
   whether a source is complete, partial, rate-limited, authentication-bound,
   unsupported, or failed.
6. Existing inference reports direct actions and shared neighborhoods, while the
   richer graph metrics in `social_analyzer.py` remain disconnected.

## Platform Coverage Matrix

| Platform | Discovery signal | Relationship capability | Access boundary | Unified target state |
| --- | --- | --- | --- | --- |
| GitHub | Login, stable ID, public profile fields | Public followers, following, reciprocity | Public REST; anonymous rate limit, optional token | Full one-hop directed graph with correct mutual detection |
| NetEase Cloud Music | Numeric UID, nickname, avatar, bindings | Public following; bounded reverse checks; cross-platform bindings | Undocumented Web API, unstable | Following graph, optional mutual/degree-2 expansion, explicit partial status for unavailable followers |
| Weibo | UID, screen name, profile URL | Fans, follows, mutual flags, mentions, repost targets | Official REST requires app key; current Web collector requires user-authorized cookie and may hit captcha | Direct record dispatch plus binding traversal; auth/captcha status |
| Bilibili | Numeric UID, nickname, profile URL | Followers and following from observed Web/mobile endpoints | Undocumented public endpoints, rate limiting and anti-bot behavior | Best-effort directed one-hop graph with partial/rate-limited status |
| Xiaohongshu | User ID, signed profile URL | Mentions from posted notes; commenter expansion is not yet reliable | User-authorized cookie plus request signer | Direct interaction dispatch with explicit auth/signer status |
| Gravatar | Email-derived profile and linked accounts | No social-edge surface | Public profile lookup | Identity evidence only; explicit unsupported relationship status |
| Douyin | Profile/manual discovery in embedded crawler | Embedded crawler can read content/comments under login, but no unified stable account relation adapter | Login, signatures, frequent endpoint drift | Coverage manifest only until a stable typed adapter exists |
| Kuaishou | Profile/manual discovery | No current typed relation collector | Login and anti-bot controls | Coverage manifest: unsupported |
| QQ/Qzone | Numeric account/profile hints and NetEase binding | No current typed relation collector | Login/private visibility | Coverage manifest: unsupported/auth-required |
| Zhihu | Public profile discovery | No current typed relation collector | Public API drift and login controls | Coverage manifest: unsupported |
| X/Twitter, Instagram, Facebook, Telegram | Maigret profile discovery | No credentialed official relation adapter in this workspace | Platform token/login and policy constraints | Coverage manifest: unsupported, never silently omitted |

## Verified Boundaries

- GitHub officially documents public `GET /users/{username}/followers` and
  `GET /users/{username}/following` endpoints and a directed follow check.
  Source: <https://docs.github.com/en/rest/users/followers>.
- Weibo's official followers endpoint returned error `10006` without a source
  app key on 2026-06-20. Source:
  <https://api.weibo.com/2/friendships/followers.json>.
- NetEase's observed `getfolloweds` endpoint returned application code `301` in
  an anonymous check on 2026-06-20, so follower collection cannot be claimed as
  publicly available. Following and binding endpoints remain best-effort.
- Bilibili relationship endpoints are observed Web/mobile surfaces rather than a
  stable developer contract. A current anonymous probe timed out, which must be
  represented as unavailable/partial rather than an empty friend list.
- Xiaohongshu collection requires both an authorized cookie and request signing;
  cookie absence, signer absence, API denial, and empty successful data are
  distinct states.

## Unified Data Semantics

### Collection status

Every attempted or unsupported capability emits:

- `site` and platform-qualified `account_id`
- `capability`: `followers`, `following`, `interactions`, or `bindings`
- `state`: `complete`, `partial`, `empty`, `auth_required`, `rate_limited`,
  `unavailable`, `unsupported`, or `error`
- `access`: `public`, `authorized`, or `unsupported`
- bounded human-readable detail with no secrets

An empty successful response is not equivalent to a failed request.

### Relationship semantics

- Directed follow: observed edge from follower to followed account.
- Mutual follow: two observed directions, represented as a strong reciprocal
  relationship without inventing an offline friendship.
- Mention/repost: directed observed interaction with bounded frequency weight.
- Shared neighborhood: inferred candidate, always lower confidence than mutual
  follow and always labeled `inferred`.
- Identity aliases: only confirmed/likely identity clusters collapse accounts
  across platforms before network metrics are computed.

### Network analysis

The unified report must include:

- node/edge counts, density, reciprocity, weak/strong component counts
- in-degree, out-degree, total degree, and normalized degree centrality
- betweenness centrality and bridge ranking
- undirected articulation points where meaningful
- deterministic greedy-modularity communities
- per-pair relationship profiles combining independent observed/inferred evidence
  with `1 - product(1 - score)` and retaining platforms/actions/evidence

Metrics describe the collected graph, not the complete real-world network. Every
report records coverage and collection states beside metrics to prevent false
completeness claims.

## Privacy and Safety Invariants

- Complete phones remain strong identity evidence by default but raw values never
  enter social nodes, edges, status details, metrics, logs, or reports.
- No phone reverse lookup, contact upload, credential extraction, captcha bypass,
  private-list access, or automatic follow/unfollow action.
- Cookies, tokens, and signatures are consumed only through existing authorized
  mechanisms and never serialized into collection status.
- Failed or blocked collectors do not fabricate empty networks.

