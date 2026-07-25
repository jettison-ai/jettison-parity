<!--
This repo produces numbers other people quote. The checklist is the review, not
paperwork. See CONTRIBUTING.md.
-->

## What this changes

<!-- One paragraph. What did you change and why? -->

Closes #

## Does this move any existing number?

<!--
REQUIRED. Silent number drift is the worst thing that can happen here.
If nothing moves, say "no numbers change" and why (pure refactor, docs, ...).
If something moves, paste before/after WITH the config name and bundle_hash.

| metric | config | before | after | label |
|---|---|---|---|---|
| standing total_saved | configs/mcp-heavy | 39,412 | 41,008 | estimated |

...and explain WHY it moved. "The optimizer got better" needs to be visible in
the diff, or it's a measurement bug.
-->

## How it was verified

<!-- Commands you actually ran, with results. -->

```
python -m pytest -q
parity run --family all --config configs/mcp-heavy    --json --out a.json
parity run --family all --config configs/openclaw-like --json --out b.json
parity run --family all --config configs/mcp-heavy    --json --out a2.json && diff a.json a2.json && echo IDENTICAL
```

## Checklist

**Correctness**

- [ ] `python -m pytest -q` passes (with `jettison` installed editable from a sibling checkout).
- [ ] `parity run --family all` runs clean on **both** shipped configs.
- [ ] New behaviour has a test; a fixed bug has a test that fails without the fix.

**Determinism** — see CONTRIBUTING.md § Determinism rules

- [ ] Two identical runs produce byte-identical JSON.
- [ ] No clock, no timestamp, no `datetime`, nothing time-varying in any output.
- [ ] No unseeded randomness. Any randomness derives from an explicit `--seed` or a content hash.
- [ ] No network in any shipped family or test.
- [ ] Everything is read in sorted order; no `os.walk` / `glob` / `set` / dict-insertion order reaches a result.
- [ ] No `$HOME`, no user config, no installed-client discovery, no locale-sensitive formatting.

**Honest measurement**

- [ ] Every new number carries a `measured` or `estimated` label, and aggregates take the weaker label.
- [ ] Dollar figures are computed per billing tier (cache read / fresh input / cache write), never `tokens_saved × input_price`.
- [ ] Synthetic output is labeled "synthetic demonstration — not evidence".
- [ ] Any reported figure names its config directory and `bundle_hash`.

**Fairness**

- [ ] This works for any middleware implementing `optimize_request`, not only `jettison`.
- [ ] The baseline arm (`--middleware none`) is still a true identity baseline.
- [ ] No fixture or task was tuned to make an optimizer look better. If a fixture changed, the justification is below and the old one still ships.

**Regression semantics** (if you touched the `parity` family)

- [ ] A completion drop, wrong tool, lost required param, lost critical fact or lost verifier commitment still produces exit code `2`.
- [ ] The results JSON is still written *before* the nonzero exit, so CI can upload it.

**Docs**

- [ ] `README.md`'s family table is up to date.
- [ ] If this backs a published claim, `jettison`'s `docs/BENCHMARKS.md` is updated in a matching PR.

## Notes for the reviewer

<!-- Trade-offs, things you're unsure about, follow-up work. -->
