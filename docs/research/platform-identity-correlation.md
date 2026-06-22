# Platform Identity and Social-Correlation Research

Date: 2026-06-19

## Purpose

Define which signals can responsibly associate accounts across platforms and
which signals can support social-relationship inference. This document separates
observed platform data from heuristic conclusions. It does not authorize account
enumeration, phone reverse lookup, contact uploads, or access-control bypass.

## Platform Capability Matrix

| Platform | Identity signals | Social signals | Access boundary | Correlation value |
| --- | --- | --- | --- | --- |
| NetEase Cloud Music | Numeric UID, nickname, avatar, `bindings` entries for Weibo/phone/WeChat/QQ/Apple/mail | Following list, reported mutual flag, bounded reverse-follow check | Public web endpoints observed by the existing collector; undocumented and unstable | A binding URL to another platform is explicit high-confidence evidence. Phone binding values must never be exported raw. |
| Weibo | UID, screen name, profile, avatar | Follows, fans, mutual flags, mentions, repost targets | Official API endpoints require an app key; current collector uses a user-authorized Web session and may encounter captcha | Direct binding from NetEase to a Weibo UID is strong identity evidence. Follow is an observed online edge, not proof of an offline relationship. |
| GitHub | Stable numeric ID/login, public name, blog, location, public email, `twitter_username` | Followers and following | Public REST API; anonymous and token rate limits apply | Verified/public email and explicit profile links are strong; username alone is weak. |
| Gravatar | Email-derived public profile, display name, linked accounts | None | Public profile lookup | Explicit linked accounts and matching verified email are strong identity evidence. |
| Bilibili | UID, nickname, avatar | Followers/following from public endpoints, frequently rate-limited | No stable cross-platform binding contract identified | Useful for social edges after a UID is known; insufficient for phone or identity lookup. |
| Xiaohongshu | User ID, nickname, signed profile/note data | Mentions and commenters where authorized | Valid login cookie plus request signature; captcha and access restrictions apply | Interaction evidence only. No phone-based identity lookup is supported. |

## Verified External Surfaces

- GitHub's REST user response exposes stable ID/login plus optional public
  `email`, `blog`, and `twitter_username`; the followers endpoint exposes account
  IDs. Sources: [GitHub users API](https://docs.github.com/en/rest/users/users),
  [GitHub followers API](https://docs.github.com/en/rest/users/followers).
- Weibo's official `users/show` and `friendships/friends` endpoints returned
  `source parameter (appkey) is missing` without an application credential. The
  implementation must not present cookie-backed Web endpoints as equivalent to
  an official anonymous API. Sources: [users/show](https://api.weibo.com/2/users/show.json),
  [friendships/friends](https://api.weibo.com/2/friendships/friends.json).
- NetEase binding behavior is grounded in the observed response consumed by
  `relations._netease_bindings`; no stable official developer contract was found.
  It must be treated as best-effort and never as an availability guarantee.

## Evidence Policy

| Evidence | Base confidence | Decision role |
| --- | ---: | --- |
| Explicit platform binding URL/ID | 0.99 | Confirmed identity link |
| Same complete normalized phone | 0.95 | Confirmed identity link; optional HMAC fingerprint |
| Same public/verified email | 0.90 | Confirmed identity link |
| Same avatar perceptual hash | 0.80 | Likely identity link |
| Same exact avatar URL | 0.75 | Likely identity link |
| Same username | 0.25 | Weak candidate only; never auto-merge |
| Same display name | 0.10 | Context only; never creates a link alone |

Independent evidence combines as `1 - product(1 - confidence)`. Evidence from the
same family is counted once at its strongest value. Confirmed links require 0.90,
likely links 0.70, possible links 0.40, and weaker observations remain unmerged.

## Phone Privacy Rules

1. Complete phone numbers are strong identity evidence and correlate by default.
2. Matching occurs only in process memory; `MAIGRET_PHONE_HASH_KEY` is optional
   and adds a stable HMAC fingerprint to redacted output.
3. Masked or partial numbers are rejected, not guessed.
4. Full numbers exist only in process memory long enough to normalize and compare.
5. JSON, graph attributes, logs, and console summaries contain only an optional
   versioned HMAC fingerprint and redacted display value.
6. Plain SHA hashes are prohibited because phone numbers have low entropy.
7. The system never queries a platform by phone number and never uploads contacts.

## Social Inference Policy

- **Observed direct:** follows, mutual follows, mentions, reposts. These describe
  online platform actions only.
- **Inferred:** two accounts share at least two outgoing neighbors and pass a
  Jaccard threshold. This is labeled `shared_neighborhood`, never `friend`.
- **Cross-platform collapse:** only confirmed/likely identity links can collapse
  account nodes before social inference. Username-only candidates cannot.
- **Scoring:** mutual follow 0.95, repeated mention up to 0.85, repost 0.75,
  one-way follow 0.45, shared neighborhood up to 0.65.
- Celebrity/high-degree overlap remains a limitation; shared-neighbor results are
  candidates for review, not factual relationships.

## Baseline Findings and Resolution

- Bare social IDs could collide across platforms; node and edge keys now include
  the platform while outbound collectors continue using native IDs.
- Degree lookup previously mixed qualified and unqualified keys; graph enrichment
  now retains platform-qualified second-degree metadata.
- NetEase phone bindings previously reached relation and cross-platform JSON raw;
  all new serializers now redact them, with optional versioned HMAC output.
- Mention counts can disappear during dataclass serialization; social inference
  intentionally uses the persisted `weight` field.
