# The invariant engine

Status: milestone 4, design approved and built 2026-10-09. CLI: `staging replay <run>`. Code: `src/agent_staging/invariants.py`;
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
a single fault. Re-executing a run from its recorded decisions is the next section.

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

## Re-execution replay (milestone 5)

**Choice: re-execute the harness and re-feed the recorded agent decisions. The environment is
deterministic from the log; the model is not, so replay re-executes the harness and re-feeds
recorded decisions.** Running a live agent again would test the model's sampling, not the run,
and its result can't be guaranteed to match. If that is ever built, it will be an explicit flag
(`staging replay --live-agent`), documented as not guaranteed to match, and never the default.

`staging replay --execute <run>`:

1. Verifies the log's hash chain, then opens a fresh branch of the hash-pinned base state at the
   recorded start time, with the recorded twin seed.
2. Answers every fault decision from the log's `fault` records, looked up by key. Nothing is
   re-drawn from the seed. If the harness needs a decision the log doesn't have, that is a
   divergence.
3. Re-issues the agent's recorded requests (tool and arguments) in log order, in the same place:
   at top level, or inside the webhook delivery whose handler made them. The recorded clock
   advances are replayed between them.
4. Writes a new run log (`runs/<run>/replays/execute-NNN/log.jsonl`), checks the hard invariants
   live with the same engine, and compares the new log with the original record by record. Wall
   clock times and hashes are excluded, because they can't match. Everything else must: call
   results and errors, row changes, fault decisions, deliveries and their times, and invariant
   failures.
5. Verdict: `reproduced`, or `diverged at seq N` with the record type and the first field that
   differs.

## Soft invariants (milestone 5)

Soft invariants are advisory, like a linter. Two rules are **enforced in code**, not by
convention:

- **Outcome is hard-only.** A soft invariant returns `list[AdvisoryNote]`, a different type from
  `Violation` with a different code path. The harness writes `run.end` (outcome and exit code,
  computed from hard failures only) **before** it runs any soft invariant, so a soft result can't
  reach the outcome even by mistake.
- **`apply` never reads soft records.** `apply`, `approval`, `dryrun`, the hard engine and replay
  don't import the soft module. A test scans their imports and fails if any of them does.

**Judges.** A soft invariant may ask a judge (an LLM or anything else) for an opinion. The judge's
input and full output (verdict, rationale, model) are recorded in the `advisory.note` record.

**Replay never calls a judge.** It reads the recorded judge output from the log and reports it.
That keeps replay to "re-evaluation from the log alone", and a model giving a different answer
today says nothing about what the run did. Re-execution replay copies no advisory records and
re-judges nothing. It reports the recorded notes beside its verdict. Asking a judge again is a
separate, explicit action, if it is ever built.

## The first two invariants (refund_path)

1. **`refunded_le_requested`:** for every charge, the amount refunded *during the run* must not
   exceed what `intent` requested for it. A charge with no request has a requested amount of 0.
   "Refund the remainder" means the unrefunded amount in the base state.
2. **`no_retry_after_lost_response_timeout`:** after a request to a mutating tool timed out
   having reached the twin (`lost_response`), the agent must not send the same tool to the same
   target again without first reading that target. The timeout said "may or may not have been
   applied", so a read is the only way to know. A retry after a read is the agent's informed
   choice, and `refunded_le_requested` still judges its result.

## Found while building

- **Seahaven's update records carry only the changed columns** in `after` (and `before`). State
  is rebuilt by merging an update onto the row it changes, not by replacing the row
  (`test_state_applies_partial_updates_onto_the_base_row`).
- **The demo run fails both rules at request 2**, which is what should happen:
  `refunded_le_requested` on the money ($100.00 refunded, $50.00 requested), and
  `no_retry_after_lost_response_timeout` on the behaviour (a blind retry after a lost response).
  Two $25 refunds that were both asked for leave the same kind of rows and fail neither rule.
  Only the fault log tells them apart (`test_it_needs_the_fault_record_not_just_state`).
