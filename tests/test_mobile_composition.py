"""Opt-in composition, immutable Fleet metadata and private enrollment output."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from riji_agent.config import Settings
from riji_agent.main import create_app
from riji_agent.mobile.cli import write_pairing
from riji_agent.mobile.store import MobileError, MobileStore
from test_mentor_runtime import runtime as mentor_runtime


def settings_for(tmp_path, **overrides):
    root = tmp_path / "vault"
    root.mkdir(exist_ok=True)
    return Settings(_env_file=None, RIJI_JOURNAL_ROOT=str(root), RIJI_DATA_DIR=str(tmp_path / "data"),
                    DEEPSEEK_API_KEY="test-secret", RIJI_ALLOWED_FEISHU_USER_IDS="synthetic-owner",
                    HERMES_SHARED_SECRET="test-transport", **overrides)


def test_fleet_build_is_startup_frozen_and_optional(tmp_path):
    settings = settings_for(tmp_path, RIJI_DEPLOYMENT_REVISION="synthetic-revision-one")
    app = create_app(settings)
    client = TestClient(app)
    value = client.get("/.well-known/fleet").json()
    assert value == {"service": "riji-agent", "version": "0.1.0", "revision": "synthetic-revision-one"}
    settings.deployment_revision = "mutated-after-startup"
    assert client.get("/.well-known/fleet").json() == value
    settings.deployment_revision = ""
    assert TestClient(create_app(settings)).get("/.well-known/fleet").status_code == 404
    assert str(tmp_path) not in json.dumps(value)


def test_mobile_routes_are_disabled_by_default(tmp_path, mentor_runtime):
    runtime, _, _ = mentor_runtime
    settings = settings_for(tmp_path)
    # The existing Hermes router accesses gateway methods lazily, so only the
    # composition dependencies are needed to prove route registration boundaries.
    gateway = SimpleNamespace(mentor_runtime=runtime, memory_service=None)
    disabled = TestClient(create_app(settings, gateway=gateway))
    assert disabled.get("/mobile/v1/capabilities").status_code == 404
    assert disabled.get("/healthz").status_code == 200
    assert disabled.get("/api/mentors/v1/me").status_code == 401
    settings.mobile_enabled = True
    enabled = TestClient(create_app(settings, gateway=gateway))
    assert enabled.get("/mobile/v1/capabilities").status_code == 401
    assert enabled.get("/api/mentors/v1/me").status_code == 401


@pytest.mark.parametrize("port", [None, 8443])
def test_cli_pairing_output_is_private_exclusive_and_bound(tmp_path, mentor_runtime, port):
    runtime, _, _ = mentor_runtime
    principal = next(iter(runtime.user_tokens.values()))
    settings = settings_for(tmp_path, RIJI_MOBILE_ENABLED=True, RIJI_MENTORS_ENABLED=True,
                            RIJI_MENTORS_CONFIG_PATH=str(tmp_path / "mentor-config.json"))
    store = MobileStore(tmp_path / "mobile-enrollment.sqlite3")
    output = tmp_path / "private-pairing.json"
    origin = "https://air.example" + (f":{port}" if port is not None else "")
    args = SimpleNamespace(principal=principal, output=str(output), url=origin)
    write_pairing(settings, store, args)
    assert output.stat().st_mode & 0o777 == 0o600
    package = json.loads(output.read_text())
    assert package["base_url"] == args.url and package["expires_in_seconds"] == 600
    assert store.pair(package["code"], "Synthetic phone")["device_token"]
    with pytest.raises(FileExistsError):
        write_pairing(settings, store, args)


@pytest.mark.parametrize("port", ["0", "65536", "-1", "invalid"])
def test_cli_pairing_rejects_invalid_https_ports_before_enrollment(port):
    args = SimpleNamespace(url="https://air.example:" + port)
    with pytest.raises(MobileError, match="private_https_origin_required"):
        write_pairing(None, None, args)
