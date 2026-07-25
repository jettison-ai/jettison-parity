"""Fixture-config loading and jettison bundle construction.

A fixture config directory models one client's standing context:

    <config>/
      tools/*.json     one tool definition per file ({name, description,
                       input_schema}), or a JSON list of such definitions
      *.md             instruction files loaded into the system prompt
                       (CLAUDE.md, AGENTS.md, system_prompt.md, ...)
      skills/*.md      skill files (frontmatter description + body)

Everything is read in sorted order so results are deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jettison.compiler import (
    build_bundle,
    compile_instructions,
    compress_description,
    minify_tools,
    summarize_skill,
)
from jettison.registry import CapabilityStore, render_capability_index


@dataclass(frozen=True)
class FixtureConfig:
    name: str
    root: Path
    tools: list[dict[str, Any]] = field(default_factory=list)
    # (label, text) in sorted-filename order
    instruction_files: list[tuple[str, str]] = field(default_factory=list)
    # (skill name, full text) in sorted-filename order
    skill_files: list[tuple[str, str]] = field(default_factory=list)

    def instructions_text(self) -> str:
        return "\n\n".join(text for _, text in self.instruction_files).strip()

    def skills_text(self) -> str:
        return "\n\n".join(text for _, text in self.skill_files).strip()

    def baseline_system_text(self, task_system: str = "") -> str:
        """The unoptimized system prompt: task system + every instruction
        file + every full skill body (the 'preload everything' client)."""
        parts = [p for p in (task_system, self.instructions_text(), self.skills_text()) if p]
        return "\n\n".join(parts)


def load_config(path: str | Path) -> FixtureConfig:
    root = Path(path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"fixture config directory not found: {root}")

    tools: list[dict[str, Any]] = []
    tools_dir = root / "tools"
    if tools_dir.is_dir():
        for f in sorted(tools_dir.glob("*.json")):
            data = json.loads(f.read_text())
            if isinstance(data, list):
                tools.extend(data)
            else:
                tools.append(data)

    instruction_files = [
        (f.name, f.read_text())
        for f in sorted(root.glob("*.md"))
        if f.name.lower() != "readme.md"
    ]

    skill_files = []
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        skill_files = [(f.stem, f.read_text()) for f in sorted(skills_dir.glob("*.md"))]

    return FixtureConfig(
        name=root.name,
        root=root,
        tools=tools,
        instruction_files=instruction_files,
        skill_files=skill_files,
    )


def build_store(config: FixtureConfig) -> CapabilityStore:
    """Compile a fixture config into a jettison CapabilityStore.

    Deterministic: same config bytes -> same bundle content hash.
    """
    minified = minify_tools(config.tools)
    skills = [summarize_skill(name, text) for name, text in config.skill_files]
    compiled = compile_instructions(config.instruction_files)
    pairs = [
        (t.name, compress_description(t.description, max_sentences=1, max_chars=100))
        for t in minified.tools
    ]
    index_text = render_capability_index(pairs, skills)
    bundle = build_bundle(minified, compiled, skills, index_text)
    return CapabilityStore(bundle=bundle)
