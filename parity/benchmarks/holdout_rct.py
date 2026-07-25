"""Family D: holdout RCT framework over recorded request logs.

The gold-standard measurement for a token optimizer is a randomized
holdout in production: a deterministic fraction of conversations bypass
the optimizer (control) while the rest go through it (treatment), and
the observed per-request input tokens are compared across arms.

Arm assignment is deterministic and conversation-stable, exactly like
Headroom's output_savings_policy::

    frac = int(sha256("arm:" + conversation_key).hexdigest()[:8], 16) / 0xFFFFFFFF
    arm  = "control" if frac < holdout_fraction else "treatment"

Analysis: per-arm mean tokens/request, delta, and a 95% normal-approx
confidence interval on the difference of means.

Two data sources:

- ``load_records(path)``: recorded logs, one JSON object per line with
  ``conversation_key`` and observed ``tokens`` (and optional ``arm``;
  when absent the arm is re-derived from the key). Results from real
  logs are labeled "measured (recorded logs)".
- ``generate_synthetic_records(...)``: a seeded generator so the family
  runs out of the box. Its results are ALWAYS labeled
  "synthetic demonstration — not evidence".
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jettison.tokens import DEFAULT_MODEL

from parity.benchmarks import standing_context
from parity.fixtures import FixtureConfig
from parity.middleware import BaseMiddleware, JettisonMiddleware

SYNTHETIC_LABEL = "synthetic demonstration — not evidence"
DEFAULT_SEED = 20260724
DEFAULT_HOLDOUT = 0.5


def assign_arm(conversation_key: str, holdout_fraction: float = DEFAULT_HOLDOUT) -> str:
    """Deterministic conversation->arm assignment (Headroom-style)."""
    if holdout_fraction <= 0.0:
        return "treatment"
    if holdout_fraction >= 1.0:
        return "control"
    digest = hashlib.sha256(("arm:" + conversation_key).encode()).hexdigest()
    frac = int(digest[:8], 16) / 0xFFFFFFFF
    return "control" if frac < holdout_fraction else "treatment"


@dataclass(frozen=True)
class LogRecord:
    conversation_key: str
    arm: str
    tokens: int  # observed input tokens for one request


def load_records(path: str | Path, holdout_fraction: float = DEFAULT_HOLDOUT) -> list[LogRecord]:
    """Read recorded request logs (JSONL: conversation_key, tokens[, arm])."""
    records = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        key = str(obj["conversation_key"])
        arm = obj.get("arm") or assign_arm(key, holdout_fraction)
        records.append(LogRecord(conversation_key=key, arm=arm, tokens=int(obj["tokens"])))
    return records


def generate_synthetic_records(
    standing_before: int,
    standing_after: int,
    seed: int = DEFAULT_SEED,
    conversations: int = 400,
    holdout_fraction: float = DEFAULT_HOLDOUT,
) -> list[LogRecord]:
    """Seeded synthetic request log: control conversations observe the
    baseline standing context, treatment conversations the optimized one,
    plus per-turn conversation noise drawn from a seeded RNG."""
    rng = random.Random(seed)
    records = []
    for i in range(conversations):
        key = f"conv-{seed}-{i:05d}"
        arm = assign_arm(key, holdout_fraction)
        standing = standing_before if arm == "control" else standing_after
        turns = rng.randint(2, 10)
        history = 0
        for _turn in range(turns):
            turn_tokens = rng.randint(120, 600)
            records.append(
                LogRecord(conversation_key=key, arm=arm, tokens=standing + history + turn_tokens)
            )
            history += turn_tokens + rng.randint(80, 240)  # assistant reply joins history
    return records


@dataclass
class ArmStats:
    arm: str
    n_requests: int
    n_conversations: int
    mean_tokens: float
    stdev_tokens: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "n_requests": self.n_requests,
            "n_conversations": self.n_conversations,
            "mean_tokens_per_request": round(self.mean_tokens, 1),
            "stdev_tokens": round(self.stdev_tokens, 1),
        }


@dataclass
class RCTResult:
    config_name: str
    model: str
    middleware: str
    seed: int | None
    holdout_fraction: float
    synthetic: bool
    control: ArmStats | None = None
    treatment: ArmStats | None = None
    delta_mean: float = 0.0  # control - treatment (tokens/request saved)
    ci95_low: float = 0.0
    ci95_high: float = 0.0
    pct_saved: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return SYNTHETIC_LABEL if self.synthetic else "measured (recorded logs)"

    @property
    def significant(self) -> bool:
        return self.ci95_low > 0 or self.ci95_high < 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": "holdout_rct",
            "config": self.config_name,
            "model": self.model,
            "middleware": self.middleware,
            "seed": self.seed,
            "holdout_fraction": self.holdout_fraction,
            "synthetic": self.synthetic,
            "label": self.label,
            "control": self.control.to_dict() if self.control else None,
            "treatment": self.treatment.to_dict() if self.treatment else None,
            "delta_mean_tokens_per_request": round(self.delta_mean, 1),
            "ci95": [round(self.ci95_low, 1), round(self.ci95_high, 1)],
            "significant_at_95": self.significant,
            "pct_saved": round(self.pct_saved, 1),
            "warnings": self.warnings,
        }


def _arm_stats(records: list[LogRecord], arm: str) -> ArmStats | None:
    xs = [r.tokens for r in records if r.arm == arm]
    if not xs:
        return None
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return ArmStats(
        arm=arm,
        n_requests=n,
        n_conversations=len({r.conversation_key for r in records if r.arm == arm}),
        mean_tokens=mean,
        stdev_tokens=math.sqrt(var),
    )


def analyze(
    records: list[LogRecord],
    config_name: str,
    model: str,
    middleware_name: str,
    seed: int | None,
    holdout_fraction: float,
    synthetic: bool,
) -> RCTResult:
    result = RCTResult(
        config_name=config_name,
        model=model,
        middleware=middleware_name,
        seed=seed,
        holdout_fraction=holdout_fraction,
        synthetic=synthetic,
    )
    result.control = _arm_stats(records, "control")
    result.treatment = _arm_stats(records, "treatment")
    if not result.control or not result.treatment:
        result.warnings.append("one arm is empty; no comparison possible")
        return result

    c, t = result.control, result.treatment
    result.delta_mean = c.mean_tokens - t.mean_tokens
    se = math.sqrt(
        (c.stdev_tokens**2) / c.n_requests + (t.stdev_tokens**2) / t.n_requests
    )
    result.ci95_low = result.delta_mean - 1.96 * se
    result.ci95_high = result.delta_mean + 1.96 * se
    if c.mean_tokens > 0:
        result.pct_saved = 100.0 * result.delta_mean / c.mean_tokens
    # Note: requests within a conversation are correlated; the naive SE
    # understates uncertainty. Flag it honestly.
    result.warnings.append(
        "CI treats requests as independent; conversation-level clustering "
        "makes the true interval wider"
    )
    return result


def run_synthetic(
    config: FixtureConfig,
    middleware: BaseMiddleware | None = None,
    model: str = DEFAULT_MODEL,
    seed: int = DEFAULT_SEED,
    conversations: int = 400,
    holdout_fraction: float = DEFAULT_HOLDOUT,
) -> RCTResult:
    """Out-of-the-box demonstration run on synthetic logs derived from a
    fixture config's measured standing-context numbers."""
    middleware = middleware if middleware is not None else JettisonMiddleware(config=config)
    sc = standing_context.measure_via_middleware(config, middleware, model)
    records = generate_synthetic_records(
        standing_before=sc.total_before,
        standing_after=sc.total_after,
        seed=seed,
        conversations=conversations,
        holdout_fraction=holdout_fraction,
    )
    return analyze(
        records,
        config_name=config.name,
        model=model,
        middleware_name=middleware.name,
        seed=seed,
        holdout_fraction=holdout_fraction,
        synthetic=True,
    )


def run_from_log(
    log_path: str | Path,
    model: str = DEFAULT_MODEL,
    holdout_fraction: float = DEFAULT_HOLDOUT,
) -> RCTResult:
    """Analyze a recorded request log (the framework path)."""
    records = load_records(log_path, holdout_fraction)
    return analyze(
        records,
        config_name=str(log_path),
        model=model,
        middleware_name="(as recorded)",
        seed=None,
        holdout_fraction=holdout_fraction,
        synthetic=False,
    )
