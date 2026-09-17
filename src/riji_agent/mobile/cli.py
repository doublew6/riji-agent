"""Explicit local enrollment and proxy commands; never print credential material."""

from __future__ import annotations

import json
import os
from pathlib import Path

from riji_agent.config import load_settings
from riji_agent.mentors.configuration import MentorConfig
from riji_agent.mentors.models import Principal
from riji_agent.mentors.store import MentorStore
from riji_agent.mobile.store import MobileError, MobileStore


def register_mobile_commands(sub) -> None:
    parser = sub.add_parser("mobile", help="Manage private mobile devices and ingress.")
    actions = parser.add_subparsers(dest="mobile_action", required=True)
    pair = actions.add_parser("pair", help="Write a one-time pairing package to a new private file.")
    pair.add_argument("--principal", required=True)
    pair.add_argument("--output", required=True)
    pair.add_argument("--url", required=True, help="Verified private HTTPS base URL.")
    actions.add_parser("devices", help="List local device IDs and revocation state.")
    revoke = actions.add_parser("revoke", help="Revoke a specific device credential.")
    revoke.add_argument("--device", required=True)
    actions.add_parser("proxy", help="Serve only mobile paths on loopback port 8766.")


def verified_principal(settings, identifier: str) -> Principal:
    if not settings.mentors_enabled or settings.mentors_config_path is None:
        raise MobileError("mentor_runtime_required")
    config = MentorConfig.load(settings.mentors_config_path, settings.journal_root)
    store = MentorStore(settings.data_dir / "mentors.sqlite3")
    principal = store.read("principal", identifier, Principal)
    if principal is None or not any(user.account == principal.account and
                                    user.legacy_owner_key == principal.legacy_owner_key for user in config.users):
        raise MobileError("verified_owner_required")
    return principal


def write_pairing(settings, store: MobileStore, args) -> None:
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(args.url)
        port = parsed.port
    except ValueError:
        raise MobileError("private_https_origin_required") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or (port is not None and not 1 <= port <= 65535)):
        raise MobileError("private_https_origin_required")
    verified_principal(settings, args.principal)
    output = Path(args.output).expanduser()
    if output.resolve().is_relative_to(settings.journal_root.resolve()):
        raise MobileError("pairing_output_outside_vault_required")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        code = store.pairing(args.principal)
        json.dump({"api_version": 1, "base_url": args.url.rstrip("/"), "code": code,
                   "expires_in_seconds": 600}, stream)
        stream.flush()
        os.fsync(stream.fileno())


def run_mobile_command(args) -> int:
    try:
        settings = load_settings()
        if not settings.mobile_enabled:
            raise MobileError("mobile_disabled")
        if args.mobile_action == "proxy":
            import uvicorn
            from riji_agent.mobile.proxy import create_proxy_app
            uvicorn.run(create_proxy_app(), host="127.0.0.1", port=8766,
                        access_log=False, proxy_headers=False)
            return 0
        store = MobileStore(settings.data_dir / "mobile.sqlite3")
        if args.mobile_action == "pair":
            write_pairing(settings, store, args)
            print("Pairing package written to the requested private file; expires in 10 minutes.")
        elif args.mobile_action == "devices":
            print(json.dumps({"devices": store.devices()}))
        elif args.mobile_action == "revoke":
            store.revoke(args.device)
            print("Device revoked; existing IM conversations are unchanged.")
        return 0
    except Exception:
        print("Mobile command failed; check enabled configuration, verified owner and private output permissions.")
        return 1
