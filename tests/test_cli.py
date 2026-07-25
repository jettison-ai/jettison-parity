from __future__ import annotations

import json

from click.testing import CliRunner

from parity.cli import main
from tests.conftest import CONFIGS, TASKS


def test_run_all_json(tmp_path):
    out = tmp_path / "results.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "--family", "all",
            "--middleware", "jettison",
            "--config", str(CONFIGS / "mcp-heavy"),
            "--tasks", str(TASKS),
            "--json",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data) == {"standing_context", "task_parity", "session_cost", "holdout_rct"}
    assert data["task_parity"]["regressions"] == []
    assert data["holdout_rct"]["label"].startswith("synthetic demonstration")
    assert json.loads(out.read_text()) == data


def test_run_single_family_tables():
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["run", "--family", "standing", "--config", str(CONFIGS / "mcp-heavy")],
    )
    assert result.exit_code == 0, result.output
    assert "Standing context" in result.output


def test_run_none_middleware_blackbox():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "run",
            "--family", "standing",
            "--middleware", "none",
            "--config", str(CONFIGS / "mcp-heavy"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    sc = data["standing_context"]
    assert sc["mode"] == "middleware:none"
    assert sc["total_saved"] == 0


def test_stable_json_output_across_runs():
    runner = CliRunner()
    args = [
        "run",
        "--family", "all",
        "--config", str(CONFIGS / "mcp-heavy"),
        "--tasks", str(TASKS),
        "--json",
    ]
    a = runner.invoke(main, args)
    b = runner.invoke(main, args)
    assert a.exit_code == b.exit_code == 0
    assert a.output == b.output
