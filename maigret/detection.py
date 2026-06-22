"""Pure response classification for Maigret site checks."""

from dataclasses import dataclass
from typing import Iterable, Optional

from .result import MaigretCheckStatus


@dataclass(frozen=True)
class DetectionDecision:
    status: MaigretCheckStatus
    presence_marker: Optional[str] = None


def classify_response(
    check_type: str,
    html_text: str,
    status_code: int,
    presence_strings: Iterable[str],
    absence_strings: Iterable[str],
    *,
    has_error: bool = False,
) -> DetectionDecision:
    """Classify a normalized transport response without network side effects."""

    if has_error:
        return DetectionDecision(MaigretCheckStatus.UNKNOWN)

    body = html_text or ""
    presence_strings = list(presence_strings or [])
    marker = next((value for value in presence_strings if value in body), None)
    presence_detected = bool(body) if not presence_strings else marker is not None

    if check_type == "message":
        absence_detected = any(value in body for value in (absence_strings or []))
        status = (
            MaigretCheckStatus.CLAIMED
            if not absence_detected and presence_detected
            else MaigretCheckStatus.AVAILABLE
        )
    elif check_type == "status_code":
        status = (
            MaigretCheckStatus.CLAIMED
            if 200 <= status_code < 300
            else MaigretCheckStatus.AVAILABLE
        )
    elif check_type == "response_url":
        status = (
            MaigretCheckStatus.CLAIMED
            if 200 <= status_code < 300 and presence_detected
            else MaigretCheckStatus.AVAILABLE
        )
    else:
        raise ValueError(f"Unknown check type '{check_type}'")

    return DetectionDecision(status, marker)
