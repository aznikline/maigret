import json
import os
import subprocess

import pytest

from maigret_extensions import secrets


def test_environment_credentials_take_precedence():
    env = {
        "MAIGRET_DIANPING_PHONE": "13800000000",
        "MAIGRET_DIANPING_PASSWORD": "secret",
    }

    credentials = secrets.load_credentials(
        "dianping",
        environ=env,
        system="Darwin",
        runner=lambda *args, **kwargs: pytest.fail("Keychain should not be called"),
    )

    assert credentials == {"phone": "13800000000", "password": "secret"}


def test_keychain_credentials_are_decoded():
    completed = subprocess.CompletedProcess(
        [], 0, stdout=json.dumps({"phone": "138", "password": "pw"}), stderr=""
    )

    credentials = secrets.load_credentials(
        "dianping", environ={}, system="Darwin", runner=lambda *a, **k: completed
    )

    assert credentials == {"phone": "138", "password": "pw"}


def test_required_credentials_fail_with_environment_instructions():
    with pytest.raises(secrets.SecretStoreError, match="MAIGRET_DIANPING_PHONE"):
        secrets.load_credentials(
            "dianping", environ={}, system="Linux", required=True
        )


def test_non_macos_credential_save_fails_closed():
    with pytest.raises(secrets.SecretStoreError, match="environment variables"):
        secrets.save_credentials("dianping", "138", "pw", system="Linux")


def test_macos_credential_save_uses_stdin_not_process_arguments():
    captured = {}

    def runner(args, **kwargs):
        captured["args"] = args
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    secrets.save_credentials(
        "dianping", "138", "private-password", system="Darwin", runner=runner
    )

    assert captured["args"][-1] == "-w"
    assert "private-password" not in captured["args"]
    assert json.loads(captured["input"]) == {
        "phone": "138",
        "password": "private-password",
    }


def test_secure_json_write_is_atomic_and_owner_only(tmp_path):
    target = tmp_path / "cookies.json"

    secrets.secure_write_json(target, {"cookies": [{"name": "sid", "value": "x"}]})

    assert json.loads(target.read_text()) == {
        "cookies": [{"name": "sid", "value": "x"}]
    }
    assert os.stat(target).st_mode & 0o777 == 0o600
