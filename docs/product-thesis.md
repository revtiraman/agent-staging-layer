# Product thesis: Agent Staging Layer

Status: Phase 0, written 2026-10-08, before any product code. Claims about other projects are
backed by `docs/research/`; anything not verified is marked UNVERIFIED.

## 1. The problem

AI agents now take actions in payment, commerce and support systems: refunds, order edits, ticket
updates, emails. Those actions have side effects that can't be undone by re-running a test, and
the failures that matter only appear under realistic **state** (what already happened to this
charge), **timing** (a webhook that arrives 40 s late) and **concurrency** (a person editing the
same record). Teams have no safe place to see those failures before production does.

## 2. Who has it

- Engineers shipping agents that call Stripe, Shopify or Zendesk on a customer's behalf
  (support, billing, ops agents).
- The person accountable when the agent refunds twice: an engineering lead or a platform team.
- Teams changing prompts, tools or models weekly, who need a regression gate on behaviour, not
  just on code.

## 3. Why existing tools are insufficient

Verified in `docs/research/repository-matrix.md`:

| Tool type | Example | Gap for agents |
| --- | --- | --- |
| Vendor mock | stripe-mock | Stateless by its own README: "does not attempt to reproduce the behavior of the real Stripe API at all" |
| Vendor test mode | Stripe sandbox | One shared account, no reset, rate-limited, can't inject faults, not reproducible |
| Stateful emulators | vercel-labs/emulate, localstripe | Stateful, but no deterministic fault injection, no replay, no dry-run; emulate's Stripe has no refunds (probed) |
| Agent-world frameworks | Seahaven + stripe_world, Volter twins | Strong stateful twins (both passed our refund probe). Seahaven explicitly does not inject faults or advance the clock; neither ships a seeded fault model or a CI failure → local replay loop |
| Agent eval benchmarks | tau2-bench, AppWorld, AgentDojo | Fixed benchmark domains, not your Stripe account; built to rank models, not gate your PR |
| Plan/approve for agents | tfminder (Terraform), Terfyn (capabilities), Volter `plan/review` | Approval exists, but not over a diff of SaaS side effects computed from a branch of production state, except partially in Volter |

**Honest finding:** a stateful Stripe twin on its own is no longer a differentiator. At least two
2026 projects already have one. The product has to be the layer around the twin.

## 4. Why stateful twins matter

An agent's bug is usually a *sequence* bug: refund → webhook not yet arrived → agent sees no
refund → refunds again. A stateless mock answers each call in isolation, so the second refund
"succeeds" and the test passes. Only a twin that remembers the first refund can reject or record
the second, and only then can an invariant like `refund_total <= charge_total` be checked.

## 5. Why dry-run matters

Testing proves the agent's *policy* is safe on synthetic data. Dry-run answers a different
question: "what exactly will this run do to **our** production state, right now?" Forking the
records a plan touches into a twin, running the agent there and showing the diff turns an opaque
batch of API calls into something a person can approve in 30 seconds.

## 6. Why deterministic replay matters

Agent failures under faults are timing-dependent. If CI fails once and nobody can reproduce it,
the test is ignored. A run log with the seed, the fault schedule, every request/response and every
state change lets `staging replay <run-id>` reproduce the same failure locally, which makes fault
testing usable. (The model's own sampling is not deterministic; replay re-feeds the recorded tool
responses and reproduces the environment exactly. See ADR D.)

## 7. Why CI integration matters

Agents change through prompts, tool descriptions and model upgrades, often with no code diff. A
CI step that runs scenarios against twins on every PR is the only place those changes get
checked before users see them. CI is also the distribution channel: one `uses:` line.

## 8. Smallest compelling MVP

One agent, one twin, one failure ordinary tests miss:

1. Stripe twin: Customers, PaymentIntents, Charges, Refunds, Events, Webhooks, stateful.
2. Seeded faults: webhook delay, duplicate, reorder; 429 with Retry-After; timeout.
3. Hard invariants in code, checked after every step.
4. Run log, then `staging replay <run-id>` reproduces the failure.
5. `staging dry-run`: snapshot, branch, run the agent, diff, approve, apply (to the twin; a real
   Stripe sandbox target only behind an explicit flag).
6. GitHub Actions job that fails the PR on a critical invariant and uploads the replay artifact.
7. A debugging-first replay UI.

Demo: 2,000 orders, 40 s webhook delay, duplicate customer request, duplicate refund caught,
replayed, then a dry-run of the corrected batch approved and applied.

## 9. What we will not build (MVP)

- Shopify and Zendesk twins (designed for, not built).
- Hosted/multi-tenant cloud, accounts, billing.
- Live sync from production Stripe (dry-run seeds from an exported snapshot or a sandbox account).
- Kubernetes, Kafka, vector DBs, microservices.
- LLM "soft invariants" beyond one optional, clearly separated judge.
- Full Stripe API coverage. Only the refund money path.

## 10. Long-term platform

Continuous agent regression testing: a library of twins (Stripe, Shopify, Zendesk, Salesforce),
production-state branching for any of them, a policy and approval engine for agent side effects,
model/agent version comparison on the same seeded scenarios, and audit logs of what every agent
was allowed to do and did. Roadmap detail: `docs/roadmap.md` (post-MVP).
