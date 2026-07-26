# Security Policy

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report it privately through GitHub Security Advisories:

> **[Security](https://github.com/jettison-ai/jettison-parity/security) →
> Advisories → Report a vulnerability**
> (direct link: <https://github.com/jettison-ai/jettison-parity/security/advisories/new>)

That opens a private thread visible only to maintainers. If you cannot use it,
email **saurabh.ssy@gmail.com** with `SECURITY` in the subject line.

Please include:

- what the issue lets an attacker do, and who the attacker has to be;
- the affected component (`parity/cli.py`, a benchmark family, `fixtures.py`,
  `middleware.py`) and the commit;
- the exact `parity run` command and a minimal fixture config that reproduces
  it — **not** a config derived from your real, private setup;
- your OS, Python version, and the `jettison` commit you had installed.

If the issue is really in the optimizer rather than the harness, report it at
[jettison-ai/jettison](https://github.com/jettison-ai/jettison/security/advisories/new)
instead — this repo imports `jettison`, so a fair number of findings will land
there.

### What to expect

| | Target |
|---|---|
| Acknowledgement | within 3 business days |
| Initial assessment | within 7 days |
| Fix or documented mitigation for confirmed high-severity issues | within 30 days |

Good-faith targets from a small, single-maintainer, pre-1.0 project — not an
SLA. Credit in the advisory unless you prefer otherwise. No paid bounty.

## Supported versions

| Version | Supported |
|---|---|
| `0.1.x` | Yes — current release line |
| `main` | Yes — fixes land here first |
| anything older | No |

## Threat model — what the harness touches

The harness is a **deterministic, offline benchmark runner**. That makes its
attack surface small and worth stating precisely:

- **No network at runtime.** Every shipped benchmark family runs fully offline.
  There are no model calls: the "model" driving task parity is a scripted
  deterministic policy. There are no API keys, no credentials and no secrets
  anywhere in this repo or its CI.

- **One deliberate outbound path, opt-in only.**
  `parity.middleware.HTTPProxyMiddleware` POSTs a request body to a URL **you**
  supply, so you can score a third-party optimizer that speaks HTTP. It is
  never used by the built-in families and never by the test suite. If you point
  it at a remote endpoint, you are sending that endpoint your fixture request
  bodies — use fixture data, not real prompts.

- **It measures fixtures, never your real setup.** Unlike `jettison audit`,
  this harness does not read your client's MCP configuration, does not launch
  MCP servers, and does not touch `$HOME`. Its only input is a fixture config
  directory of committed files. Keep it that way: environment-dependent input
  would break determinism *and* widen this surface.

- **It executes the optimizer under test in-process.** `pip install -e
  ../jettison` plus `import jettison` means the middleware being benchmarked
  runs with your full user privileges. Benchmarking an untrusted third-party
  middleware is equivalent to running untrusted code — do it in a container.

- **Fixture configs and task files are data, not code.** `tools/*.json`,
  `tasks/*.json` and the markdown instruction files are parsed, never
  evaluated. Anything that turns fixture *content* into execution — a path
  traversal out of the config directory, a deserialization gadget — is a
  vulnerability, and a config directory downloaded from a PR is exactly the
  hostile input to think about.

- **Nothing is transmitted about you.** No telemetry, no analytics, no
  phone-home, in any code path.

### In scope

- Any code execution triggered by parsing a fixture config, a task file or an
  RCT log (`--rct-log`).
- Path traversal or arbitrary file reads/writes via `--config`, `--tasks`,
  `--rct-log` or `--out`.
- Any unexpected outbound network connection from a shipped benchmark family.
- Leakage of a user-supplied endpoint URL, header or token into results JSON,
  logs, or CI artifacts.
- **Result forgery**: anything that lets a middleware under test detect it is
  being benchmarked and alter behaviour, or otherwise cause the harness to
  report a favourable number it did not earn. For a measurement instrument,
  this is a security issue, not just a bug.

### Out of scope

- Vulnerabilities in `jettison` itself — report those in that repo.
- The fact that the RCT family's synthetic generator produces made-up data. It
  is labeled *"synthetic demonstration — not evidence"* by design.
- Denial of service caused by pointing the harness at an enormous config of
  your own making.
- Anything requiring an attacker who already has local code execution as your
  user.
