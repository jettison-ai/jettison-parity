from __future__ import annotations

from parity.benchmarks import standing_context
from parity.middleware import JettisonMiddleware, NoneMiddleware


def test_mcp_heavy_detailed_breakdown(mcp_heavy):
    res = standing_context.measure(mcp_heavy)
    cats = {r.category for r in res.rows}
    assert cats == {"tools", "skills", "instructions"}
    assert res.total_before > 5000
    assert res.total_after < res.total_before
    assert res.pct_saved > 50  # the registry should cut standing context sharply
    for row in res.rows:
        assert row.tokens_before > 0
        assert row.label in ("measured", "estimated")
    assert res.bundle_hash


def test_openclaw_detailed_breakdown(openclaw_like):
    res = standing_context.measure(openclaw_like)
    assert res.total_before > 20000  # fat system prompt + 8K tokens of schemas
    assert res.total_after < res.total_before
    tools = next(r for r in res.rows if r.category == "tools")
    skills = next(r for r in res.rows if r.category == "skills")
    assert tools.pct_saved > 80
    assert skills.pct_saved > 0


def test_deterministic_across_runs(mcp_heavy):
    a = standing_context.measure(mcp_heavy).to_dict()
    b = standing_context.measure(mcp_heavy).to_dict()
    assert a == b


def test_middleware_blackbox_totals(mcp_heavy):
    jt = standing_context.measure_via_middleware(mcp_heavy, JettisonMiddleware(config=mcp_heavy))
    assert jt.total_after < jt.total_before
    none = standing_context.measure_via_middleware(mcp_heavy, NoneMiddleware())
    assert none.total_after == none.total_before


def test_to_dict_labels(mcp_heavy):
    d = standing_context.measure(mcp_heavy).to_dict()
    assert d["label"] in ("measured", "estimated")
    assert all("pct_saved" in row for row in d["rows"])
