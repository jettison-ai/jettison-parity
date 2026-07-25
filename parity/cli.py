"""parity CLI: run the Parity Harness benchmark families.

    parity run [--family standing|parity|cost|rct|resident|all]
               [--middleware none|jettison]
               [--config PATH] [--tasks PATH]
               [--json] [--out PATH] [--turns N] [--seed N]
               [--rct-log PATH]

Every rendered number carries a measured/estimated label. Exits nonzero
when the task-parity family detects a regression (completion drop, wrong
tool, or a lost critical fact).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from parity.benchmarks import (
    holdout_rct,
    resident_context,
    session_cost,
    standing_context,
    task_parity,
)
from parity.fixtures import load_config
from parity.middleware import MIDDLEWARE_NAMES, get_middleware

FAMILIES = ("standing", "parity", "cost", "rct", "resident", "all")

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = _REPO_ROOT / "configs" / "mcp-heavy"
DEFAULT_TASKS = _REPO_ROOT / "tasks"


@click.group()
def main() -> None:
    """Parity Harness: benchmark suite for agent token optimizers."""


@main.command("run")
@click.option("--family", type=click.Choice(FAMILIES), default="all", show_default=True)
@click.option(
    "--middleware",
    "middleware_name",
    type=click.Choice(MIDDLEWARE_NAMES),
    default="jettison",
    show_default=True,
    help="Optimizer under test (baseline arm is always 'none').",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=f"Fixture config directory [default: {DEFAULT_CONFIG}]",
)
@click.option(
    "--tasks",
    "tasks_path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=f"Task files directory for the parity family [default: {DEFAULT_TASKS}]",
)
@click.option("--json", "json_out", is_flag=True, help="Print results as JSON instead of tables.")
@click.option("--out", "out_path", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Also write results JSON to this path.")
@click.option("--turns", type=int, default=session_cost.DEFAULT_TURNS, show_default=True, help="Session length for the cost family.")
@click.option("--seed", type=int, default=holdout_rct.DEFAULT_SEED, show_default=True, help="Seed for the synthetic RCT generator.")
@click.option("--holdout", type=float, default=holdout_rct.DEFAULT_HOLDOUT, show_default=True, help="Control-arm fraction for the RCT family.")
@click.option("--rct-log", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None, help="Recorded request log (JSONL) for the RCT family; omit for the synthetic demonstration.")
def run(
    family: str,
    middleware_name: str,
    config_path: Path | None,
    tasks_path: Path | None,
    json_out: bool,
    out_path: Path | None,
    turns: int,
    seed: int,
    holdout: float,
    rct_log: Path | None,
) -> None:
    """Run one benchmark family, or all of them."""
    console = Console(stderr=False)
    config = load_config(config_path or DEFAULT_CONFIG)
    tasks_dir = tasks_path or DEFAULT_TASKS
    configs_root = (config_path or DEFAULT_CONFIG).parent

    results: dict[str, dict] = {}
    exit_code = 0

    if family in ("resident", "all"):
        res = resident_context.run(turns=turns)
        results["resident_context"] = res.to_dict()
        if not json_out:
            resident_context.render(res, console)

    if family in ("standing", "all"):
        if middleware_name == "jettison":
            res = standing_context.measure(config)
        else:
            res = standing_context.measure_via_middleware(config, get_middleware(middleware_name, config))
        results["standing_context"] = res.to_dict()
        if not json_out:
            _render_standing(console, res)

    if family in ("parity", "all"):
        res_p = task_parity.run(
            tasks_dir,
            configs_root,
            middleware_factory=lambda cfg: get_middleware(middleware_name, cfg),
        )
        results["task_parity"] = res_p.to_dict()
        if not json_out:
            _render_parity(console, res_p)
        if res_p.regressions:
            exit_code = 2

    if family in ("cost", "all"):
        res_c = session_cost.simulate(
            config, get_middleware(middleware_name, config), turns=turns
        )
        results["session_cost"] = res_c.to_dict()
        if not json_out:
            _render_cost(console, res_c)

    if family in ("rct", "all"):
        if rct_log is not None:
            res_r = holdout_rct.run_from_log(rct_log, holdout_fraction=holdout)
        else:
            res_r = holdout_rct.run_synthetic(
                config, get_middleware(middleware_name, config), seed=seed, holdout_fraction=holdout
            )
        results["holdout_rct"] = res_r.to_dict()
        if not json_out:
            _render_rct(console, res_r)

    payload = json.dumps(results, indent=2, ensure_ascii=False)
    if json_out:
        click.echo(payload)
    if out_path is not None:
        out_path.write_text(payload + "\n")
        if not json_out:
            console.print(f"[dim]results written to {out_path}[/dim]")

    if exit_code:
        if not json_out:
            console.print("[bold red]task-parity regressions detected[/bold red]")
        sys.exit(exit_code)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _render_standing(console: Console, res: standing_context.StandingContextResult) -> None:
    table = Table(title=f"Standing context per turn — {res.config_name} ({res.mode})")
    table.add_column("category")
    table.add_column("before", justify="right")
    table.add_column("after", justify="right")
    table.add_column("saved", justify="right")
    table.add_column("saved %", justify="right")
    table.add_column("label")
    for r in res.rows:
        table.add_row(
            r.category,
            f"{r.tokens_before:,}",
            f"{r.tokens_after:,}",
            f"{r.tokens_saved:,}",
            f"{r.pct_saved}%",
            r.label,
        )
    table.add_row(
        "[bold]total[/bold]",
        f"[bold]{res.total_before:,}[/bold]",
        f"[bold]{res.total_after:,}[/bold]",
        f"[bold]{res.total_saved:,}[/bold]",
        f"[bold]{res.pct_saved}%[/bold]",
        res.label,
    )
    console.print(table)
    console.print(f"[dim]model={res.model}  bundle_hash={res.bundle_hash or 'n/a'}  tokens are {res.label}[/dim]\n")


def _render_parity(console: Console, res: task_parity.TaskParityResult) -> None:
    table = Table(title=f"Task parity — {res.config_name} (baseline vs {res.middleware})")
    table.add_column("task")
    table.add_column("completed b/o")
    table.add_column("tool b/o")
    table.add_column("facts b/o")
    table.add_column("commitments b/o")
    table.add_column("req tokens b/o", justify="right")
    table.add_column("regressions")
    for o in res.outcomes:
        b, p = o.baseline, o.optimized

        def yn(v: bool) -> str:
            return "yes" if v else "NO"

        table.add_row(
            o.task_id,
            f"{yn(b.completed)}/{yn(p.completed)}",
            f"{yn(b.correct_tool)}/{yn(p.correct_tool)}",
            f"{b.critical_facts_retained}/{b.critical_facts_total} · {p.critical_facts_retained}/{p.critical_facts_total}",
            f"{b.commitments_retained}/{b.commitments_total} · {p.commitments_retained}/{p.commitments_total}",
            f"{b.request_tokens:,} / {p.request_tokens:,}",
            "; ".join(o.regressions) or "-",
        )
    console.print(table)
    base_tok, opt_tok = res.total_request_tokens
    saved = base_tok - opt_tok
    pct = 100.0 * saved / base_tok if base_tok else 0.0
    console.print(
        f"[dim]total request tokens: baseline {base_tok:,} vs optimized {opt_tok:,} "
        f"(saved {saved:,}, {pct:.1f}%) — token counts are estimated[/dim]\n"
    )


def _render_cost(console: Console, res: session_cost.SessionCostResult) -> None:
    table = Table(
        title=(
            f"Session cost — {res.config_name}, {res.turns} turns, {res.model} "
            f"(standing {res.standing_before:,} -> {res.standing_after:,} tokens)"
        )
    )
    table.add_column("regime")
    table.add_column("baseline $", justify="right")
    table.add_column("optimized $", justify="right")
    table.add_column("saved $", justify="right")
    table.add_column("saved %", justify="right")
    table.add_column("label")
    for r in res.regimes:
        table.add_row(
            r.regime,
            f"${r.baseline_usd:.4f}",
            f"${r.optimized_usd:.4f}",
            f"${r.saved_usd:.4f}",
            f"{r.pct_saved}%",
            res.label,
        )
    console.print(table)
    console.print(
        f"[dim]prices are {res.price_label}; token counts are {res.token_label}; "
        f"per-turn conversation increment of {res.per_turn_tokens} tokens is a synthetic estimate[/dim]\n"
    )


def _render_rct(console: Console, res: holdout_rct.RCTResult) -> None:
    table = Table(title=f"Holdout RCT — {res.config_name}")
    table.add_column("arm")
    table.add_column("conversations", justify="right")
    table.add_column("requests", justify="right")
    table.add_column("mean tokens/req", justify="right")
    table.add_column("stdev", justify="right")
    for arm in (res.control, res.treatment):
        if arm:
            table.add_row(
                arm.arm,
                f"{arm.n_conversations:,}",
                f"{arm.n_requests:,}",
                f"{arm.mean_tokens:,.1f}",
                f"{arm.stdev_tokens:,.1f}",
            )
    console.print(table)
    sig = "significant" if res.significant else "NOT significant"
    console.print(
        f"delta (control - treatment): [bold]{res.delta_mean:,.1f}[/bold] tokens/request "
        f"({res.pct_saved:.1f}%), 95% CI [{res.ci95_low:,.1f}, {res.ci95_high:,.1f}] — {sig}"
    )
    style = "yellow" if res.synthetic else "green"
    console.print(f"[{style}]label: {res.label}[/{style}]")
    for w in res.warnings:
        console.print(f"[dim]note: {w}[/dim]")
    console.print()


if __name__ == "__main__":
    main()
