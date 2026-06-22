import pytest

from maigret.detection import classify_response
from maigret.result import MaigretCheckStatus


@pytest.mark.parametrize(
    "check_type,body,status,presence,absence,expected",
    [
        ("message", "user profile", 200, ["profile"], ["not found"], MaigretCheckStatus.CLAIMED),
        ("message", "not found", 200, ["profile"], ["not found"], MaigretCheckStatus.AVAILABLE),
        ("status_code", "", 204, [], [], MaigretCheckStatus.CLAIMED),
        ("status_code", "", 404, [], [], MaigretCheckStatus.AVAILABLE),
        ("response_url", "profile", 200, ["profile"], [], MaigretCheckStatus.CLAIMED),
        ("response_url", "", 200, [], [], MaigretCheckStatus.AVAILABLE),
    ],
)
def test_classify_response_supported_modes(
    check_type, body, status, presence, absence, expected
):
    decision = classify_response(check_type, body, status, presence, absence)
    assert decision.status is expected


def test_message_without_presence_markers_claims_only_nonempty_body():
    assert (
        classify_response("message", "some page", 200, [], []).status
        is MaigretCheckStatus.CLAIMED
    )
    assert (
        classify_response("message", "", 200, [], []).status
        is MaigretCheckStatus.AVAILABLE
    )


def test_transport_error_is_unknown():
    decision = classify_response("status_code", "", 200, [], [], has_error=True)
    assert decision.status is MaigretCheckStatus.UNKNOWN


def test_unknown_check_type_is_rejected():
    with pytest.raises(ValueError, match="Unknown check type"):
        classify_response("custom", "body", 200, [], [])
