"""USB installation packages: operator-only issuance, never console credentials."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from riji_agent.mobile.store import MobileError, MobileStore

MAX_ORIGIN_BYTES = 2048
MAX_PACKAGE_BYTES = 16384


def installation_bytes(payload: dict) -> bytes:
    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    if len(data) > MAX_PACKAGE_BYTES:
        raise MobileError("installation_package_too_large")
    return data


def canonical_origin(value: str) -> str:
    try:
        if len(value.encode("utf-8")) > MAX_ORIGIN_BYTES:
            raise ValueError
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
        if (any(character.isspace() for character in value) or parts.scheme != "https" or not host or parts.username is not None
                or parts.password is not None or parts.path not in {"", "/"}
                or parts.query or parts.fragment or "?" in value or "#" in value
                or not re.fullmatch(r"[A-Za-z0-9.:-]+", host)
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError
        host = "[" + host + "]" if ":" in host else host
        origin = "https://" + host.lower() + (":" + str(port) if port and port != 443 else "")
        if len(origin.encode("utf-8")) > MAX_ORIGIN_BYTES:
            raise ValueError
        return origin
    except ValueError:
        raise MobileError("https_origin_required") from None


def write_installation(settings, store: MobileStore, args) -> None:
    from riji_agent.mobile.cli import verified_principal

    verified_principal(settings, args.principal)
    payload = {"api_version": 1, "endpoint": canonical_origin(args.url)}
    if args.previous_endpoint:
        payload["previous_endpoint"] = canonical_origin(args.previous_endpoint)
    # Reserve the complete maximum credential fields before issuing any device.
    installation_bytes({**payload, "device_id": "x" * 128, "device_token": "x" * 128})
    output = Path(args.output).expanduser()
    if output.resolve().is_relative_to(settings.journal_root.resolve()):
        raise MobileError("installation_output_outside_vault_required")
    if not args.device_name.strip() or len(args.device_name) > 100:
        raise MobileError("invalid_device_name")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    issued = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            issued = store.provision(args.principal, args.device_name)
            payload.update(issued)
            stream.write(installation_bytes(payload))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            if issued is not None:
                store.revoke(issued["device_id"])
        finally:
            output.unlink(missing_ok=True)
        raise
