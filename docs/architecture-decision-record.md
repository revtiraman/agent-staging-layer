# Architecture decision record (proposed, awaiting Gate 1)

Date 2026-10-08. Evidence: `docs/research/`. Status of every decision: **Proposed.**

## Summary

**Compose on Seahaven (MIT, Python) as the twin kernel; build the differentiating layer first;
choose the Stripe twin last (stripe_world if Kiln licenses it, our own subset otherwise). The
layer we build ourselves:** seeded faults, webhook
scheduler on a virtual clock, scenario + invariant engine, hash-chained run log and replay,
change-log-based dry-run with hash-bound approval, CLI and a GitHub Actions job. One Python
process, SQLite files, no services.

```
 agent process (unchanged code; STRIPE base URL → twin)        staging CLI
        │ HTTPS-shaped requests                                   │
        ▼                                                         ▼
 ┌──────────────── staging runtime (one process) ─────────────────────────┐
 │ request validation (stripe/openapi) → FAULT MIDDLEWARE (seeded) →       │
 │ Stripe world handlers ── Seahaven instance (SQLite, fixture branch) ──┐ │
 │        │ events                                                      │ │
 │        ▼                                                             │ │
 │ WEBHOOK SCHEDULER (virtual clock; delay/dup/reorder by seed) ──► agent │ │
 │        │                                                             │ │
 │ INVARIANT ENGINE (after every step, from state + change log) ◄───────┘ │
 │        │                                                               │
 │ RUN LOG (JSONL, hash-chained) ──► REPLAY ENGINE                        │
 │        │                                                               │
 │ DRY-RUN: snapshot → fixture → branch → agent → change log → PLAN/diff  │
 │        └──► APPROVAL (bound to plan hash) ──► APPLY (stale-base check) │
 └────────────────────────────────────────────────────────────────────────┘
        │ run artifacts (.staging/runs/<id>/)
        ▼
 replay UI (static page)       GitHub Actions job (fails PR on hard invariant)
```

Differences from the brief's diagram: no separate "Scenario Controller" service (it's a library
called by the CLI); Shopify/Zendesk twins are interfaces only; faults sit *before* the handlers so
they replay deterministically; the dry-run diff comes from Seahaven's change log instead of a
separate diff engine.

## A. Twin kernel: Seahaven (dependency, not fork)

- **Why:** the only MIT kernel with per-run SQLite instances, fixtures that record their parent
  (branches), a before/after change log per row, and a seeded clock/ids/RNG. 2,113 tests pass
  locally. Its own docs leave faults and clock control "to the harness around the world": that is
  our layer, so we compose instead of fighting it.
- **Alternatives:** Volter twin (Apache-2.0, but a direct competitor with a non-public kernel
  repo); vercel-labs/emulate (Apache-2.0, great DX, in-memory, Stripe without refunds or events);
  our own kernel (re-doing determinism that already exists).
- **Trade-offs:** Python ≥3.14 only; project is about 4 weeks old with ★11. Mitigation: everything
  of ours talks to a small `TwinKernel` protocol (create instance, call, change log, freeze, fork),
  with Seahaven as its first adapter, so it can be swapped.
- **Licence:** MIT. **Ops complexity:** a pip dependency. **Scale:** hundreds of instances per
  process (its claim, UNVERIFIED; we benchmark in Phase 19). **MVP fit:** high.

## A2. Stripe twin: deferred until Kiln answers (revised after Gate 1 review)

The twin is a commodity; the weeks go to the layer above it. So the Stripe twin is **not**
built first.

- **Ask first:** a licence request to Kiln for stripe_world (`docs/outreach/kiln-stripe-world-licence.md`).
- **If MIT/Apache:** depend on stripe_world unmodified; our only Stripe work is webhook delivery
  (offered upstream) and fault hooks. Estimated days, not weeks.
- **If no, or no answer in 7 days:** write the MVP subset as our own Seahaven world from
  `stripe/openapi` (MIT): Customers, PaymentIntents, Charges, Refunds, Events, WebhookEndpoints,
  roughly 1,500 lines. Refund rules: no refund on a missing payment, an already fully refunded charge, or
  an amount above the remainder.
- **Meanwhile:** the layer is built against the `TwinKernel` protocol and exercised with
  `refund_path`, a deliberately tiny Seahaven test world (customer, charge, refund, event; a few
  hundred lines). It is test scaffolding, labelled as such, never shipped as "the Stripe twin".
  Whichever Stripe twin wins plugs in behind the same protocol, and the layer's tests rerun
  against it unchanged.
- **Not:** Volter as a dependency (competitor; kernel source not public). Its fork/plan/lease
  design is studied in `docs/research/volter-plan-fork-lease.md`.

## Implementation order (revised)

| # | Milestone | Depends on Kiln? |
| --- | --- | --- |
| 1 | 3-day spike: dry-run → diff → approve UX on `refund_path` (Seahaven change log) | No |
| 2 | `TwinKernel` protocol + Seahaven adapter + harness-owned virtual clock | No |
| 3 | Seeded fault middleware + webhook scheduler (delay, duplicate, reorder, 429, timeout) | No |
| 4 | Scenario + hard-invariant engine | No |
| 5 | Hash-chained run log + `staging replay` (environment replay) | No |
| 6 | Dry-run: plan with full-content hash, human-only approval, per-object stale check, apply | No |
| 7 | CLI + local GitHub Actions job | No |
| 8 | Stripe twin: stripe_world adapter **or** own subset | **Yes** |
| 9 | Replay UI, threat model, conformance against a Stripe sandbox, benchmarks, docs, demo | Partly (sandbox key) |

## B. Agent execution harness

The agent is an ordinary subprocess. The harness sets `STRIPE_API_BASE` (or the SDK's base URL)
to the twin and gives it a test key; it receives webhooks at its own endpoint. Same code in dev,
CI and production; only env changes. MCP front door: Seahaven can serve MCP (post-MVP for us).
The demo ships two agents: a deterministic scripted one (for tests) and an optional Claude-backed
one (real API calls, logged).

## C. Scenario engine

YAML files validated by pydantic: `seed` (fixture name or generator + count), `actors`
(customer, support agent, payment system), `faults`, `actions` (timed on the virtual clock),
`assertions`. Composable by `extends:`; versioned in git; runs identically locally and in CI.
Grading model from tau2-bench: final state, plus invariants after every step.

## D. Replay engine

Run log = JSONL in `.staging/runs/<id>/`: header (seed, scenario hash, fixture checksum, agent
version, model, prompt hash), then every request, response, fault decision, webhook delivery, state
change and invariant result. Each record carries the SHA-256 of the previous one (tamper evidence).

Two replay levels, stated honestly:
1. **Environment replay** (default, `staging replay <id>`): feed the recorded agent requests back
   into a fresh branch with the same seed; assert every response, fault and invariant matches
   byte-for-byte. Fully deterministic, no LLM.
2. **Agent re-run** (`--live`): run the agent again in the identical environment. Model sampling
   isn't deterministic, so this reproduces the conditions, not necessarily the decision.

## E. Dry-run engine

Snapshot (MVP: export from a Stripe **test-mode** account, or a twin "production" fixture in the
demo) → Seahaven fixture → branch → run agent → change log → **plan**: each change mapped to the
Stripe operation that would reproduce it, with idempotency key, `destructive` and `irreversible`
flags, and money totals. Approval is recorded with the SHA-256 of the exact ordered plan, so any
change to the plan voids it. Apply re-reads the touched objects first and refuses if they changed
since the snapshot. Applying to real Stripe is behind `--target stripe-sandbox`, and `sk_live_`
keys are refused outright in the MVP.

## F. CI integration

A composite GitHub Action in this repo (not published) runs `staging test` in a container,
uploads `.staging/runs/` as an artifact, prints `staging replay <id>` for failures, and exits
non-zero on any hard invariant failure.

## G. Storage

SQLite files per instance (Seahaven) plus JSONL run logs on disk. No database server, no queue.

## H. Observability

Structured JSON logs; optional OpenTelemetry spans (exporter off by default) with the run id as the
correlation id, so tools like agentevals can read runs.

## Open risks

| # | Risk | Mitigation |
| --- | --- | --- |
| 1 | stripe_world unlicensed | Ask Kiln first; build the layer meanwhile; own subset only as the 7-day fallback |
| 2 | Seahaven is young, Python 3.14-only | `TwinKernel` protocol; pin version; uv manages Python |
| 3 | Seahaven's clock can't be advanced on demand (verified: modes are `fixed`, `tick` (+1 s per call), `running`, `wall`; no advance API) | The harness owns a virtual clock for faults and webhook scheduling; the twin runs in `tick` mode, so object timestamps stay deterministic. Spike in Phase 5 to confirm timestamps stay plausible |
| 4 | Fidelity claims need real sandbox recordings | Needs a Stripe test-mode key from you (Phase 18) |
| 5 | Volter ships plan → review → push; if they add seeded faults they take the wedge | Ship the fault/replay/CI loop first (milestones 3–5, 7); close their approval gaps (agent can self-approve, hash covers ids not amounts); don't race on vendor breadth |
| 6 | LLM nondeterminism | Two-level replay (D), documented |
| 7 | Solo bandwidth | Strict MVP; Shopify/Zendesk stay interfaces |
