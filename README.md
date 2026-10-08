# Agent Staging Layer

Staging, simulation and dry-run infrastructure for AI agents that write to SaaS systems.
Stripe first.

**Status: milestone 1 spike done** (dry-run → diff → approve → apply on a test world).
Not a product yet: no Stripe twin, no faults, no replay. Read, in order:

1. [docs/product-thesis.md](docs/product-thesis.md)
2. [docs/problem-map.md](docs/problem-map.md)
3. [docs/research/repository-matrix.md](docs/research/repository-matrix.md)
4. [docs/architecture-decision-record.md](docs/architecture-decision-record.md) (proposed)
5. [docs/licensing.md](docs/licensing.md)
6. [docs/spike-notes.md](docs/spike-notes.md)

## Try the spike

```sh
uv run staging spike seed
uv run staging spike run            # prints the plan
uv run staging approve <plan-id>    # type the confirmation at your terminal
uv run staging apply <plan-id>
uv run pytest
```

Licence: Apache-2.0 (see LICENSE and NOTICE).
