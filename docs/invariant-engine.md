# The invariant engine

Status: milestone 4, design approved 2026-10-09. Code: `src/agent_staging/invariants.py`;
refund_path's invariants: `src/agent_staging/twins/refund_path_invariants.py`.

An invariant is a rule a run must never break. The engine checks every **hard** invariant after
every step of a run, records each failure as a log record, and fails the run. **Hard means pure
and deterministic: no LLM, no I/O, no clock, no randomness.** Soft (judged) invariants come in
milestone 5, in a separate layer that never gates a run or an apply.

## Four inputs

Every invariant is a pure function `check(state, changes, faults, intent) -> violations`. All four
inputs are rebuilt from the run log (plus the hash-pinned base state), so a failure can always be
reproduced from the log alone.

| Input | What it is | Built from |
| --- | --- | --- |
| `state` | Read-only rows at the evaluation point | The base state (a saved state, checked against its file sha256) plus every logged change up to that point |
| `changes` | Ordered row changes, each with the call `i`, the request `n` and the twin time that made it | `change` records |
| `faults` | The harness timeline: every fault decision (kind, key, seed), every request (outcome, `reached_twin`, and the twin's result even when the agent saw a timeout), every webhook delivery | `fault`, `call` and `event.delivered` records |
| `intent` | What the run was asked to do: the scenario's declared requests ("refund $50 on charge-0001") | the `intent` record at the start of the run |

**Why `faults` is a timeline, not only fault decisions.** A fault only means something relative
to the request it hit. `no_retry_after_lost_response_timeout` needs fault `request/1` *and* the
later request that retried it.

**Why `intent` exists.** A faithful twin refuses an over-refund, as Stripe does and as
refund_path's `CHECK amount_refunded <= amount` does. So the agent's real bug after a
lost-response retry is not "refund total > charge total", which can't happen. It is two valid
$50 refunds when the customer asked for $50. Only a comparison with what was asked can catch it.
**Never weaken a twin to make an invariant fire.** `intent` will later also carry
`allowed_actions`, `forbidden_actions` and `consents`. Their schema is deliberately not designed
yet: the record has a format version, and new fields are added beside `requests`.

## Invariants

- Each has a `name`, an integer `version` (bumped whenever its logic changes) and `check()`.
- Invariants belong to a twin, because they name its tables. The engine is generic.
- A violation names a **subject** ("charge charge-0001"). It is reported once per (invariant,
  subject), at the first step it appears, with **evidence**: rows (table + key + values), fault
  keys and request numbers, all sorted, so evidence is deterministic.

## When they run

- A step is a `call` record (one agent request, whatever its outcome) or an `event.delivered`
  record. Every invariant is evaluated after every step, and once more at `steps.end`, after any
  changes made outside a call are logged.
- **Live runs and replay use the same code path.** The engine is fed log records as they are
  appended (live) or as they are read back (replay), in their canonical JSON form. A live result
  and a replay result can't differ because they were computed differently.

## Failures are records

```
invariant.failed  {invariant, version, subject, step, message, evidence: {rows, faults, requests}, seed}
run.end           {outcome: "passed" | "failed", violations: [log seqs of invariant.failed records]}
```

`step` is the log seq of the record after which the invariant first failed. `seed` is the run's
fault seed. The run's start lists the active invariants and their versions
(`invariants.active`). The exit code of a run is derived from `run.end`, never the other way
round.

## Replay

`staging replay <run>` verifies the log's hash chain, rebuilds the four inputs from the log and
the hash-pinned base state, re-evaluates every invariant that was active in the run, and compares
the result with the recorded `invariant.failed` records. It does not re-run the agent or re-draw
a single fault. Re-executing a run from its recorded decisions is milestone 5.

Outcomes, written to a replay record (`runs/<run>/replays/`):

- **Same outcome, same invariant code:** reproduced; exit 0.
- **Same outcome, different invariant code:** replay succeeds and notes the version mismatch in
  the replay record; exit 0.
- **Different outcome, different invariant code:** replay reports "invariant X version A vs
  version B produced different results at step N" and exits non-zero.
- **Different outcome, same invariant code:** the engine is not deterministic, which is a bug;
  replay reports "invariant X version A produced different results at step N on replay" and
  exits non-zero.
- An invariant that was active in the run but no longer exists is reported, with a non-zero exit.
  Invariants that exist now but weren't active in the run are listed, not evaluated.

## The first two invariants (refund_path)

1. **`refunded_le_requested`:** for every charge, the amount refunded *during the run* must not
   exceed what `intent` requested for it. A charge with no request has a requested amount of 0.
   "Refund the remainder" means the unrefunded amount in the base state.
2. **`no_retry_after_lost_response_timeout`:** after a request to a mutating tool timed out
   having reached the twin (`lost_response`), the agent must not send the same tool to the same
   target again without first reading that target. The timeout said "may or may not have been
   applied", so a read is the only way to know. A retry after a read is the agent's informed
   choice, and `refunded_le_requested` still judges its result.
