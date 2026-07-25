"""Middleware abstraction: `parity run` scores any request optimizer.

The minimum contract is one method::

    optimize_request(body, provider) -> body

where ``body`` is a provider-format request dict (Anthropic Messages or
OpenAI Chat Completions). Optimizers that resolve their own meta-tools
mid-turn (like jettison's capability registry) additionally implement
``run_interception`` and ``patch_incoming``; the base class provides
no-op defaults so simple optimizers ignore them.

Built-in adapters:

- ``none``      identity baseline
- ``jettison``  jettison's rewrite pipeline + interception loop
- ``HTTPProxyMiddleware``  POSTs the body to a remote compression
  endpoint — point the harness at any third-party middleware that
  speaks {"provider": ..., "body": ...} -> {"body": ...}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from jettison.proxy.interceptor import InterceptionLoop, SessionState
from jettison.proxy.rewrite import RewriteResult, rewrite_request, session_key
from jettison.registry import CapabilityStore

from parity.fixtures import FixtureConfig, build_store


@dataclass
class InterceptionInfo:
    """What happened inside one middleware-driven model turn."""

    response: dict[str, Any]
    rounds: int = 0
    meta_calls_resolved: int = 0
    mixed_turn: bool = False


@runtime_checkable
class Middleware(Protocol):
    name: str

    def optimize_request(self, body: dict[str, Any], provider: str) -> dict[str, Any]: ...


class BaseMiddleware:
    """Default no-op hooks so optimize_request is the only required piece."""

    name = "base"

    def optimize_request(self, body: dict[str, Any], provider: str) -> dict[str, Any]:
        return body

    def patch_incoming(self, body: dict[str, Any], provider: str) -> int:
        """Fix up client-fabricated tool_results from a prior mixed turn.
        Returns the number of patches applied."""
        return 0

    async def run_interception(
        self,
        response: dict[str, Any],
        body: dict[str, Any],
        api_call_fn: Any,
        provider: str,
    ) -> InterceptionInfo:
        """Resolve middleware-owned tool calls in ``response``, calling
        ``api_call_fn(messages, tools)`` for continuations. Default: none."""
        return InterceptionInfo(response=response)


class NoneMiddleware(BaseMiddleware):
    """Identity baseline: requests pass through untouched."""

    name = "none"


class JettisonMiddleware(BaseMiddleware):
    """jettison's rewrite pipeline: capability registry + compiled
    instructions + meta-tool interception loop. Per-conversation session
    state (loaded capabilities, pending mixed-turn results) is keyed by
    jettison's session_key."""

    name = "jettison"

    def __init__(self, config: FixtureConfig | None = None, store: CapabilityStore | None = None):
        if store is None:
            if config is None:
                raise ValueError("JettisonMiddleware needs a FixtureConfig or a CapabilityStore")
            store = build_store(config)
        self.store = store
        self.loop = InterceptionLoop(store)
        self.sessions: dict[str, SessionState] = {}
        self.last_rewrite: RewriteResult | None = None

    def _session(self, body: dict[str, Any], provider: str) -> SessionState:
        key = session_key(body, provider)
        return self.sessions.setdefault(key, SessionState())

    def optimize_request(self, body: dict[str, Any], provider: str) -> dict[str, Any]:
        session = self._session(body, provider)
        result = rewrite_request(body, provider, self.store, session)
        self.last_rewrite = result
        return result.body

    def patch_incoming(self, body: dict[str, Any], provider: str) -> int:
        session = self._session(body, provider)
        return self.loop.patch_incoming_messages(body.get("messages") or [], session, provider)

    async def run_interception(
        self,
        response: dict[str, Any],
        body: dict[str, Any],
        api_call_fn: Any,
        provider: str,
    ) -> InterceptionInfo:
        session = self._session(body, provider)
        outcome = await self.loop.run(
            response,
            body.get("messages") or [],
            body.get("tools") or [],
            api_call_fn,
            provider,
            session,
        )
        return InterceptionInfo(
            response=outcome.response,
            rounds=outcome.rounds,
            meta_calls_resolved=outcome.meta_calls_resolved,
            mixed_turn=outcome.mixed_turn,
        )


class HTTPProxyMiddleware(BaseMiddleware):
    """Adapter for third-party middleware exposed as an HTTP endpoint.

    POSTs ``{"provider": provider, "body": body}`` to ``endpoint`` and
    expects ``{"body": optimized_body}`` back. Inject a custom
    ``httpx.Client`` (e.g. built on ``httpx.MockTransport``) for tests;
    the harness itself never touches the network in CI.
    """

    name = "http-proxy"

    def __init__(self, endpoint: str, client: httpx.Client | None = None, timeout: float = 30.0):
        self.endpoint = endpoint
        self._client = client or httpx.Client(timeout=timeout)

    def optimize_request(self, body: dict[str, Any], provider: str) -> dict[str, Any]:
        resp = self._client.post(self.endpoint, json={"provider": provider, "body": body})
        resp.raise_for_status()
        data = resp.json()
        out = data.get("body")
        if not isinstance(out, dict):
            raise ValueError(f"middleware endpoint {self.endpoint} returned no 'body' object")
        return out


MIDDLEWARE_NAMES = ("none", "jettison")


def get_middleware(name: str, config: FixtureConfig | None = None) -> BaseMiddleware:
    if name == "none":
        return NoneMiddleware()
    if name == "jettison":
        return JettisonMiddleware(config=config)
    raise ValueError(f"unknown middleware {name!r}; built-ins: {MIDDLEWARE_NAMES}")
