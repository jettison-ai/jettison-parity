from __future__ import annotations

import asyncio

from parity.benchmarks import task_parity
from parity.middleware import JettisonMiddleware, NoneMiddleware
from tests.conftest import CONFIGS, TASKS


def test_all_tasks_no_regressions():
    res = task_parity.run(TASKS, CONFIGS, middleware_factory=lambda cfg: JettisonMiddleware(config=cfg))
    assert len(res.outcomes) >= 6
    assert res.regressions == [], f"unexpected regressions: {res.regressions}"
    for o in res.outcomes:
        assert o.baseline.completed and o.optimized.completed
        assert o.baseline.correct_tool and o.optimized.correct_tool
        assert o.baseline.correct_params and o.optimized.correct_params
        assert o.optimized.critical_facts_retained == o.optimized.critical_facts_total
        assert o.optimized.commitments_retained >= o.baseline.commitments_retained


def test_optimized_arm_uses_fewer_request_tokens():
    res = task_parity.run(TASKS, CONFIGS, middleware_factory=lambda cfg: JettisonMiddleware(config=cfg))
    base, opt = res.total_request_tokens
    assert 0 < opt < base


def test_registry_flow_exercised(mcp_heavy):
    tasks = {t["id"]: t for t in task_parity.load_tasks(TASKS)}

    # search->load->call: two meta rounds resolve inside one client request
    chain = tasks["search-load-call"]
    arm = asyncio.run(
        task_parity.run_arm(chain, mcp_heavy, JettisonMiddleware(config=mcp_heavy), "optimized")
    )
    assert arm.meta_rounds >= 2
    assert arm.completed and arm.correct_tool

    # mixed turn: meta call alongside a client-owned call, patched next request
    mixed = tasks["mixed-turn"]
    arm = asyncio.run(
        task_parity.run_arm(mixed, mcp_heavy, JettisonMiddleware(config=mcp_heavy), "optimized")
    )
    assert arm.mixed_turns == 1
    assert arm.patched_results == 1
    assert arm.completed and arm.correct_tool


def test_baseline_arm_never_touches_meta_tools(mcp_heavy):
    tasks = task_parity.load_tasks(TASKS)
    for task in tasks:
        arm = asyncio.run(task_parity.run_arm(task, mcp_heavy, NoneMiddleware(), "baseline"))
        assert arm.meta_rounds == 0
        assert arm.mixed_turns == 0


def test_deterministic_across_runs():
    factory = lambda cfg: JettisonMiddleware(config=cfg)  # noqa: E731
    a = task_parity.run(TASKS, CONFIGS, middleware_factory=factory).to_dict()
    b = task_parity.run(TASKS, CONFIGS, middleware_factory=factory).to_dict()
    assert a == b


def test_regression_detection_flags_lost_facts():
    baseline = task_parity.ArmResult(arm="baseline", middleware="none", completed=True, correct_tool=True, correct_params=True, critical_facts_total=2, critical_facts_retained=2)
    optimized = task_parity.ArmResult(arm="optimized", middleware="x", completed=True, correct_tool=True, correct_params=True, critical_facts_total=2, critical_facts_retained=1)
    regs = task_parity.compare_arms({}, baseline, optimized)
    assert any("critical fact" in r for r in regs)

    optimized_bad = task_parity.ArmResult(arm="optimized", middleware="x", completed=False, critical_facts_total=0, critical_facts_retained=0)
    regs = task_parity.compare_arms({}, baseline, optimized_bad)
    assert any("completion" in r for r in regs)
