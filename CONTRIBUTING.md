# Contributing to jettison-parity

This repo is a **measurement instrument**. Its output is used to make public
claims about how much an optimizer saves and whether it changes answers, so the
bar here is different from a normal library: a benchmark that is convenient but
slightly dishonest is worse than no benchmark at all.

Two rules carry most of the weight:

1. **Deterministic, offline, byte-reproducible.** Anyone must be able to rerun
   a number and get the same bytes.
2. **Every number carries a `measured` or `estimated` label**, and labels
   propagate pessimistically.

Everything below is downstream of those.

---

## Dev setup

The harness has no vendored copy of the optimizer: `parity.fixtures`,
`parity.middleware` and `parity.benchmarks.task_parity` all `import jettison`.
So you need **both repos checked out as siblings**:

```
code/
  jettison/          # github.com/jettison-ai/jettison
  jettison-parity/   # this repo
```

With [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/jettison-ai/jettison
git clone https://github.com/jettison-ai/jettison-parity
cd jettison-parity
uv venv --python 3.13
source .venv/bin/activate
uv pip install -e ../jettison        # the optimizer under test
uv pip install -e ".[dev]"           # the harness + pytest
```

With stdlib venv + pip:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ../jettison
python -m pip install -e ".[dev]"
```

Notes:

- **`pyproject.toml` currently declares `requires-python = ">=3.11"`** while
  `jettison` supports 3.10+. CI therefore matrixes 3.11/3.12/3.13. Nothing in
  the source actually needs 3.11 (the modern union annotations are all behind
  `from __future__ import annotations`), so if you want the floors to match,
  lowering it and adding `"3.10"` to the CI matrix is a one-line change.
- Installing `jettison` **bare** (no `[runtime]`) is intentional: the harness
  only uses jettison's pure surfaces, and keeping the install minimal means CI
  notices if a benchmark starts depending on the proxy stack.
- Always install `jettison` **editable from a checkout**, never from PyPI, when
  you are benchmarking a change. The point of a run is to score the working
  tree, not the last release.

## Running things

```bash
python -m pytest -q                                   # full suite: offline, ~1s

parity run --family all --config configs/mcp-heavy    # human-readable tables
parity run --family standing --config configs/openclaw-like
parity run --family parity --middleware none          # the identity baseline arm
parity run --family cost --turns 100
parity run --family rct --seed 7
parity run --family all --config configs/mcp-heavy --json --out results.json
```

Exit codes: `0` = all families ran with no task-parity regressions; `2` =
the task-parity family found a regression (completion drop, wrong tool, lost
required param, lost critical fact, or a lost verifier commitment). The
results file is written *before* the nonzero exit, so CI can upload it and
report which task broke.

## Adding a benchmark family

A "family" is one question the harness can answer. There are four today:
`standing`, `parity`, `cost`, `rct`. To add a fifth:

1. **Create `parity/benchmarks/<family>.py`.** Give it one entry point
   (`run(...)` or `measure(...)`) that takes a `FixtureConfig` and/or a
   middleware and returns a result **dataclass**.
2. **The result dataclass needs a `to_dict()`** returning JSON-serializable
   data, including:
   - `"family": "<name>"` — so a merged results file is self-describing;
   - the config name, model, and middleware name it ran under;
   - a `label` (`measured` | `estimated`) on every number or group of numbers,
     with the aggregate taking the weaker label of its parts.
   Follow the existing shapes: `standing_context.StandingContextResult`,
   `task_parity.TaskParityResult`.
3. **Wire it into `parity/cli.py`**: add the name to `FAMILIES`, add an
   `if family in ("<name>", "all"):` block that stores
   `results["<name>"] = res.to_dict()`, and add a `_render_<name>()` that
   prints a Rich table when `--json` is not set. Every rendered table must show
   the label; look at `_render_standing` for the convention.
4. **Decide whether it gates.** Only `parity` sets a nonzero exit code today,
   because only it can detect a *correctness* regression. If your family
   detects a regression rather than reporting a magnitude, set `exit_code` the
   same way; if it just reports a number, do not.
5. **Add tests** in `tests/test_<family>.py`: at minimum, that it runs on both
   fixture configs, that its numbers are labeled, and that **two runs produce
   identical output**.
6. **Document it** in the family table in `README.md`, and in the
   `jettison` repo's `docs/BENCHMARKS.md` if it will back a published claim.

New fixture configs go in `configs/<name>/` with the layout `parity.fixtures`
expects (`tools/*.json`, `skills/*.md`, top-level `*.md` instruction files).
Everything is read in sorted order. Fixture content must be committed, not
generated at runtime — a config nobody can inspect cannot back a claim.

New tasks go in `tasks/NN-name.json`, matching the schema documented at the top
of `parity/benchmarks/task_parity.py`. A task must pin `expected.tool`,
`expected.required_params`, `expected.critical_facts` and
`expected.final_answer_contains` — a task with nothing to lose measures nothing.

## Determinism rules

These are enforced by tests and by CI (which runs the suite twice and compares
bytes). Break one and the harness stops being evidence.

- **No clocks.** No `time.time()`, no `datetime.now()`, no timestamps in any
  output. Two runs a day apart must produce identical JSON.
- **No unseeded randomness.** The RCT generator takes an explicit `--seed`;
  arm assignment is a `sha256` of the conversation key, not a coin flip.
  Nothing else in the suite may use `random` at all.
- **No network.** The suite runs offline. `HTTPProxyMiddleware` is the single
  deliberate exception and is only reachable when a user explicitly points it
  at their own endpoint; it is never used by the built-in families, and no
  test may depend on it reaching anything.
- **Sort everything.** Files are read in sorted order; dicts are serialized
  with sorted keys where order could leak into output. Never let `os.walk`,
  `glob`, `set` iteration or dict insertion order reach a result.
- **No environment dependence.** No `$HOME`, no user config, no installed-MCP
  discovery, no locale-sensitive formatting. A fixture config is the only
  input.
- **No wall-clock-dependent numbers.** Latency, if ever measured, is reported
  separately and explicitly excluded from the byte-comparison gate — never
  folded into a determinism-checked payload.

## Honest-measurement rules

Restated from the README because they are the reason this repo exists:

- **Label everything.** `measured` = an exact tokenizer or a resolved provider
  price produced it. `estimated` = a calibrated estimator or a fallback table
  did. Aggregates take the weaker label. An unlabeled number is a bug; CI fails
  on unlabeled rows.
- **Synthetic is never evidence.** The RCT family's built-in generator
  demonstrates the pipeline. Its output is labeled *"synthetic demonstration —
  not evidence"* and must never be quoted as a result. Real claims need
  recorded logs via `--rct-log`.
- **Disclose the config.** A number without its fixture is meaningless. Always
  report the config directory and its `bundle_hash` alongside any figure.
- **Price cache-aware.** Dollar savings are computed per billing tier (cache
  read / fresh input / cache write) under both cache-hit and cache-miss
  regimes. Never multiply tokens saved by the input price — that overstates
  savings by up to ~10x on cache-hit-heavy agents.
- **Do not tune fixtures toward a result.** If a new fixture makes the
  optimizer look better, it needs a justification that would survive a hostile
  reader: it must model a real setup, and the old fixture stays.

## Running against your own middleware

The harness scores **any** optimizer, not just Jettison. That is the point —
third parties should be able to check our numbers or beat them.

The minimum contract is one method:

```python
class Middleware(Protocol):
    name: str
    def optimize_request(self, body: dict, provider: str) -> dict: ...
```

`body` is a provider-format request dict (Anthropic Messages or OpenAI Chat
Completions). Subclass `parity.middleware.BaseMiddleware` and you only have to
implement that one method; the base class provides no-op `patch_incoming` and
`run_interception` hooks. Implement those two as well only if your optimizer
resolves its own meta-tools mid-turn (search/load style indirection) — see
`JettisonMiddleware` for a worked example.

In-process:

```python
from parity.benchmarks import standing_context, task_parity
from parity.fixtures import load_config
from parity.middleware import BaseMiddleware

class MyMiddleware(BaseMiddleware):
    name = "mine"
    def optimize_request(self, body, provider):
        ...
        return body

cfg = load_config("configs/mcp-heavy")
print(standing_context.measure_via_middleware(cfg, MyMiddleware()).to_dict())
print(task_parity.run("tasks", "configs", middleware_factory=lambda c: MyMiddleware()).to_dict())
```

Over HTTP, with no Python integration at all — point the harness at any
compression endpoint that accepts `{"provider": ..., "body": ...}` and returns
`{"body": optimized_body}`:

```python
from parity.middleware import HTTPProxyMiddleware
mw = HTTPProxyMiddleware("http://localhost:8080/compress")
```

To expose your optimizer as a `--middleware` choice on the CLI, register it in
`get_middleware()` and add its name to `MIDDLEWARE_NAMES` in
`parity/middleware.py`. Always report the baseline arm (`--middleware none`)
next to your own numbers.

## Pull requests

- Branch from `main`, keep it focused.
- `python -m pytest -q` passes, and `parity run --family all` runs clean on
  **both** shipped configs.
- New numbers are labeled; new families have a determinism test.
- If your change moves an existing benchmark number, say so explicitly in the
  PR description with before/after and the config name. Silent number drift is
  the single worst thing that can happen to this repo.

## Reporting bugs and vulnerabilities

Bugs: use the issue forms, and include the exact `parity run` command, the
config, and the `--json` output.

Security vulnerabilities: **never in a public issue.** See
[SECURITY.md](SECURITY.md).

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
