"""Family A: standing-context tokens per turn, before vs after.

Fully deterministic and reproducible: given a fixture config directory,
measure the standing context a client ships on EVERY request (tool
schemas, skill bodies, instruction files) and what jettison's
compiler + registry replace it with (meta-tools + capability index +
compiled instructions).

Two measurement modes:

- ``measure(config)``: detailed per-category breakdown using jettison's
  compiler/registry directly (tools / skills / instructions rows).
- ``measure_via_middleware(config, middleware)``: black-box totals for
  ANY middleware — builds a canonical request body, counts the standing
  parts before and after ``optimize_request``.

Token labels propagate from ``jettison.tokens`` ("measured" only when the
tokenizer is exact for the model; otherwise "estimated").
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from jettison.compiler import compile_instructions, summarize_skill
from jettison.registry import anthropic_tool_defs, render_capability_index
from jettison.tokens import DEFAULT_MODEL, count_json, count_text

from parity.fixtures import FixtureConfig, build_store
from parity.middleware import BaseMiddleware


@dataclass
class CategoryRow:
    category: str  # tools | skills | instructions
    tokens_before: int
    tokens_after: int
    label: str  # measured | estimated

    @property
    def tokens_saved(self) -> int:
        return self.tokens_before - self.tokens_after

    @property
    def pct_saved(self) -> float:
        if self.tokens_before <= 0:
            return 0.0
        return round(100.0 * self.tokens_saved / self.tokens_before, 1)


@dataclass
class StandingContextResult:
    config_name: str
    model: str
    mode: str  # "jettison-detailed" | f"middleware:{name}"
    rows: list[CategoryRow] = field(default_factory=list)
    bundle_hash: str = ""

    @property
    def total_before(self) -> int:
        return sum(r.tokens_before for r in self.rows)

    @property
    def total_after(self) -> int:
        return sum(r.tokens_after for r in self.rows)

    @property
    def total_saved(self) -> int:
        return self.total_before - self.total_after

    @property
    def pct_saved(self) -> float:
        if self.total_before <= 0:
            return 0.0
        return round(100.0 * self.total_saved / self.total_before, 1)

    @property
    def label(self) -> str:
        labels = {r.label for r in self.rows}
        return "measured" if labels == {"measured"} else "estimated"

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": "standing_context",
            "config": self.config_name,
            "model": self.model,
            "mode": self.mode,
            "bundle_hash": self.bundle_hash,
            "rows": [
                {
                    **asdict(r),
                    "tokens_saved": r.tokens_saved,
                    "pct_saved": r.pct_saved,
                }
                for r in self.rows
            ],
            "total_before": self.total_before,
            "total_after": self.total_after,
            "total_saved": self.total_saved,
            "pct_saved": self.pct_saved,
            "label": self.label,
        }


def measure(config: FixtureConfig, model: str = DEFAULT_MODEL) -> StandingContextResult:
    """Per-category before/after using jettison's compiler + registry."""
    store = build_store(config)
    bundle = store.bundle

    # ---- before: what the client ships on every request ----
    tools_before = count_json(config.tools, model)
    instr_before = count_text(config.instructions_text() or "", model) if config.instruction_files else None
    skills_before = count_text(config.skills_text() or "", model) if config.skill_files else None

    # ---- after: meta-tools + capability index + compiled instructions ----
    # The index header + tool lines are attributed to the tools category
    # (the header exists to explain the tool registry); skill index lines
    # to skills; compiled instruction text to instructions.
    # Rebuild the two index sections separately for attribution. The full
    # index in the bundle is the concatenation of these sections.
    skills = [summarize_skill(name, text) for name, text in config.skill_files]
    index_pairs = _index_pairs(bundle)
    tools_index_text = render_capability_index(index_pairs, [])
    skills_lines = _skills_section(bundle, skills)

    meta_tokens = count_json(anthropic_tool_defs(), model)
    tools_after = meta_tokens.tokens + count_text(tools_index_text, model).tokens

    rows = [
        CategoryRow(
            category="tools",
            tokens_before=tools_before.tokens,
            tokens_after=tools_after,
            label=tools_before.label,
        )
    ]
    if skills_before is not None:
        skills_after = count_text(skills_lines, model).tokens if skills_lines else 0
        rows.append(
            CategoryRow(
                category="skills",
                tokens_before=skills_before.tokens,
                tokens_after=skills_after,
                label=skills_before.label,
            )
        )
    if instr_before is not None:
        compiled = compile_instructions(config.instruction_files)
        instr_after = count_text(compiled.text, model) if compiled.text else None
        rows.append(
            CategoryRow(
                category="instructions",
                tokens_before=instr_before.tokens,
                tokens_after=instr_after.tokens if instr_after else 0,
                label=instr_before.label,
            )
        )

    return StandingContextResult(
        config_name=config.name,
        model=model,
        mode="jettison-detailed",
        rows=rows,
        bundle_hash=bundle.content_hash,
    )


def _index_pairs(bundle: Any) -> list[tuple[str, str]]:
    """(name, one-line summary) pairs as they appear in the bundle index."""
    pairs: list[tuple[str, str]] = []
    for line in bundle.capability_index_text.splitlines():
        if line.startswith("- ") and not line.startswith("- skill"):
            body = line[2:]
            name, _, summary = body.partition(": ")
            if name in bundle.schema_store:
                pairs.append((name, summary))
    return pairs


def _skills_section(bundle: Any, skills: list[Any]) -> str:
    if not skills:
        return ""
    lines = ["Skills (load as skill:<name>):"]
    for s in sorted(skills, key=lambda x: x.name):
        entry = f"- {s.name}"
        if s.description:
            entry += f": {s.description}"
        lines.append(entry)
    return "\n".join(lines)


def canonical_body(config: FixtureConfig, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    """The canonical unoptimized request a fixture client would send."""
    return {
        "model": model,
        "max_tokens": 1024,
        "system": config.baseline_system_text(),
        "messages": [{"role": "user", "content": "Please help me with my task."}],
        "tools": [json.loads(json.dumps(t)) for t in config.tools],
    }


def standing_tokens(body: dict[str, Any], model: str = DEFAULT_MODEL) -> int:
    """Tokens of the standing parts of a request body (system + tools)."""
    total = 0
    system = body.get("system")
    if isinstance(system, str) and system:
        total += count_text(system, model).tokens
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                total += count_text(str(block.get("text", "")), model).tokens
    tools = body.get("tools") or []
    if tools:
        total += count_json(tools, model).tokens
    return total


def measure_via_middleware(
    config: FixtureConfig, middleware: BaseMiddleware, model: str = DEFAULT_MODEL
) -> StandingContextResult:
    """Black-box totals for any middleware: one canonical request body,
    standing tokens before vs after optimize_request."""
    body = canonical_body(config, model)
    before = standing_tokens(body, model)
    optimized = middleware.optimize_request(json.loads(json.dumps(body)), "anthropic")
    after = standing_tokens(optimized, model)
    label = count_text("probe", model).label
    return StandingContextResult(
        config_name=config.name,
        model=model,
        mode=f"middleware:{middleware.name}",
        rows=[CategoryRow(category="request-standing-total", tokens_before=before, tokens_after=after, label=label)],
    )
