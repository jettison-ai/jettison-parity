"""Resident-context benchmark: what shaping-on-arrival is worth.

A tool result is billed on the turn it lands and again on every later turn
it stays resident, so its cost is ``tokens x remaining_turns``. This family
simulates a session of N turns in which large tool results arrive at known
points, and prices the baseline against Jettison's Horizon Manager — plus
the history-eviction alternative, so the reason eviction is not implemented
stays visible and re-checkable rather than being folklore.

Fully deterministic: no clocks, no randomness, no model calls.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from jettison.horizon.economics import (
    PLACEHOLDER_TOKENS,
    evaluate_shape,
    eviction_break_even_turns,
)
from jettison.pricing import get_price

# Where large results land in the session and how big they are. Modelled on
# the observed shape of real coding sessions: a burst of file reads early
# while the agent orients, then sporadic larger ones as work proceeds.
DEFAULT_ARRIVALS: list[tuple[int, int]] = [
    (2, 12_000), (3, 8_000), (5, 20_000), (8, 6_000), (12, 15_000),
    (18, 9_000), (25, 30_000), (31, 7_000), (40, 11_000), (46, 5_000),
]
EVICT_AFTER_TURNS = 12


@dataclass
class ResidentContextResult:
    family: str = "resident_context"
    model: str = "claude-sonnet-4-5"
    turns: int = 50
    arrivals: int = 0
    baseline_resident_token_turns: int = 0
    shaped_token_turns_saved: int = 0
    evicted_token_turns_saved: int = 0
    baseline_usd: float = 0.0
    shaped_usd: float = 0.0
    evicted_usd: float = 0.0
    shaped_pct: float = 0.0
    evicted_pct: float = 0.0
    eviction_break_even_turns: float = 0.0
    price_label: str = "estimated"
    token_label: str = "estimated"
    rows: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run(
    turns: int = 50,
    model: str = "claude-sonnet-4-5",
    arrivals: list[tuple[int, int]] | None = None,
) -> ResidentContextResult:
    arrivals = arrivals or DEFAULT_ARRIVALS
    price = get_price(model)
    read_rate = price.cache_read_per_m / 1e6
    write_rate = price.cache_write_per_m / 1e6

    res = ResidentContextResult(
        model=model,
        turns=turns,
        arrivals=len(arrivals),
        eviction_break_even_turns=round(eviction_break_even_turns(model), 2),
        price_label=price.label,
    )

    resident_after_arrival = 0
    for turn, tokens in arrivals:
        remaining = max(0, turns - turn - 1)
        baseline_tt = tokens * remaining
        res.baseline_resident_token_turns += baseline_tt

        decision = evaluate_shape(tokens, remaining, model)
        shaped_tt = decision.tokens_freed * remaining if decision.should_shape else 0
        res.shaped_token_turns_saved += shaped_tt

        # Eviction: drop it after EVICT_AFTER_TURNS, paying one cache-write
        # for the suffix that must be rebuilt. Approximate the suffix with
        # the content resident at that moment.
        evict_turn = turn + EVICT_AFTER_TURNS
        evicted_tt = 0
        if evict_turn < turns - 1:
            evict_remaining = turns - evict_turn - 1
            gain = (tokens - PLACEHOLDER_TOKENS) * evict_remaining * read_rate
            cost = max(resident_after_arrival, tokens) * write_rate
            if gain > cost:
                evicted_tt = int((gain - cost) / read_rate)
        res.evicted_token_turns_saved += evicted_tt
        resident_after_arrival += tokens

        res.rows.append(
            {
                "arrival_turn": turn,
                "tokens": tokens,
                "remaining_turns": remaining,
                "shaped": decision.should_shape,
                "reason": decision.reason,
                "token_turns_baseline": baseline_tt,
                "token_turns_saved_shaping": shaped_tt,
                "token_turns_saved_eviction": evicted_tt,
            }
        )

    res.baseline_usd = round(res.baseline_resident_token_turns * read_rate, 4)
    res.shaped_usd = round(res.shaped_token_turns_saved * read_rate, 4)
    res.evicted_usd = round(res.evicted_token_turns_saved * read_rate, 4)
    if res.baseline_usd:
        res.shaped_pct = round(100 * res.shaped_usd / res.baseline_usd, 1)
        res.evicted_pct = round(100 * res.evicted_usd / res.baseline_usd, 1)
    return res


def render(res: ResidentContextResult, console) -> None:
    from rich.table import Table

    t = Table(title=f"Resident context — {res.turns} turns, {res.arrivals} large results")
    t.add_column("strategy")
    t.add_column("token-turns saved", justify="right")
    t.add_column("$ saved", justify="right")
    t.add_column("% of resident cost", justify="right")
    t.add_row("shape on arrival (shipped)", f"{res.shaped_token_turns_saved:,}",
              f"${res.shaped_usd:,.4f}", f"{res.shaped_pct}%")
    t.add_row("evict from history (not built)", f"{res.evicted_token_turns_saved:,}",
              f"${res.evicted_usd:,.4f}", f"{res.evicted_pct}%")
    console.print(t)
    console.print(
        f"baseline resident cost ${res.baseline_usd:,.4f} "
        f"({res.baseline_resident_token_turns:,} token-turns) — "
        f"prices are {res.price_label}, token counts are {res.token_label}"
    )
    console.print(
        f"[dim]an eviction must displace {res.eviction_break_even_turns} turns of "
        f"cache-reads to pay for its cache-write — that is why history is never "
        f"mutated[/dim]"
    )
