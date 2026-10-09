# Demo video script (draft 1)

Draft started 2026-10-09, after milestone 5. A 4-minute screen recording, terminal first. Each
segment is marked **BUILT** (can be recorded today), **PARTLY**, or **NOT BUILT**, with the
milestone that builds it. Nothing marked NOT BUILT goes in a recording until it exists.

## 0:00–0:15 · The failure (voice over a black terminal)

> An agent refunds $50. The request times out, but the refund went through. The agent retries.
> The customer gets $100. Every test passed, because no test had a timeout in it.

## 0:15–1:30 · Catch it, then reproduce it — BUILT except one command

1. Show the scenario: 2,000 seeded charges, one intent ("refund $50 on charge-1234"), and the
   fault profile with `at_request: {1: lost_response}`.
2. Run it. The terminal shows request 1 (`timeout`, `reached_twin: true`), request 2 (the retry),
   and then:
   `invariant.failed refunded_le_requested: charge-1234: $100.00 refunded during the run in 2
   refund(s); $50.00 was requested`, with the evidence (the charge row, two refund rows, fault
   `request/1`) and `run.end outcome: failed`.
3. Point out that the twin accepted both refunds, as Stripe would. The bug is the agent's.
4. `staging replay <run>` → `REPRODUCED` (re-evaluated from the log alone).
5. `staging replay --execute <run>` → `REPRODUCED` (environment rebuilt, every fault read back from
   the log, the agent's requests re-fed: the same failure at the same step).

**Gap:** there's no CLI command that runs this scenario yet. Today it runs from
`tests/test_invariants.py`. A `staging scenario run <file>` command (or a fixed `staging demo`)
is needed before recording. It's small and fits milestone 7's CLI work.

## 1:30–2:15 · Why it reproduces — BUILT

- `staging log <run>` with the fault records: each one has its seed and key
  (`request/1`, `webhook/3`), and is written before its effect.
- One sentence on the reorder rule: only deliveries due at the same instant are reordered, so
  replay never depends on how time was stepped.
- One line on the hash chain: edit any line and replay refuses (`not intact`).

## 2:15–3:15 · The fix, dry-run, human approval — PARTLY (milestones 6 and 8)

- The fixed agent passes the same scenario. BUILT: the "read after a timeout" agent passes.
- Dry-run of the corrected batch against production state, then the plan and diff, `approve
  <hash>` typed at the terminal, and apply. BUILT on refund_path. **On a Stripe twin: NOT BUILT
  (milestone 8).** Signed approvals and id mapping: NOT BUILT (milestone 6).
- Must be said on screen until milestone 6 lands: approval defends against a buggy agent, not
  a hostile one with shell access.

## 3:15–3:45 · The CI gate — NOT BUILT (milestone 7)

A PR that changes the agent's retry prompt, the GitHub Actions job failing on
`refunded_le_requested`, and the replay artifact downloaded and replayed locally.

## 3:45–4:00 · Close

Plain status, matching the site: what works, what's next, nothing implied.

## Before recording

- [ ] `staging scenario run` (or `staging demo`): runs the 2,000-charge scenario from the CLI.
- [ ] The Stripe twin (milestone 8) for the dry-run segment, or record that segment on
      refund_path and say so on screen.
- [ ] Milestone 6 (signed approvals, id mapping) before any claim about hostile agents.
- [ ] Milestone 7 for the CI segment.
