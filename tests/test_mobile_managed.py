"""Synthetic public-admission and operator-provisioning failure boundaries."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from riji_agent.mobile.provisioning import canonical_origin, write_installation
from riji_agent.mobile.public_proxy import PublicLimits, create_public_proxy_app
from riji_agent.mobile.store import MobileError, MobileStore
from test_mobile_composition import settings_for
from test_mentor_runtime import runtime as mentor_runtime
from test_mobile_api import mobile, drain


@pytest.fixture
def public(tmp_path):
    store = MobileStore(tmp_path / "mobile.sqlite3")
    device = store.provision("synthetic-owner", "Synthetic phone")
    return store, device, {"Authorization": "Bearer " + device["device_token"]}


def provision_args(tmp_path, principal):
    return SimpleNamespace(principal=principal, output=str(tmp_path / "installation.json"),
                           url="https://AIR.example:10000/", previous_endpoint="https://air.example:8443",
                           device_name="Synthetic phone")


def test_operator_issues_unique_private_package_for_verified_owner(tmp_path, mentor_runtime):
    runtime, _, _ = mentor_runtime
    principal = next(iter(runtime.user_tokens.values()))
    settings = settings_for(tmp_path, RIJI_MOBILE_ENABLED=True, RIJI_MENTORS_ENABLED=True,
                            RIJI_MENTORS_CONFIG_PATH=str(tmp_path / "mentor-config.json"))
    store = MobileStore(tmp_path / "mobile.sqlite3")
    args = provision_args(tmp_path, principal)
    write_installation(settings, store, args)
    from pathlib import Path
    output = Path(args.output)
    assert output.stat().st_mode & 0o777 == 0o600
    package = json.loads(output.read_text())
    assert set(package) == {"api_version", "endpoint", "previous_endpoint", "device_id", "device_token"}
    assert package["endpoint"] == "https://air.example:10000"
    assert store.authenticate(package["device_token"])["principal"] == principal
    with pytest.raises(FileExistsError):
        write_installation(settings, store, args)
    assert len(store.devices()) == 1
    args.output = str(tmp_path / "second.json")
    write_installation(settings, store, args)
    second = json.loads(Path(args.output).read_text())
    assert package["device_id"] != second["device_id"] and package["device_token"] != second["device_token"]
    assert package["device_token"].encode() not in store.path.read_bytes()


def test_provisioning_rejects_unverified_owner_vault_symlink_and_cleans_failed_issue(tmp_path, mentor_runtime, monkeypatch):
    runtime, _, _ = mentor_runtime
    principal = next(iter(runtime.user_tokens.values()))
    settings = settings_for(tmp_path, RIJI_MOBILE_ENABLED=True, RIJI_MENTORS_ENABLED=True,
                            RIJI_MENTORS_CONFIG_PATH=str(tmp_path / "mentor-config.json"))
    store = MobileStore(tmp_path / "mobile.sqlite3")
    args = provision_args(tmp_path, "unknown-owner")
    with pytest.raises(MobileError):
        write_installation(settings, store, args)
    args.principal = principal
    args.output = str(settings.journal_root / "secret.json")
    with pytest.raises(MobileError):
        write_installation(settings, store, args)
    output = tmp_path / "installation.json"
    output.symlink_to(tmp_path / "target.json")
    args.output = str(output)
    with pytest.raises(OSError):
        write_installation(settings, store, args)
    output.unlink()
    def failure(*args):
        raise OSError("synthetic disk failure")
    monkeypatch.setattr("riji_agent.mobile.provisioning.os.fsync", failure)
    with pytest.raises(OSError):
        write_installation(settings, store, args)
    assert not output.exists()
    assert len(store.devices()) == 1 and store.devices()[0]["revoked"] == 1


@pytest.mark.parametrize("origin", ["http://air.example", "https://user:pw@air.example", "https://air.example/path",
    "https://air.example?", "https://air.example#", "https://air.example:0", "https://air.example:99999", "https://air.example\n"])
def test_installation_origin_validation(origin):
    with pytest.raises(MobileError):
        canonical_origin(origin)


def test_public_cli_uses_fixed_loopback_port_and_disables_access_logs_and_forwarded_trust(tmp_path, monkeypatch, capsys):
    from riji_agent.mobile.cli import run_mobile_command
    calls = []
    monkeypatch.setattr("riji_agent.mobile.cli.load_settings", lambda: SimpleNamespace(mobile_enabled=True, data_dir=tmp_path))
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: calls.append(kwargs))
    assert run_mobile_command(SimpleNamespace(mobile_action="public-proxy")) == 0
    assert calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 8767
    assert calls[0]["access_log"] is False and calls[0]["proxy_headers"] is False
    assert calls[0]["http"] == "h11" and calls[0]["limit_concurrency"] == 32
    assert calls[0]["h11_max_incomplete_event_size"] == 16384
    assert calls[0]["headers"] == [("Cache-Control", "no-store")]
    assert not (tmp_path / "mobile.sqlite3").exists()
    assert capsys.readouterr().out == ""


def test_public_authentication_and_revocation_are_checked_before_reading_body(public):
    store, device, headers = public
    calls = []
    app = create_public_proxy_app(store.path, transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={})))
    async def check(path, authorization, expected):
        async def receive():
            pytest.fail("Rejected request body must not be read")
        sent = []
        async def send(value):
            sent.append(value)
        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                   "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
                   "headers": [(b"authorization", authorization.encode())] if authorization else [],
                   "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8767)}, receive, send)
        assert sent[0]["status"] == expected
    asyncio.run(check("/mobile/v1/privacy/actions", "", 401))
    asyncio.run(check("/mobile/v1/privacy/actions", "Bearer " + "x" * 64, 401))
    for path in ["/mobile/v1/pair", "/admin/memory", "/hermes/messages", "/healthz", "/.well-known/fleet"]:
        asyncio.run(check(path, headers["Authorization"], 404))
    client = TestClient(app)
    assert client.get("/mobile/v1/capabilities", headers=headers).status_code == 200
    store.revoke(device["device_id"])
    asyncio.run(check("/mobile/v1/privacy/actions", headers["Authorization"], 401))
    assert len(calls) == 1


def test_provisioning_origin_and_serialized_package_exact_byte_limits(tmp_path, monkeypatch):
    from riji_agent.mobile import provisioning
    origin = "https://" + "a" * (2048 - len("https://"))
    assert len(canonical_origin(origin).encode()) == 2048
    with pytest.raises(MobileError):
        canonical_origin(origin + "a")
    overhead = len(provisioning.installation_bytes({"synthetic": ""}))
    assert len(provisioning.installation_bytes({"synthetic": "a" * (16384 - overhead)})) == 16384
    with pytest.raises(MobileError, match="installation_package_too_large"):
        provisioning.installation_bytes({"synthetic": "a" * (16385 - overhead)})
    monkeypatch.setattr("riji_agent.mobile.cli.verified_principal", lambda *args: None)
    store = MobileStore(tmp_path / "mobile.sqlite3")
    args = provision_args(tmp_path, "synthetic-owner")
    settings = SimpleNamespace(journal_root=tmp_path / "vault")
    args.url = origin + "a"
    with pytest.raises(MobileError):
        write_installation(settings, store, args)
    args.url = "https://air.example"
    monkeypatch.setattr(provisioning, "MAX_PACKAGE_BYTES", 128)
    with pytest.raises(MobileError, match="installation_package_too_large"):
        write_installation(settings, store, args)
    assert store.devices() == [] and not (tmp_path / "installation.json").exists()


def test_anonymous_minute_quota_never_spends_authorized_budget(public):
    store, _, headers = public
    client = TestClient(create_public_proxy_app(store.path, limits=PublicLimits(global_per_minute=1, failed_auth_per_minute=2),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))))
    for index in range(8):
        response = client.get("/mobile/v1/capabilities", headers={"Authorization": "Bearer " + "x" * 64})
        assert response.status_code == (401 if index < 2 else 429)
    assert client.get("/mobile/v1/capabilities", headers=headers).status_code == 200


def test_locked_auth_lookup_is_offloaded_and_concurrency_is_admitted_before_lookup(public, monkeypatch):
    import sqlite3
    import time
    from riji_agent.mobile.public_proxy import Admission
    store, _, headers = public
    calls = []
    original = Admission.authenticate
    def lookup(self, authorization):
        calls.append(1)
        return original(self, authorization)
    monkeypatch.setattr(Admission, "authenticate", lookup)
    app = create_public_proxy_app(store.path, limits=PublicLimits(concurrency=6),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    db = sqlite3.connect(store.path)
    db.execute("BEGIN EXCLUSIVE")
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            started = time.monotonic()
            pending = [asyncio.create_task(client.get("/mobile/v1/capabilities", headers=headers)) for _ in range(12)]
            await asyncio.sleep(0.01)
            assert time.monotonic() - started < 0.15, "SQLite lock waits blocked the event loop"
            assert len(calls) <= 6
            responses = await asyncio.gather(*pending)
            assert all(response.status_code == 503 for response in responses)
            assert len(calls) == 6
            db.rollback()
            assert (await client.get("/mobile/v1/capabilities", headers=headers)).status_code == 200
    try:
        asyncio.run(run())
    finally:
        db.close()


def test_authorized_public_post_preserves_mentor_and_memory_mutation_body_and_receipt(mobile):
    service, backend_client, _, paired = mobile
    app = create_public_proxy_app(service.store.path, transport=httpx.ASGITransport(app=backend_client.app))
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer " + paired["device_token"]
    body = {"operation_id": "public-message-one", "text": "Exact synthetic public mentor input 中文"}
    response = client.post("/mobile/v1/mentors/gentle_reviewer/messages", json=body)
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["operation_id"] == body["operation_id"] and receipt["state"] == "completed"
    assert receipt["kind"] == "mentor_message"
    drain(service, receipt["result"]["conversation_id"])
    messages = client.get("/mobile/v1/mentors/gentle_reviewer/conversation").json()["messages"]
    assert messages[0]["actor"] == "user" and messages[0]["text"] == body["text"]
    assert client.get("/mobile/v1/operations/" + body["operation_id"]).json() == receipt
    version = client.get("/mobile/v1/memories/memory-one").json()["version"]
    mutation = {"operation_id": "public-memory-one", "expected_version": version, "action": "update", "content": "Exact public correction 中文"}
    response = client.post("/mobile/v1/memories/memory-one/actions", json=mutation)
    assert response.status_code == 200 and response.json()["state"] == "completed"
    assert response.json()["operation_id"] == mutation["operation_id"]
    assert service.memory.service.backend.get("memory-one").content == mutation["content"]
    assert client.get("/mobile/v1/operations/" + mutation["operation_id"]).json() == response.json()
def test_public_boundaries_strip_headers_and_recover_after_large_body_redirect_timeout(public):
    store, _, headers = public
    calls = []
    async def upstream(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "https://elsewhere.example"})
        if len(calls) == 2:
            await asyncio.sleep(0.1)
        return httpx.Response(200, json={"ok": True}, headers={"Set-Cookie": "unwanted=true"})
    app = create_public_proxy_app(store.path, transport=httpx.MockTransport(upstream), limits=PublicLimits(upstream_seconds=0.01))
    client = TestClient(app)
    assert client.post("/mobile/v1/privacy/actions", headers=headers, content=b"x" * 65537).status_code == 413
    assert not calls
    assert client.get("/mobile/v1/capabilities", headers=headers).status_code == 502
    assert client.get("/mobile/v1/capabilities", headers=headers).status_code == 503
    response = client.get("/mobile/v1/capabilities", headers={**headers, "Cookie": "secret", "X-Forwarded-Host": "elsewhere"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert "cookie" not in calls[-1].headers and "x-forwarded-host" not in calls[-1].headers


def test_missing_or_locked_auth_store_is_unavailable_not_revoked_and_never_initialized(public, tmp_path):
    import sqlite3
    store, _, headers = public
    missing = tmp_path / "absent.sqlite3"
    assert TestClient(create_public_proxy_app(missing)).get("/mobile/v1/capabilities", headers=headers).status_code == 503
    assert not missing.exists()
    calls = []
    client = TestClient(create_public_proxy_app(store.path, transport=httpx.MockTransport(
        lambda request: calls.append(request) or httpx.Response(200, json={}))))
    db = sqlite3.connect(store.path)
    try:
        db.execute("BEGIN EXCLUSIVE")
        assert client.get("/mobile/v1/capabilities", headers=headers).status_code == 503
        assert not calls
    finally:
        db.rollback()
        db.close()
    assert client.get("/mobile/v1/capabilities", headers=headers).status_code == 200


def test_slow_body_and_concurrency_fail_bounded_then_allow_next_request(public):
    store, _, headers = public
    calls = []
    app = create_public_proxy_app(store.path, transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200, json={})),
                                  limits=PublicLimits(body_seconds=0.03, concurrency=1))
    async def run():
        entered = asyncio.Event()
        async def slow():
            entered.set()
            yield b"{"
            await asyncio.sleep(1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            pending = asyncio.create_task(client.post("/mobile/v1/privacy/actions", headers=headers, content=slow()))
            await entered.wait()
            assert (await client.get("/mobile/v1/capabilities", headers=headers)).status_code == 503
            assert (await pending).status_code == 408
            assert (await client.get("/mobile/v1/capabilities", headers=headers)).status_code == 200
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("limits", [PublicLimits(requests_per_minute=1), PublicLimits(global_per_minute=1)])
def test_rate_limit_expires_without_retaining_credential_material(public, limits, monkeypatch):
    store, _, headers = public
    from riji_agent.mobile import public_proxy
    now = [100.0]
    monkeypatch.setattr(public_proxy.time, "monotonic", lambda: now[0])
    app = create_public_proxy_app(store.path, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})), limits=limits)
    client = TestClient(app)
    assert client.get("/mobile/v1/capabilities", headers=headers).status_code == 200
    assert client.get("/mobile/v1/capabilities", headers=headers).status_code == 429
    now[0] += 61
    assert client.get("/mobile/v1/capabilities", headers=headers).status_code == 200
