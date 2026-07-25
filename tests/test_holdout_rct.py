from __future__ import annotations

import json

from parity.benchmarks import holdout_rct
from parity.middleware import JettisonMiddleware


def test_arm_assignment_deterministic_and_balanced():
    keys = [f"conv-{i}" for i in range(2000)]
    arms = [holdout_rct.assign_arm(k, 0.5) for k in keys]
    assert arms == [holdout_rct.assign_arm(k, 0.5) for k in keys]  # stable
    control = arms.count("control")
    assert 850 < control < 1150  # ~50% within tolerance
    assert holdout_rct.assign_arm("x", 0.0) == "treatment"
    assert holdout_rct.assign_arm("x", 1.0) == "control"


def test_synthetic_run_is_labeled_and_significant(mcp_heavy):
    res = holdout_rct.run_synthetic(mcp_heavy, JettisonMiddleware(config=mcp_heavy))
    assert res.synthetic is True
    assert res.label == holdout_rct.SYNTHETIC_LABEL
    assert "not evidence" in res.label
    assert res.control and res.treatment
    assert res.delta_mean > 0  # treatment observes the smaller standing context
    assert res.significant  # a large standing-context delta dominates the noise
    assert res.ci95_low < res.delta_mean < res.ci95_high


def test_synthetic_deterministic_by_seed(mcp_heavy):
    mw = lambda: JettisonMiddleware(config=mcp_heavy)  # noqa: E731
    a = holdout_rct.run_synthetic(mcp_heavy, mw(), seed=7).to_dict()
    b = holdout_rct.run_synthetic(mcp_heavy, mw(), seed=7).to_dict()
    c = holdout_rct.run_synthetic(mcp_heavy, mw(), seed=8).to_dict()
    assert a == b
    assert a != c


def test_recorded_log_framework(tmp_path):
    log = tmp_path / "requests.jsonl"
    rows = []
    for i in range(200):
        key = f"conv-{i}"
        arm = holdout_rct.assign_arm(key, 0.5)
        tokens = 30000 if arm == "control" else 4000
        rows.append(json.dumps({"conversation_key": key, "tokens": tokens}))
    log.write_text("\n".join(rows) + "\n")

    res = holdout_rct.run_from_log(log)
    assert res.synthetic is False
    assert res.label == "measured (recorded logs)"
    assert res.control.mean_tokens == 30000
    assert res.treatment.mean_tokens == 4000
    assert res.delta_mean == 26000
