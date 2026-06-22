"""Privacy-preserving handling for phone observations."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import asdict
from typing import Optional

from .models import Entity


PHONE_FINGERPRINT_VERSION = "hmac-sha256:v1"
REDACTED_PHONE = "[redacted-phone]"


class PhonePrivacyError(ValueError):
    pass


def normalize_phone(value) -> Optional[str]:
    """Return a full canonical phone or None; never infer masked digits."""
    raw = str(value or "").strip()
    if not raw or re.search(r"[A-Za-z*•xX]", raw):
        return None
    has_plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if has_plus:
        return f"+{digits}" if 7 <= len(digits) <= 15 else None
    if len(digits) == 11 and digits.startswith("1"):
        return f"+86{digits}"
    if len(digits) == 13 and digits.startswith("86"):
        return f"+{digits}"
    return None


def phone_fingerprint(value, key: str) -> str:
    if not key or len(key.encode("utf-8")) < 16:
        raise PhonePrivacyError(
            "Phone correlation requires MAIGRET_PHONE_HASH_KEY with at least 16 bytes"
        )
    normalized = normalize_phone(value)
    if normalized is None:
        raise PhonePrivacyError("Phone correlation requires a complete phone number")
    digest = hmac.new(
        key.encode("utf-8"),
        f"phone:v1:{normalized}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{PHONE_FINGERPRINT_VERSION}:{digest}"


def safe_entity_dict(
    entity: Entity, *, allow_phone: bool = True, phone_key: Optional[str] = None
) -> dict:
    data = asdict(entity)
    if entity.kind != "phone":
        return data
    data["value"] = REDACTED_PHONE
    data["phone_redacted"] = True
    if allow_phone and phone_key and normalize_phone(entity.value):
        data["phone_fingerprint"] = phone_fingerprint(entity.value, phone_key)
    return data


def sanitize_binding(
    binding: dict, *, allow_phone: bool = True, phone_key: Optional[str] = None
) -> dict:
    data = dict(binding)
    if str(data.get("platform", "")).lower() != "phone":
        return data
    raw = data.get("id") or data.get("url")
    data["id"] = None
    data["url"] = ""
    data["phone_redacted"] = True
    if allow_phone and phone_key and normalize_phone(raw):
        data["phone_fingerprint"] = phone_fingerprint(raw, phone_key)
    return data
