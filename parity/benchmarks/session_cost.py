"""Family C: cache-aware dollar cost of an N-turn session.

Simulates an N-turn agent session (default 50) over a fixture config.
Every turn re-sends the standing context (system + tools) plus a growing
conversation history. Costs are computed at provider prices with the
three input tiers priced separately (``jettison.pricing.get_price``):

- cache-HIT regime: the standing prefix is written to the provider cache
  on turn 1 (cache_write) and read from it on turns 2..N (cache_read);
  conversation history bills as fresh input.
- cache-MISS regime: the standing prefix bills as fresh input every turn
  (what you pay when prefix caching is broken or unavailable).

Reported dollar figures inherit the price-table label ("measured" when
provider-derived via Headroom's pricing map, else "estimated") combined
with the token-count label. The per-turn conversation increment is a
fixed synthetic constant and is always labeled "estimated".

Deterministic: same config + model + turns -> same dollars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jettison.pricing import cache_aware_savings, get_price
from jettison.tokens import DEFAULT_MODEL

from parity.benchmarks import standing_context
from parity.fixtures import FixtureConfig
from parity.middleware import BaseMiddleware, JettisonMiddleware

DEFAULT_TURNS = 50
PER_TURN_TOKENS = 180  # synthetic per-turn user+assistant increment (estimated)


@dataclass
class RegimeCost:
    regime: str  # "cache-hit" | "cache-miss"
    baseline_usd: float
    optimized_usd: float
    avoided_usd: float  # via jettison.pricing.cache_aware_savings

    @property
    def saved_usd(self) -> float:
        return round(self.baseline_usd - self.optimized_usd, 6)

    @property
    def pct_saved(self) -> float:
        if self.baseline_usd <= 0:
            return 0.0
        return round(100.0 * self.saved_usd / self.baseline_usd, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "baseline_usd": round(self.baseline_usd, 6),
            "optimized_usd": round(self.optimized_usd, 6),
            "saved_usd": self.saved_usd,
            "avoided_usd": round(self.avoided_usd, 6),
            "pct_saved": self.pct_saved,
        }


@dataclass
class SessionCostResult:
    config_name: str
    model: str
    middleware: str
    turns: int
    per_turn_tokens: int
    standing_before: int
    standing_after: int
    token_label: str
    price_label: str
    regimes: list[RegimeCost] = field(default_factory=list)

    @property
    def label(self) -> str:
        return (
            "measured"
            if self.token_label == "measured" and self.price_label == "measured"
            else "estimated"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": "session_cost",
            "config": self.config_name,
            "model": self.model,
            "middleware": self.middleware,
            "turns": self.turns,
            "per_turn_tokens": self.per_turn_tokens,
            "per_turn_tokens_label": "estimated",
            "standing_before": self.standing_before,
            "standing_after": self.standing_after,
            "token_label": self.token_label,
            "price_label": self.price_label,
            "label": self.label,
            "regimes": [r.to_dict() for r in self.regimes],
        }


def _session_cost_usd(
    standing: int,
    turns: int,
    per_turn: int,
    price: Any,
    cached: bool,
) -> float:
    """Total input-side USD for one arm of one regime."""
    total = 0.0
    for t in range(1, turns + 1):
        history = per_turn * (t - 1)
        fresh_input = history + per_turn  # conversation is never prefix-cached here
        if cached:
            standing_cost = standing * (price.cache_write_per_m if t == 1 else price.cache_read_per_m)
        else:
            standing_cost = standing * price.input_per_m
        total += (standing_cost + fresh_input * price.input_per_m) / 1e6
    return total


def simulate(
    config: FixtureConfig,
    middleware: BaseMiddleware | None = None,
    model: str = DEFAULT_MODEL,
    turns: int = DEFAULT_TURNS,
    per_turn_tokens: int = PER_TURN_TOKENS,
) -> SessionCostResult:
    middleware = middleware if middleware is not None else JettisonMiddleware(config=config)
    sc = standing_context.measure_via_middleware(config, middleware, model)
    standing_before = sc.total_before
    standing_after = sc.total_after
    price = get_price(model)

    avoided_per_turn = max(0, standing_before - standing_after)
    regimes = []
    for regime, cached in (("cache-hit", True), ("cache-miss", False)):
        baseline = _session_cost_usd(standing_before, turns, per_turn_tokens, price, cached)
        optimized = _session_cost_usd(standing_after, turns, per_turn_tokens, price, cached)
        if cached:
            avoided = cache_aware_savings(
                model,
                cache_write_tokens_avoided=avoided_per_turn,  # turn 1
                cache_read_tokens_avoided=avoided_per_turn * (turns - 1),  # turns 2..N
            )
        else:
            avoided = cache_aware_savings(model, input_tokens_avoided=avoided_per_turn * turns)
        regimes.append(
            RegimeCost(
                regime=regime,
                baseline_usd=baseline,
                optimized_usd=optimized,
                avoided_usd=avoided.dollars,
            )
        )

    return SessionCostResult(
        config_name=config.name,
        model=model,
        middleware=middleware.name,
        turns=turns,
        per_turn_tokens=per_turn_tokens,
        standing_before=standing_before,
        standing_after=standing_after,
        token_label=sc.label,
        price_label=price.label,
        regimes=regimes,
    )
