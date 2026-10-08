# Agent Staging Layer

Staging, simulation and dry-run infrastructure for AI agents that write to SaaS systems.
Stripe first.

**Status: milestone 4 done** (1: dry-run → diff → approve → apply on a test world; 2: twin
kernel interface and a harness-owned clock; 3: seeded, logged faults; 4: hard invariants over
state, changes, faults and intent, with failures as log records and `staging replay` that
re-evaluates them from the log alone). Not a product yet: no Stripe twin, no re-execution replay.

> **Security status:** In milestones 1–5, approval defends against a buggy agent (one that proposes more than intended). It does not yet defend against a hostile agent with shell access; that requires sandboxing the agent and signing approvals, which land in milestone 6. Do not deploy this against an agent you don't trust to run your own code.
 Read, in order:

1. [docs/product-thesis.md](docs/product-thesis.md)
2. [docs/problem-map.md](docs/problem-map.md)
3. [docs/research/repository-matrix.md](docs/research/repository-matrix.md)
4. [docs/architecture-decision-record.md](docs/architecture-decision-record.md) (proposed)
5. [docs/licensing.md](docs/licensing.md)
6. [docs/spike-notes.md](docs/spike-notes.md)
7. [docs/kernel-interface.md](docs/kernel-interface.md)
8. [docs/invariant-engine.md](docs/invariant-engine.md)

## Try the spike

```sh
uv run staging spike seed
uv run staging spike run            # prints the plan
uv run staging approve <plan-id>    # type the confirmation at your terminal
uv run staging apply <plan-id>
uv run pytest
```

Licence: Apache-2.0 (see LICENSE and NOTICE).
