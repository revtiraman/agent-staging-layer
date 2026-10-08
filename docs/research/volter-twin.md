# @volter/twin + @volter/twin-stripe (Volter)

npm `@volter/twin` 0.1.3 (Apache-2.0) and `@volter/twin-stripe` 3.0.2 (Apache-2.0), TypeScript.
Kernel `repository` points to `github.com/volter-ai/twin` → **404**. Stripe pack source:
`volter-ai/twin-packs-open` (★0, created 2026-10-06). Part of Volter's commercial "World" product
(`@volter/world`, `npx volter world up`).

## Architecture

Kernel (about 9,200 lines, `src/`): event log (`events.jsonl`, observed vendor facts), action log
(`actions.jsonl`, local writes), projected state = `fold(events) + apply(actions)`, push/egress
ledger to the real vendor, connectors that pull real vendor state, `fork.ts`, `plan.ts`,
`changeset.ts`, `rateBudget.ts`, `lease.ts`, `serve.ts`.

Stripe pack: surface generated from Stripe's OpenAPI; handlers in `src/semantics/<family>.ts`
(customers, payment_intents, charges, refunds, events, webhook_endpoints, subscriptions, invoices…),
state machines in `src/semantics/states.ts`, money/billing/ledger engine in `src/engine/`.

## Core abstractions

Event, Action (transaction commit), projected state, Plan (provider calls, idempotency keys,
destructive flags, conflicts, `requiresApproval`), review decision bound to a hash of the ordered
pending action ids, fork (snapshot base → divergence diff → optional push).

## Execution model

SDK → `world-stripe serve` (`http://127.0.0.1:<port>`, keys from `POST /_twin/app-credentials`) →
handler → action appended → projection → response. `applyPlan`: recompute approval requirement,
refuse a stale base, lease, push each call with phases.

## State model

Append-only JSONL logs on disk under `.volter/world/`, projected into a read model.

## Evidence

- Probe passed: second refund rejected with `amount_too_large`; six coherent event types.
- Boots only with a TypeScript runtime (`node --experimental-strip-types`), because the `bin` is a
  `.ts` file without a shebang.
- No test suite ships in the tarball; testing maturity UNVERIFIED.

## Reusable pieces

Apache-2.0, so legally usable with NOTICE/attribution. **Strategically risky:** the kernel source
isn't public, it's days old with ★0, and Volter sells the same plan → review → push loop we would
build. Treat it as the main competitor and as an **architectural reference** (hash-bound approval,
stale-base refusal, push ledger), not as a dependency.

## Missing capabilities

Seeded fault injection (only Stripe test-card declines), webhook delay/duplicate/reorder, a
scenario/invariant engine, a CI-failure → local replay command.
