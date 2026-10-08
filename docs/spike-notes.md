# Milestone 1 spike notes: dry-run → diff → approve → apply

Started and finished 2026-10-08 (day 1 of the 3-day timebox). Twin: `refund_path` (test scaffolding, no
vendor vocabulary). Code: `src/agent_staging/{dryrun,approval,apply,runlog,session,spike}.py`.
Tests: `tests/test_spike.py`, 10 passing.

## What approval protects against today

**In milestones 1–5, approval defends against a buggy agent (one that proposes more than intended). It does not yet defend against a hostile agent with shell access; that requires sandboxing the agent and signing approvals, which land in milestone 6. Do not deploy this against an agent you don't trust to run your own code.**

Why: any process running as the same OS user can open a pseudo-terminal and type the confirmation
(our own test does exactly that), and it can write `.staging/approvals/` directly. The typed
confirmation stops an agent that *proposes the wrong thing*; it does not stop an agent that
*attacks the approval step*. See open issue 2.

## Success criterion: met

> An agent runs, proposes three refunds, the diff shows 2 valid + 1 skipped, a human types a
> confirmation, the 2 are applied to the twin, and the run is logged.

`test_success_criterion_end_to_end` does exactly this. The confirmation is typed into a real
pseudo-terminal that is the CLI's controlling terminal, so the approval path isn't mocked. The
plan the human sees:

```
PLAN plan-034d7dcddd0f   base prod-0001   run run-6804740845f8

  + Refund $120.00 on charge-0001 to cust-0001 <ana@example.com>
      ~ charges charge-0001: amount_refunded: 0 -> 12000
      + refunds refund-0002
  + Refund $80.00 on charge-0002 to cust-0002 <ben@example.com>
      ~ charges charge-0002: amount_refunded: 0 -> 8000
      + refunds refund-0003
  ~ SKIPPED  Refund the remainder on charge-0003 to cust-0003 <cy@example.com>: charge charge-0003 is already fully refunded

Summary: 3 proposed · 2 to apply ($200.00) · 1 skipped · 0 destructive · 0 irreversible · 0 blocked
Hash:    sha256:034d7dcddd0fa8a6e9c871c0fd43e507cba0b326c7abdf59b4288462a4a1ff5f
```

Then `approve 034d7dcd` → `APPROVED`, then apply → `prod-0001 -> prod-0002`, two refunds land,
charge-0003 is untouched, and the run log (run.start, 6 calls, changes, plan.created,
plan.decision, 2 × apply.call, apply.done) verifies as an intact hash chain.

## Pre-task: can a change be traced to the call that made it? Yes, natively

Verified against Seahaven 0.5.0 before writing the plan builder. Every change-log record carries
`i`, the ordinal of the call that made it. The call log at index `i` has `{tool, arguments,
error, tool_error}`. A call that raised a `ToolError` leaves no changes. Joining on `i` is enough to
render "Refund $120.00 on charge-0001 to cust-0001", **so no correlation-ID layer is needed inside
one world.**

Caveats:
- Writes made with no call in flight (`inst.bulk()`) have `i = None`. The plan marks them
  BLOCKED, and a blocked plan can't be approved (tested).
- Not yet verified: the same join over Seahaven's HTTP path, and under composition, where nested calls
  share their parent's ordinal per its docs. Re-check both at milestone 2. A run-level correlation
  id will still be needed across several worlds or real systems.

## Decisions

**1. How the diff is rendered.** One line per *intended operation*, written by the world
(`describe()`), with the row changes indented under it. The money figure comes from the **rows
written**, not the arguments, so "refund the remainder" shows $120.00, not "remainder". Refused
calls appear as SKIPPED with the tool's own error. Writes that no declared mutating tool explains
appear as BLOCKED. A summary line gives counts, the money total, destructive and irreversible
counts, and the full hash.

**2. Where approval happens.** In a separate command, `staging approve <plan>`, on the
**controlling terminal** (`/dev/tty`), never stdin. The human must type `approve <first 8 hex of
the hash>`, which they can only know by having the plan on screen; anything else records a
rejection. The record says `actor.kind = "human"`, and there is no other kind and no bypass flag
(Volter gap 1). The agent only ever gets `Session.call(tool, **args)`. Piped input with no terminal
is refused (tested).

**3. What the plan hash covers.** SHA-256 of the canonical JSON of the whole plan body: format
version; world name, version and schema hash; base fixture id and its file SHA-256; every
operation (call ordinal, tool, **all arguments**, the rendered sentence, amount, destructive and
irreversible flags, every row change before and after); every skipped and blocked item; the summary.
So approval covers "refund $120.00 to cust-0001", not just a list of ids (Volter gap 2, closed).
Editing anything in the plan file voids the approval (tested). Apply recomputes the hash from
the file, never trusts a stored field.

**4. How apply works.** It **re-executes exactly the approved calls** on a fresh copy of current
production, then requires the rows they changed to equal the plan's rows. Only then is the result
frozen as the new production fixture and HEAD moved. Any mismatch or tool refusal discards the
instance, leaving production unchanged. Re-executing, rather than writing the rows directly, means the world's own
rules (no over-refund) run again at apply time. Columns set from the clock (`refunds.created_at`)
are declared **volatile** per world and excluded from the comparison, because a later apply
legitimately stamps a later time.

**5. What "stale" means.** Per row the plan touches, not per account. If production HEAD is still
the plan's base fixture (and its file hash matches), it's not stale by construction. Otherwise
the plan is stale if a column it updates no longer holds the value it saw, a row it inserts already
exists, or a row it deletes is gone. Changes to rows the plan doesn't touch are allowed: a new
customer elsewhere doesn't block the apply (tested).

## Open issues found by the spike

1. **Generated ids aren't stable across bases.** refund_path numbers rows by counting, so an
   unrelated refund in production makes the replayed refunds collide with existing ids, and apply
   aborts (safely; `test_unrelated_refund_shifts_ids_and_apply_aborts_safely`). Real vendors
   generate opaque ids anyway, so ids the plan *creates* can't be compared literally. Decide in
   milestone 6: treat created ids as placeholders and map them, rather than requiring equality.
2. **The terminal is not yet a security boundary.** Any process running as the same OS user can
   allocate a pseudo-terminal (our own test does), and the `approvals/` directory is writable by
   that user, so an agent with shell and filesystem access to `.staging/` could forge approval.
   Gaps 1 and 3 are closed in the *interface*, not against a hostile agent on the same account: so
   today approval defends against a buggy agent only. Needed: run the agent in a separate sandbox
   or OS user with no access to `.staging/`, and sign approvals with a key the agent can't read
   (milestone 6 and threat model).
3. **World specifics are hard-wired.** `dryrun.py` and `apply.py` import `refund_path` directly
   (mutating tools, `describe`, volatile columns). These become methods on the world adapter behind
   the `TwinKernel` protocol (milestone 2).
4. **Apply is atomic only because the target is a twin.** Freezing a fixture is all-or-nothing.
   Against a real system, calls can half-succeed, so apply will need a per-call push ledger with
   idempotency keys and confirmation (Volter's push-ledger idea).
5. **Test harness noise.** Python 3.14 warns that `forkpty()` in a multi-threaded process may
   deadlock. The child calls `exec` immediately, so the warning is filtered in the tests.

## Planted for milestone 6 (not built)

- **Id mapping:** plans will name created objects by what they are ("the refund for
  charge-0001"), and apply will resolve each name to whatever id the system returns, so the kernel
  interface must not assume ids are stable across bases.
- **Signed approvals:** `staging approve` will sign `{plan_id, plan_hash, decision}` with a key
  the agent can't read (macOS Keychain or a file owned by another OS user), so the approval
  record format must carry a `signature` field from milestone 2 on, even while it is empty.

## Throwaway vs. keep

Keep: the run log, the plan format and hashing, the approval rules, the stale definition and the
apply-and-verify order. Rework: everything that names `refund_path` (issue 3) and the counter ids
(issue 1).
