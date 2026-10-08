# Volter fork / plan / lease: close read

`@volter/twin` 0.1.3, `src/fork.ts` (268 lines), `src/plan.ts` (298), `src/lease.ts` (95). Read in
full on 2026-10-08. Reference only: nothing here is copied (see licensing.md).

## What it does

**Fork** (`forkTwin`): copies the base twin's event log into an isolated root and records a
`baseline` of every resource at fork time. `forkDivergence` diffs current resources against that
baseline field by field (`created`, `changed: {field: {before, after}}`), ignoring `id`, `type`,
`updatedAt`. Git-like operations over the *action* log: `cherryPickActions`, `mergeForks` (refuses
different base event ids unless `force`), `resetFork` (appends reverts, keeps history), `rebaseFork`
(appends fresh vendor events, then reports actions whose preconditions now fail). `auditForkNoRealWrites`
reports whether anything reached the vendor via the egress ledger or the push ledger. The comment
admits an earlier version checked only one channel and reported "a fabricated green".

**Plan** (`buildApplyPlan`): pending actions → provider calls via a vendor `ActionMapper`
(operation, input, idempotency key, `destructive`, expected confirmation event); actions whose
preconditions fail against projected state become `conflicts`. `transactionSetId` =
`sha256([service, ordered action ids])[:16]`. `recordPlanReview` refuses if the pending set changed
since the reviewer looked; a rejection needs a reason. `applyPlan`: recompute whether approval is
needed (never trust the stored bit), refuse a stale base (remote ref advanced), take a lease, push
each call through phases via an injected `writeFn`, release.

**Lease**: at most one active lease per (provider, remote ref), check-and-write inside a
cross-process file lock; all times supplied by the caller (deterministic).

## Ideas worth taking (re-implemented, not copied)

1. Approval binds to an exact, ordered set; any change voids it.
2. Recompute "needs approval" at apply time from the plan's own content.
3. Refuse a stale base before any side effect or lock.
4. Single-writer lease around apply.
5. An audit that checks *every* channel a real write can travel through.
6. Caller-supplied timestamps everywhere → deterministic tests.

## Gaps we can exploit

| # | Observation (from the code) | Consequence | Our design |
| --- | --- | --- | --- |
| 1 | `PlanReviewRecord.actor.kind` accepts `'agent'`; `recordPlanReview` only checks the actor id is non-empty | An agent can approve its own plan | Approval only from a human channel (TTY typed confirmation or a signed CI approval); the agent's credentials can't reach the approve path (threat model #9) |
| 2 | `transactionSetId` hashes **action ids only**, not the mapped `providerCalls` (inputs, amounts) | What was approved is "these ids", not "refund $120 to cus_123". A mapper change alters the real calls without voiding approval | Hash the full canonical plan: operation, target, every input field, money totals |
| 3 | Plans and reviews are plain JSON/JSONL files; nothing is signed (the code comments on this risk for `requiresApproval`) | Anyone who can write the directory can append an "approved" line | Hash-chained run log; approval record includes the plan hash and the previous record's hash; optional signing key in CI |
| 4 | `planRequiresApproval` = `transactions.length > 0`, while the `buildApplyPlan` doc says approval is needed "if any call is destructive or any conflict exists" | Doc and code disagree; there's no risk tiering | Policy: thresholds (amount, count, destructive, irreversible) decide auto-approve vs human; `irreversible` (emails sent) shown separately |
| 5 | Stale-base check is per remote ref: any vendor change invalidates the plan | Coarse; in busy accounts, plans go stale constantly | Per-object preconditions on only the objects the plan touches |
| 6 | No fault injection, no time model beyond caller-supplied stamps, no scenario/invariant runner, no CI replay loop | Their dry-run shows *what* will change, not *whether the agent behaves under bad conditions* | That is our wedge: seeded faults + virtual-clock webhooks + invariants + `staging replay` |

Unverified: whether `@volter/world` (the product layer above this kernel) closes gaps 1–3; its
source is not in the packages we inspected.
