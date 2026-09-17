"""The private ingress cannot forward the mixed admin/transport surface."""

import httpx
import pytest
from fastapi.testclient import TestClient

from riji_agent.mobile.proxy import admitted, create_proxy_app


@pytest.mark.parametrize("path", ["/admin/memory", "/hermes/messages", "/.well-known/fleet", "/healthz",
    "/mobile/v1/../admin/memory", "/mobile/v1/%2e%2e/admin", "/mobile/v1/%63apabilities",
    "//mobile/v1/capabilities", "/mobile/v1/capabilities/", "/mobile/v1/memories/x%2Fy"])
def test_disallowed_paths_never_reach_upstream(path):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={})
    client = TestClient(create_proxy_app(transport=httpx.MockTransport(upstream)))
    assert client.get(path).status_code == 404
    assert not calls


def test_only_allowlisted_methods_queries_and_headers_are_forwarded(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://untrusted.invalid:9999")
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"api_version": 1}, headers={"Set-Cookie": "admin=bad"})
    client = TestClient(create_proxy_app(transport=httpx.MockTransport(upstream)))
    response = client.get("/mobile/v1/capabilities", headers={"Authorization": "Bearer synthetic-token",
                          "Cookie": "review=secret", "X-Forwarded-Host": "evil.invalid"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert str(calls[0].url) == "http://127.0.0.1:8765/mobile/v1/capabilities"
    assert "cookie" not in calls[0].headers and "x-forwarded-host" not in calls[0].headers
    assert client.get("/mobile/v1/capabilities?target=admin").status_code == 404
    assert client.put("/mobile/v1/capabilities").status_code == 404
    assert len(calls) == 1


def test_oversize_requests_and_upstream_redirects_fail_closed():
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://untrusted.invalid"})
    client = TestClient(create_proxy_app(transport=httpx.MockTransport(upstream)))
    assert client.post("/mobile/v1/pair", content=b"x" * 65537).status_code == 413
    assert not calls
    assert client.get("/mobile/v1/capabilities").status_code == 502
    assert len(calls) == 1


def test_raw_admission_rejects_encoded_queries_non_ascii_and_embedded_dot_paths():
    assert admitted("GET", b"/mobile/v1/memories", b"cursor=abc_123")
    for path, query in [(b"/mobile/v1/memories", b"cursor=a%2Fb"),
                        (b"/mobile/v1/memories", b"cursor=a&cursor=b"),
                        (b"/mobile/v1/\xff", b""), (b"/mobile/v1/./capabilities", b"")]:
        assert not admitted("GET", path, query)


def test_oversized_response_is_rejected_without_truncating_json():
    client = TestClient(create_proxy_app(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"text": "x" * (4 * 1024 * 1024)}))))
    response = client.get("/mobile/v1/capabilities")
    assert response.status_code == 503 and response.json()["detail"] == "mobile_response_too_large"
