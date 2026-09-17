"""Standard uvicorn h11 protocol checks on an in-memory transport, no listening ports."""

import asyncio

import httpx
from uvicorn import Config
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState

from riji_agent.mobile.public_proxy import Admission, create_public_proxy_app
from test_mobile_managed import public


class Wire(asyncio.Transport):
    def __init__(self):
        self.output = bytearray()
        self.closed = False

    def write(self, data):
        self.output.extend(data)

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed

    def get_extra_info(self, name, default=None):
        return ("127.0.0.1", 0) if name in {"sockname", "peername"} else default

    def pause_reading(self):
        pass

    def resume_reading(self):
        pass


async def exchange(app, payload, concurrency=32):
    config = Config(app, http="h11", access_log=False, proxy_headers=False, log_config=None,
                    limit_concurrency=concurrency, h11_max_incomplete_event_size=16384,
                    headers=[("Cache-Control", "no-store")])
    config.load()
    state = ServerState()
    state.default_headers = config.encoded_headers
    protocol = H11Protocol(config, state, {})
    wire = Wire()
    protocol.connection_made(wire)
    try:
        protocol.data_received(payload)
        if state.tasks:
            await asyncio.wait_for(asyncio.gather(*state.tasks), 1)
        return bytes(wire.output)
    finally:
        protocol.connection_lost(None)


def test_h11_both_complete_and_incomplete_oversize_headers_fail_before_authentication(public, monkeypatch):
    store, _, headers = public
    def forbidden(*args):
        raise AssertionError("Header rejection must precede authentication")
    monkeypatch.setattr(Admission, "authenticate", forbidden)
    app = create_public_proxy_app(store.path)
    prefix = b"GET /mobile/v1/capabilities HTTP/1.1\r\nHost: local\r\nX-Long: " + b"x" * 16385
    complete = asyncio.run(exchange(app, prefix + b"\r\nConnection: close\r\n\r\n"))
    assert complete.startswith(b"HTTP/1.1 431") and b"cache-control: no-store" in complete
    partial = asyncio.run(exchange(app, prefix))
    assert partial.startswith(b"HTTP/1.1 400")


def test_unsupported_protocol_methods_and_routes_use_generic_noncacheable_application_errors(public):
    store, _, _ = public
    app = create_public_proxy_app(store.path)
    for path, method in [("/mobile/v1/capabilities", "TRACE"), ("/", "GET"), ("/admin/memory", "GET")]:
        request = f"{method} {path} HTTP/1.1\r\nHost: local\r\nConnection: close\r\n\r\n".encode()
        response = asyncio.run(exchange(app, request))
        assert response.startswith(b"HTTP/1.1 404")
        assert b"cache-control: no-store" in response and b'"detail":"mobile_route_not_found"' in response


def test_transport_overload_uses_uvicorn_nonjson_503_with_default_no_store_then_recovers(public):
    store, _, headers = public
    app = create_public_proxy_app(store.path, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    request = ("GET /mobile/v1/capabilities HTTP/1.1\r\nHost: local\r\nAuthorization: " +
               headers["Authorization"] + "\r\nConnection: close\r\n\r\n").encode()
    overloaded = asyncio.run(exchange(app, request, concurrency=1))
    assert overloaded.startswith(b"HTTP/1.1 503") and b"cache-control: no-store" in overloaded
    assert b"text/plain" in overloaded
    assert asyncio.run(exchange(app, request)).startswith(b"HTTP/1.1 200")
