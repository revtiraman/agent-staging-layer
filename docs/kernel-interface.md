# The twin kernel interface

Status: milestone 2, 2026-10-08. Code form: `src/agent_staging/kernel.py`.

This is the contract between the staging layer (dry-run, plan, approval, apply, run log,
scenarios) and a twin. It describes **what** a twin must do, not how. Read it before adding
a twin, and before changing anything above the line it draws.

## Scope: exactly two twins

The interface exists for two implementations: `refund_path` (test scaffolding) and the
Stripe twin (milestone 8). Every part of it is needed by at least one of those two. A
change justified only by a twin we have not committed to build does not go in. When a third
twin is committed to, it can change this document then.

## What a twin provides

| Part | Meaning | Why the staging layer needs it |
| --- | --- | --- |
| `state(id)` | A saved state: world name and version, schema hash, file hash, the twin's time when saved | The plan records exactly which state it was computed from; apply refuses if that file changed |
| `open(state, start, seed, delivery_delay)` | A **branch**: a private running copy of a state | Dry-runs and applies never touch the saved state until a branch is frozen |
| `is_mutating(tool)` | Whether a tool is allowed to change state | A tool that writes without being declared mutating blocks the plan |
| `is_irreversible(tool)` | Whether the real-world effect can't be undone (an email sent) | Shown on the plan; the human sees it before approving |
| `assigned_columns(table)` | Which columns the system fills in, and with what: `id` or `time` | See rule 2 |
| `describe(branch, tool, args, changes)` | One sentence a person approves, and the money it moves (minor units + currency) | The diff a human reads is these sentences, not row dumps |

## What a branch provides

| Part | Meaning |
| --- | --- |
| `call(tool, **args)` | Run a tool. A refusal raises `TwinToolError(code, message)`, as a vendor API returns an error |
| `calls()` | Every call in order: position `i`, tool, arguments, error code and message |
| `changes()` | Every changed row: `i` (which call), table, insert/update/delete, key, before, after |
| `row(table, key)` | Read one row by key, for describing and stale checks. Not a tool, not logged |
| `now()` | The twin's current time |
| `advance(duration)` | Move time forward, delivering due events (rule 1) |
| `pending()` / `subscribe(handler)` | Events not yet delivered / where delivered events go |
| `freeze(id, description)` | Save the branch as a new state |

## The three rules

### 1. The harness owns time

- A branch starts at its state's saved time (or a later `start`). It moves **only** when the
  harness calls `advance(duration)`. Tool calls do not move it.
- World code reads time only through the kernel. Under Seahaven that is `ctx.clock`, which
  also backs SQL's `'now'` and `CURRENT_TIMESTAMP`. A test scans the twins for wall-clock reads.
- `advance(d)` delivers every event due at or before `now + d`, in (due time, emission order),
  with the clock set to **each event's own due time** while its handler runs. So a webhook
  due at T+40 is handled at T+40 even if the harness jumps from T+10 to T+50.
- Durations are never negative. Time is millisecond precision.
- Same state, same seed, same calls, same advances: same changes, same events, same delivery
  times. Nothing in a twin may depend on the wall clock or on unseeded randomness.
- Only the harness can move time. The agent's interface (`Session`) has `call` and `on_event`,
  and nothing else.

Why it matters: the bugs this product exists to catch are sequence bugs across time
(refund at T+0, webhook at T+40, agent retries at T+10). They can only be reproduced if time
is an input to the run, not something the run reads.

### 2. Ids a branch assigns are not stable

An id a twin mints on a branch (a new refund's id) is only valid on that branch. Production
may assign a different one: our counters shift when unrelated rows are added, and a real
vendor's ids are random. So:

- Twins declare which columns they assign (`id` or `time`). The plan records this.
- `describe()` names objects by references that **existed before the call** ("refund on
  charge-0001 to cust-0001"), never by an id the call itself assigned.
- Nothing above the kernel may treat an assigned id from a dry-run as the id production will
  use. Today apply compares them literally and **aborts** on a mismatch, which is safe but
  too strict. Milestone 6 replaces that with name-based mapping ("the refund for
  charge-0001" → whatever production returned).
- Clock-assigned columns are excluded when apply checks its result against the plan.

### 3. Every change belongs to exactly one call

Each changed row carries `i`, the call that made it. A change with no call (`i = None`) is
unexplained, and a plan containing one is BLOCKED and can't be approved.

This rule has a consequence that was **verified, not assumed**, at milestone 2: Seahaven's
own HTTP serving (`seahaven.http`) runs each request inside `bulk()`, where changes get
`i = None` even when the handler delegates to a tool
(`test_a_tool_call_inside_bulk_loses_its_attribution_and_blocks_the_plan`). Kiln's
stripe_world serves its Stripe API that way. So a Stripe twin served over HTTP for real
SDKs must turn **each HTTP request into one top-level kernel call**. Otherwise every write it
makes is blocked. That's a milestone 8 design constraint.

## Events

A twin that sends notifications (webhooks) declares an outbox: rows a call inserts there are
events. Because the outbox row is written in the same call as the change it reports, a
refused call emits nothing, and the event appears in the plan's row changes like any other
write. Delivery is the harness's job: `delivery_delay` after emission today, a seeded schedule
of delays, duplicates and reorders in milestone 3. Pending deliveries live on the branch, not
in saved states.

## Errors

`TwinToolError(code, message)`: `code` is stable and machine-readable (`already_refunded`),
`message` is for people. A twin's internal bug is reported as code `internal_error`, never
as a tool refusal.

## Why Seahaven

We build both twins on Kiln's Seahaven (MIT, pinned `0.5.0`) because, from
`docs/research/seahaven.md`, it already gives the three things this contract needs most:

1. A **change log with per-row before/after and the call ordinal** (rule 3). That is what lets
   a plan say "Refund $120.00 on charge-0001" instead of "a row changed".
2. **Branchable saved states** (fixtures) with file hashes, which is what dry-run and stale
   checks are built on.
3. **One clock object behind every door**, including SQL. Moving its instant (rule 1) moves
   time for tools, SQL defaults and saved states alike.

What Seahaven does **not** give, and the kernel adds: moving the clock on demand (Seahaven's
modes are fixed, tick, running and wall), events and their delivery, the id-assignment
declaration, and attribution over its HTTP path. Moving the clock touches one private member
(`Clock._start`), the same kind of access `seahaven.http` makes itself. It is pinned and
tested, and it is the first thing to check on any Seahaven upgrade.

The interface does not expose Seahaven types. A twin that is not built on Seahaven (if
milestone 8 writes its own Stripe subset) can implement it directly.
