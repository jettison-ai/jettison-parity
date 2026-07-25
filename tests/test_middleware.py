from __future__ import annotations

import json

import httpx
import pytest

from parity.benchmarks import standing_context
from parity.middleware import (
    HTTPProxyMiddleware,
    JettisonMiddleware,
    Middleware,
    NoneMiddleware,
    get_middleware,
)


def test_none_is_identity(mcp_heavy):
    body = standing_context.canonical_body(mcp_heavy)
    out = NoneMiddleware().optimize_request(body, "anthropic")
    assert out == body


def test_jettison_rewrites_tools_and_system(mcp_heavy):
    body = standing_context.canonical_body(mcp_heavy)
    mw = JettisonMiddleware(config=mcp_heavy)
    out = mw.optimize_request(body, "anthropic")
    names = [t["name"] for t in out["tools"]]
    assert names == ["jettison_search_capabilities", "jettison_load_capabilities"]
    assert "Capability registry" in out["system"]
    assert mw.last_rewrite is not None
    assert mw.last_rewrite.tokens_after < mw.last_rewrite.tokens_before


def test_protocol_conformance(mcp_heavy):
    assert isinstance(NoneMiddleware(), Middleware)
    assert isinstance(JettisonMiddleware(config=mcp_heavy), Middleware)
    assert isinstance(HTTPProxyMiddleware("http://example.invalid/compress"), Middleware)


def test_http_proxy_middleware_with_fake_endpoint():
    # A fake third-party compression endpoint that drops the tools list.
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["provider"] == "anthropic"
        body = payload["body"]
        body["tools"] = []
        return httpx.Response(200, json={"body": body})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    mw = HTTPProxyMiddleware("http://middleware.test/compress", client=client)
    out = mw.optimize_request({"model": "m", "tools": [{"name": "x"}], "messages": []}, "anthropic")
    assert out["tools"] == []


def test_http_proxy_middleware_rejects_bad_payload():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"nope": 1}))
    )
    mw = HTTPProxyMiddleware("http://middleware.test/compress", client=client)
    with pytest.raises(ValueError):
        mw.optimize_request({"model": "m"}, "anthropic")


def test_factory(mcp_heavy):
    assert get_middleware("none").name == "none"
    assert get_middleware("jettison", mcp_heavy).name == "jettison"
    with pytest.raises(ValueError):
        get_middleware("bogus")
