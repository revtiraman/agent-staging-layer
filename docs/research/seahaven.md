# Kiln-AI/Seahaven

`github.com/Kiln-AI/Seahaven` · MIT · Python ≥3.14 · ★11 · created 2026-09-11 · studied at e076fca.
Runtime deps: `apsw` (SQLite), `pydantic`, `pyyaml`; optional `openenv`, `mcp`.

## Architecture

A framework for "worlds": a world is a SQLite schema plus Python tool functions. Seahaven provides
the rest: instances, fixtures, a controlled clock/ids/randomness, a change log, composition of
worlds, and serving (in-process, HTTP via Starlette, MCP, OpenEnv, a web console).
`src/seahaven/` is about 8,750 lines across `world.py`, `instances.py`, `fixtures.py`, `clock.py`,
`ids.py`, `changes.py`, `state.py`, `composition.py`, `http/`, `mcp/`, `openenv/`, `lint/`.

## Core abstractions

- `World(name, version, schema, state_format)` with `@world.tool` functions taking `Ctx`.
- `Ctx`: `ctx.db` (SQLite), `ctx.clock`, `ctx.ids`, deterministic randomness.
- **Fixture**: a frozen database file; instances are *copies*, never opened in place.
  Fixtures record their parent, so a fixture forked from a fixture is a branch.
- **Instance**: `world.instance(fixture, seed=...)`, private SQLite per run.
- **Change log**: `inst.change_log()`, one record per row per call, with call ordinal, table,
  op, key, and the row **before and after**.
- HTTP handlers: a world can serve a vendor's REST API (`http_apis.md`); middleware wraps handlers
  (stripe_world uses it for idempotency, error envelopes).

## Execution model

Request → (HTTP route `/worlds/{id}/…` or a tool call) → middleware → handler/tool with `Ctx` →
SQL on the instance DB → change records appended → response. Each `{id}` is its own instance,
created on first use.

## State model

Relational SQLite per instance, `STRICT` tables. The clock has modes (`fixed`, `running`) and the
same fixture + seed + clock give the same ids, timestamps and results.

## Extensibility

A new SaaS = a new world (schema + handlers). Worlds compose: "MyCoWorld can include
StripeAPIWorld and ShopifyAPIWorld" (README). `extensions.md` documents protocol extensions
(worked example: XML-RPC).

## Testing

pytest, a pytest plugin, `seahaven check` lint that tells an author the exact fix. Local run:
2,113 passed / 21 failed (world-loading lint tests only) / 23 skipped.

## Missing capabilities (its own words, `concepts.md`)

"Seahaven does not put a time limit on a tool call, … mock a tool's response, move a clock
forward … If your eval needs one, build it into the harness around the world."
So: **no fault injection, no webhook delivery, no clock advance, no dry-run/approval, no CI
runner.**

## Reusable pieces

The whole package, as a dependency (MIT, attribution in licensing.md). No copying needed.

## Opportunity

Seahaven gives us the hard deterministic substrate: instances, fixtures as branches, before/after
change log. Everything the brief calls the differentiator sits in the harness Seahaven tells you
to build: seeded faults, webhook scheduler on a virtual clock, run log and replay, dry-run diff from
the change log, approval, CI. That is exactly our product.
