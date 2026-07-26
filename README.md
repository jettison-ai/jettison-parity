# jettison-parity — the Parity Harness

[![parity](https://github.com/jettison-ai/jettison-parity/actions/workflows/ci.yml/badge.svg)](https://github.com/jettison-ai/jettison-parity/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-29%20passing-brightgreen)](https://github.com/jettison-ai/jettison-parity/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)

A deterministic, offline benchmark suite for **agent token optimizers**.
It answers the two questions any optimizer must answer together:

1. **How many tokens/dollars does it save?**
2. **Does it change task outcomes?** (the "parity" in Parity Harness)

Built for [Jettison](https://github.com/jettison-ai/jettison), but the
harness scores **any** middleware through a small adapter interface (see
below). Every number published in Jettison's README that is not a live A/B
comes from here, and is reproducible with one command.

## The four benchmark families

| Family | Command | What it measures |
|---|---|---|
| Standing context | `parity run --family standing` | Tokens per turn of standing context (tool schemas, skill bodies, instruction files) before vs after optimization, broken down per category. Deterministic, byte-reproducible. |
| Task parity | `parity run --family parity` | Runs each task in `tasks/` twice — baseline (full context) and optimized — driven by the same scripted deterministic fake-model policy. Scores task completion, correct tool selection and required params, critical-fact retention (explicit facts plus `jettison.verifier.extract_text_commitments` containment), meta rounds/mixed turns, and request tokens. **Exits nonzero on any regression.** |
| Session cost | `parity run --family cost` | Dollar cost of an N-turn session (default 50) with the three input tiers priced separately (cache read / fresh input / cache write) in both cache-hit and cache-miss regimes. Naive `tokens_saved x input_price` accounting overstates savings ~10x on cache-hit-heavy agents; this family prices honestly. |
| Holdout RCT | `parity run --family rct` | Framework for a randomized holdout over **recorded request logs**: deterministic sha256 arm assignment (Headroom-style), per-arm mean tokens/request, 95% normal-approx CI on the delta. Ships with a seeded synthetic generator so it runs out of the box — synthetic results are always labeled **"synthetic demonstration — not evidence"**. |

## Quickstart

```bash
pip install -e .          # needs the `jettison` package importable
parity run --family all --config configs/mcp-heavy
parity run --family all --json --out results.json
```

Two fixture configs ship with the harness:

- `configs/mcp-heavy/` — 20 verbose MCP-style tool schemas, CLAUDE.md +
  AGENTS.md with duplicated paragraphs, 3 skills.
- `configs/openclaw-like/` — 70 skill one-liners, a ~14K-token system
  prompt, and ~9K tokens of fat tool schemas.

All fixture content is deterministic (no unseeded randomness anywhere in
the suite; the RCT generator takes an explicit `--seed`).

## Running against your own middleware

The harness talks to optimizers through one method:

```python
class Middleware(Protocol):
    name: str
    def optimize_request(self, body: dict, provider: str) -> dict: ...
```

Built-ins: `none` (identity baseline) and `jettison` (rewrite pipeline +
meta-tool interception loop). To benchmark a third-party optimizer
exposed over HTTP, use `parity.middleware.HTTPProxyMiddleware`, which
POSTs `{"provider": ..., "body": ...}` to your compression endpoint and
expects `{"body": optimized_body}` back:

```python
from parity.middleware import HTTPProxyMiddleware
from parity.benchmarks import standing_context, task_parity
from parity.fixtures import load_config

mw = HTTPProxyMiddleware("http://localhost:8080/compress")
cfg = load_config("configs/mcp-heavy")
print(standing_context.measure_via_middleware(cfg, mw).to_dict())
```

Optimizers that resolve their own meta-tools mid-turn additionally
implement `run_interception` / `patch_incoming` (no-op defaults in
`BaseMiddleware`).

## Task files

Each JSON file in `tasks/` is one deterministic scenario: the tools come
from a fixture config, the "model" is a scripted policy (search -> load
-> call through a registry when the full catalog is absent; direct call
when it is present), and `expected` pins the tool that must be called,
its required params, the critical facts that must survive in the
optimized context, and the completion marker. The shipped six cover:
tool selection among 20 tools, a search->load->call chain, a critical
numeric constraint, a security rule, an output-format requirement, and a
mixed turn (meta-call alongside a client-owned tool call).

## Honest-measurement rules

- **Every number carries a label.** `measured` means an exact tokenizer
  or provider-derived price produced it; `estimated` means a calibrated
  estimator or fallback table did. Labels propagate pessimistically:
  a total containing any estimated component is labeled estimated.
- **Synthetic is never evidence.** The RCT family's built-in generator
  exists to demonstrate the pipeline; its output is labeled
  "synthetic demonstration — not evidence" and should never be quoted
  as a result. Real claims require recorded logs (`--rct-log`).
- **Disclosed configs.** A benchmark number is meaningless without its
  fixture: always report the config directory (and its content hash —
  `bundle_hash` in the standing-context output) alongside any number.
- **Cache-aware dollars.** Dollar savings are computed per billing tier
  (cache read vs fresh input vs cache write), in both cache-hit and
  cache-miss regimes. The RCT CI note flags conversation-level
  clustering, which the naive interval ignores.
- **No network, no clocks.** The whole suite runs offline; outputs
  contain no timestamps, so byte-identical reruns are expected (and
  tested).

## Reproduce

```bash
python -m pytest              # full suite, offline, deterministic
parity run --family all --config configs/mcp-heavy --json
```

## License

Apache-2.0.
