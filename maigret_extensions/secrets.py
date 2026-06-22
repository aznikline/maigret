"""Credential and sensitive JSON storage for local extension workflows."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Mapping, Optional


class SecretStoreError(RuntimeError):
    pass


def _prefix(platform_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", platform_name).strip("_").upper()
    return f"MAIGRET_{normalized}"


def _instructions(platform_name: str) -> str:
    prefix = _prefix(platform_name)
    return (
        f"Set {prefix}_PHONE and {prefix}_PASSWORD environment variables, "
        "or run setup on macOS to store them in Keychain."
    )


def load_credentials(
    platform_name: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    system: Optional[str] = None,
    runner: Optional[Callable] = None,
    required: bool = False,
) -> Optional[dict]:
    environ = os.environ if environ is None else environ
    prefix = _prefix(platform_name)
    phone = environ.get(f"{prefix}_PHONE")
    password = environ.get(f"{prefix}_PASSWORD")
    if phone and password:
        return {"phone": phone, "password": password}

    system = platform.system() if system is None else system
    if system == "Darwin":
        runner = subprocess.run if runner is None else runner
        result = runner(
            [
                "security",
                "find-generic-password",
                "-s",
                f"maigret.{platform_name}",
                "-a",
                "credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
            except (TypeError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict) and data.get("phone") and data.get("password"):
                return {"phone": str(data["phone"]), "password": str(data["password"])}

    if required:
        raise SecretStoreError(_instructions(platform_name))
    return None


def save_credentials(
    platform_name: str,
    phone: str,
    password: str,
    *,
    system: Optional[str] = None,
    runner: Optional[Callable] = None,
) -> None:
    system = platform.system() if system is None else system
    if system != "Darwin":
        raise SecretStoreError(
            "Credential files are disabled; use environment variables. "
            + _instructions(platform_name)
        )

    runner = subprocess.run if runner is None else runner
    payload = json.dumps({"phone": phone, "password": password})
    result = runner(
        [
            "security",
            "add-generic-password",
            "-U",
            "-s",
            f"maigret.{platform_name}",
            "-a",
            "credentials",
            "-w",
        ],
        input=payload + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or "Keychain command failed").strip()
        raise SecretStoreError(f"Could not save credentials to macOS Keychain: {detail}")


def has_credentials(platform_name: str) -> bool:
    return load_credentials(platform_name) is not None


def secure_write_json(path, data) -> None:
    """Atomically write JSON with owner-only permissions."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            descriptor = -1
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
