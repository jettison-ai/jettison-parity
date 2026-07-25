"""Family B: task parity — does the optimizer change task outcomes?

Each task JSON file describes a deterministic scenario:

    {
      "id": "...", "title": "...",
      "config": "mcp-heavy",            # fixture config supplying tools
      "system": "...",                  # task-specific system text
      "user": "...",                    # opening user message
      "search_query": "...",            # what the scripted model searches for
      "skip_search": false,             # model loads directly from the index
      "mixed_turn": false,              # model pairs a meta call with a real call
      "tool_result": "...",             # canned result the client returns
      "final_answer": "...",            # scripted final assistant text
      "expected": {
        "tool": "fs_read_file",             # the tool that must be called
        "arguments": {"path": "..."},       # args the scripted model uses
        "required_params": ["path"],        # params that must be present+correct
        "critical_facts": ["..."],          # substrings that must survive in the
                                            # optimized standing context
        "final_answer_contains": "..."      # completion marker
      },
      "also_call": {"tool": "...", "arguments": {...}, "result": "..."}
    }

The task runs twice — baseline (``none`` middleware, full context) and
optimized (the middleware under test) — driven by the same scripted
fake-model policy (no network, no real LLM):

  1. If the expected tool has already returned a result -> final answer.
  2. If the expected tool is visible in the tools list -> call it.
  3. If its full schema arrived in a tool_result (loaded) -> call it.
  4. If meta-tools are visible -> search, then load, then call.
  5. Otherwise -> give up (scored as not completed).

Scoring per arm: task completion, correct tool selection + params,
critical-fact retention (both explicit facts and
``jettison.verifier.extract_text_commitments`` containment), api calls /
meta rounds, and total request tokens sent to the "provider". A
regression is a completion drop, a wrong tool, or a lost critical fact.
All numbers are deterministic.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jettison.proxy import formats
from jettison.registry import LOAD_TOOL, META_TOOL_NAMES, SEARCH_TOOL
from jettison.tokens import DEFAULT_MODEL, count_json
from jettison.verifier import extract_text_commitments

from parity.fixtures import FixtureConfig, load_config
from parity.middleware import BaseMiddleware, NoneMiddleware

MAX_CLIENT_STEPS = 8

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def load_tasks(tasks_dir: str | Path) -> list[dict[str, Any]]:
    tasks_dir = Path(tasks_dir)
    tasks = [json.loads(f.read_text()) for f in sorted(tasks_dir.glob("*.json"))]
    if not tasks:
        raise FileNotFoundError(f"no task files (*.json) found in {tasks_dir}")
    return tasks


# ---------------------------------------------------------------------------
# Scripted fake model (Anthropic response format)
# ---------------------------------------------------------------------------


class ScriptedModel:
    """Deterministic fake-model policy for one task arm."""

    def __init__(self, task: dict[str, Any], model: str = DEFAULT_MODEL):
        self.task = task
        self.model = model
        self.expected = task["expected"]
        self.api_calls = 0
        self._seq = 0
        self._also_done = False

    def _id(self) -> str:
        self._seq += 1
        return f"call_{self.task['id']}_{self._seq:02d}"

    def _response(self, content: list[dict[str, Any]], stop_reason: str) -> dict[str, Any]:
        return {
            "id": f"msg_{self.task['id']}_{self.api_calls:02d}",
            "type": "message",
            "role": "assistant",
            "model": self.model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }

    def _tool_use(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"type": "tool_use", "id": self._id(), "name": name, "input": arguments}

    @staticmethod
    def _tool_results(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
        out = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        out.append((str(block.get("tool_use_id", "")), str(block.get("content", ""))))
        return out

    @staticmethod
    def _call_names(messages: list[dict[str, Any]]) -> dict[str, str]:
        names = {}
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        names[str(block.get("id", ""))] = str(block.get("name", ""))
        return names

    async def __call__(self, messages: list[dict[str, Any]], tools: Any) -> dict[str, Any]:
        self.api_calls += 1
        tools = tools or []
        visible = [t.get("name", "") for t in tools if isinstance(t, dict)]
        expected_tool = self.expected["tool"]
        arguments = self.expected.get("arguments", {})

        results = self._tool_results(messages)
        call_names = self._call_names(messages)

        # 1. expected tool already executed (a non-error result exists)?
        for call_id, content in results:
            if call_names.get(call_id) == expected_tool and not content.lower().startswith("error"):
                return self._response(
                    [{"type": "text", "text": self.task["final_answer"]}], "end_turn"
                )

        # 2. expected tool directly visible?
        if expected_tool in visible:
            content = [self._tool_use(expected_tool, arguments)]
            if self.task.get("mixed_turn") and not self._also_done and self.task.get("also_call"):
                also = self.task["also_call"]
                content.append(self._tool_use(also["tool"], also.get("arguments", {})))
                self._also_done = True
            return self._response(content, "tool_use")

        # 3. schema already loaded via the registry?
        loaded = any(
            '"loaded"' in content and f'"{expected_tool}"' in content for _, content in results
        )
        if loaded:
            return self._response([self._tool_use(expected_tool, arguments)], "tool_use")

        # 4. registry meta-tools visible?
        if LOAD_TOOL in visible:
            searched = any('"results"' in content for _, content in results)
            if searched or self.task.get("skip_search"):
                content = [self._tool_use(LOAD_TOOL, {"names": [expected_tool]})]
                if self.task.get("mixed_turn") and not self._also_done and self.task.get("also_call"):
                    also = self.task["also_call"]
                    content.append(self._tool_use(also["tool"], also.get("arguments", {})))
                    self._also_done = True
                return self._response(content, "tool_use")
            if SEARCH_TOOL in visible:
                query = self.task.get("search_query", expected_tool)
                return self._response([self._tool_use(SEARCH_TOOL, {"query": query})], "tool_use")

        # 5. dead end.
        return self._response(
            [{"type": "text", "text": "I cannot find a suitable tool for this task."}],
            "end_turn",
        )


# ---------------------------------------------------------------------------
# Arm driver (simulated client <-> middleware <-> scripted model)
# ---------------------------------------------------------------------------


@dataclass
class ArmResult:
    arm: str
    middleware: str
    completed: bool = False
    correct_tool: bool = False
    correct_params: bool = False
    wrong_tool_calls: int = 0
    critical_facts_total: int = 0
    critical_facts_retained: int = 0
    commitments_total: int = 0
    commitments_retained: int = 0
    api_calls: int = 0
    meta_rounds: int = 0
    mixed_turns: int = 0
    patched_results: int = 0
    request_tokens: int = 0  # total tokens sent to the provider across the arm
    final_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["request_tokens_label"] = "estimated"
        return d


@dataclass
class TaskOutcome:
    task_id: str
    title: str
    baseline: ArmResult
    optimized: ArmResult
    regressions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "baseline": self.baseline.to_dict(),
            "optimized": self.optimized.to_dict(),
            "regressions": self.regressions,
        }


@dataclass
class TaskParityResult:
    config_name: str
    middleware: str
    model: str
    outcomes: list[TaskOutcome] = field(default_factory=list)

    @property
    def regressions(self) -> list[str]:
        out = []
        for o in self.outcomes:
            out.extend(f"{o.task_id}: {r}" for r in o.regressions)
        return out

    @property
    def total_request_tokens(self) -> tuple[int, int]:
        return (
            sum(o.baseline.request_tokens for o in self.outcomes),
            sum(o.optimized.request_tokens for o in self.outcomes),
        )

    def to_dict(self) -> dict[str, Any]:
        base_tok, opt_tok = self.total_request_tokens
        return {
            "family": "task_parity",
            "config": self.config_name,
            "middleware": self.middleware,
            "model": self.model,
            "tasks": [o.to_dict() for o in self.outcomes],
            "regressions": self.regressions,
            "total_request_tokens_baseline": base_tok,
            "total_request_tokens_optimized": opt_tok,
            "request_tokens_label": "estimated",
        }


def _visible_context_text(body: dict[str, Any]) -> str:
    """All standing text the model can currently see (system + tools)."""
    parts = []
    system = body.get("system")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        parts.extend(
            str(b.get("text", "")) for b in system if isinstance(b, dict) and b.get("type") == "text"
        )
    tools = body.get("tools") or []
    if tools:
        parts.append(json.dumps(tools, ensure_ascii=False))
    return "\n\n".join(parts)


async def run_arm(
    task: dict[str, Any],
    config: FixtureConfig,
    middleware: BaseMiddleware,
    arm: str,
    model: str = DEFAULT_MODEL,
) -> ArmResult:
    expected = task["expected"]
    scripted = ScriptedModel(task, model)
    result = ArmResult(arm=arm, middleware=middleware.name)

    system_text = config.baseline_system_text(task.get("system", ""))
    messages: list[dict[str, Any]] = [{"role": "user", "content": task["user"]}]

    called: list[tuple[str, dict[str, Any]]] = []  # non-meta tool calls, in order
    seen_context: list[str] = []  # optimized standing context + loaded content
    final_text: str | None = None

    for _step in range(MAX_CLIENT_STEPS):
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": 1024,
            "system": system_text,
            "messages": messages,  # canonical history; patched in place
            "tools": [json.loads(json.dumps(t)) for t in config.tools],
        }
        result.patched_results += middleware.patch_incoming(body, "anthropic")
        opt_body = middleware.optimize_request(body, "anthropic")

        current_system = opt_body.get("system", "")

        async def api_call(msgs: list[dict[str, Any]], tools: Any) -> dict[str, Any]:
            result.request_tokens += count_json(
                {"system": current_system, "tools": tools or [], "messages": msgs}, model
            ).tokens
            return await scripted(msgs, tools)

        response = await api_call(opt_body.get("messages") or [], opt_body.get("tools") or [])
        info = await middleware.run_interception(response, opt_body, api_call, "anthropic")
        response = info.response
        result.meta_rounds += info.rounds
        if info.mixed_turn:
            result.mixed_turns += 1

        seen_context.append(_visible_context_text(opt_body))

        calls = formats.extract_tool_calls(response, "anthropic")
        if not calls:
            final_text = "".join(
                str(b.get("text", ""))
                for b in response.get("content") or []
                if isinstance(b, dict) and b.get("type") == "text"
            )
            break

        # Client executes what it can; unknown tools get an error result
        # (mixed-turn recovery patches those on the next request).
        tool_results = []
        for call in calls:
            if call.name in META_TOOL_NAMES:
                content = "Error: unknown tool"  # the client never saw meta-tools
            elif call.name == expected["tool"]:
                called.append((call.name, call.arguments))
                content = task.get("tool_result", "ok")
            elif task.get("also_call") and call.name == task["also_call"]["tool"]:
                called.append((call.name, call.arguments))
                content = task["also_call"].get("result", "ok")
            else:
                called.append((call.name, call.arguments))
                content = "Error: tool not available"
            tool_results.append((call, content))

        messages.append(formats.extract_assistant_message(response, "anthropic"))
        messages.extend(formats.build_tool_result_message(tool_results, "anthropic"))

    result.api_calls = scripted.api_calls
    result.final_text = final_text or ""
    marker = expected.get("final_answer_contains", "")
    result.completed = bool(final_text) and (marker in final_text if marker else True)

    # Loaded capability content also counts as visible context.
    for _, content in ScriptedModel._tool_results(messages):
        seen_context.append(content)
    context_text = _norm("\n".join(seen_context))

    # --- tool selection scoring ---
    allowed = {expected["tool"]}
    if task.get("also_call"):
        allowed.add(task["also_call"]["tool"])
    expected_calls = [(n, a) for n, a in called if n == expected["tool"]]
    result.wrong_tool_calls = sum(1 for n, _ in called if n not in allowed)
    result.correct_tool = bool(expected_calls) and result.wrong_tool_calls == 0
    if expected_calls:
        args = expected_calls[0][1]
        want = expected.get("arguments", {})
        required = expected.get("required_params", list(want.keys()))
        result.correct_params = all(p in args and args[p] == want.get(p, args[p]) for p in required)

    # --- critical-fact retention ---
    facts = expected.get("critical_facts", [])
    result.critical_facts_total = len(facts)
    result.critical_facts_retained = sum(1 for f in facts if _norm(f) in context_text)

    # --- commitment containment (jettison.verifier) ---
    commitments = extract_text_commitments(system_text, source="system")
    result.commitments_total = len(commitments)
    result.commitments_retained = sum(
        1 for c in commitments if _norm(c.span) in context_text or _norm(c.key) in context_text
    )
    return result


def compare_arms(task: dict[str, Any], baseline: ArmResult, optimized: ArmResult) -> list[str]:
    regressions = []
    if baseline.completed and not optimized.completed:
        regressions.append("task completion lost under optimization")
    if baseline.correct_tool and not optimized.correct_tool:
        regressions.append("correct tool selection lost under optimization")
    if baseline.correct_params and not optimized.correct_params:
        regressions.append("required tool params lost under optimization")
    if optimized.critical_facts_retained < optimized.critical_facts_total:
        missing = optimized.critical_facts_total - optimized.critical_facts_retained
        regressions.append(f"{missing} critical fact(s) missing from optimized context")
    if optimized.commitments_retained < baseline.commitments_retained:
        lost = baseline.commitments_retained - optimized.commitments_retained
        regressions.append(f"{lost} verifier commitment(s) lost under optimization")
    return regressions


def run(
    tasks_dir: str | Path,
    configs_root: str | Path,
    middleware_factory: Any,
    model: str = DEFAULT_MODEL,
) -> TaskParityResult:
    """Run every task twice (baseline vs optimized).

    ``middleware_factory(config) -> BaseMiddleware`` builds a FRESH
    middleware per task so session state never leaks between tasks.
    """
    tasks = load_tasks(tasks_dir)
    configs_root = Path(configs_root)
    config_cache: dict[str, FixtureConfig] = {}

    outcomes = []
    mw_name = "?"
    for task in tasks:
        cfg_name = task.get("config", "mcp-heavy")
        if cfg_name not in config_cache:
            config_cache[cfg_name] = load_config(configs_root / cfg_name)
        config = config_cache[cfg_name]

        baseline = asyncio.run(run_arm(task, config, NoneMiddleware(), "baseline", model))
        middleware = middleware_factory(config)
        mw_name = middleware.name
        optimized = asyncio.run(run_arm(task, config, middleware, "optimized", model))

        outcomes.append(
            TaskOutcome(
                task_id=task["id"],
                title=task.get("title", task["id"]),
                baseline=baseline,
                optimized=optimized,
                regressions=compare_arms(task, baseline, optimized),
            )
        )

    config_name = ",".join(sorted(config_cache))
    return TaskParityResult(
        config_name=config_name, middleware=mw_name, model=model, outcomes=outcomes
    )
