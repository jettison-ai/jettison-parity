from __future__ import annotations

from parity.benchmarks import session_cost
from parity.middleware import JettisonMiddleware, NoneMiddleware


def test_optimized_cheaper_in_both_regimes(mcp_heavy):
    res = session_cost.simulate(mcp_heavy, JettisonMiddleware(config=mcp_heavy))
    assert res.turns == 50
    assert {r.regime for r in res.regimes} == {"cache-hit", "cache-miss"}
    for regime in res.regimes:
        assert regime.baseline_usd > regime.optimized_usd > 0
        assert regime.saved_usd > 0
        assert regime.avoided_usd > 0


def test_cache_hit_savings_smaller_than_cache_miss(mcp_heavy):
    # Cached standing context bills ~10x cheaper, so the dollar delta in the
    # cache-hit regime must be far smaller than in the miss regime. An
    # optimizer that ignores this overstates its savings.
    res = session_cost.simulate(mcp_heavy, JettisonMiddleware(config=mcp_heavy))
    hit = next(r for r in res.regimes if r.regime == "cache-hit")
    miss = next(r for r in res.regimes if r.regime == "cache-miss")
    assert hit.saved_usd < miss.saved_usd


def test_identity_middleware_saves_nothing(mcp_heavy):
    res = session_cost.simulate(mcp_heavy, NoneMiddleware())
    for regime in res.regimes:
        assert regime.saved_usd == 0
        assert regime.avoided_usd == 0


def test_deterministic_and_labeled(mcp_heavy):
    a = session_cost.simulate(mcp_heavy, JettisonMiddleware(config=mcp_heavy)).to_dict()
    b = session_cost.simulate(mcp_heavy, JettisonMiddleware(config=mcp_heavy)).to_dict()
    assert a == b
    assert a["label"] in ("measured", "estimated")
    assert a["per_turn_tokens_label"] == "estimated"
    assert a["price_label"] in ("measured", "estimated")
