# Repository matrix

Collected 2026-10-08 with `gh api`, the npm/PyPI registries, shallow clones (commits in
`research/README.md`), local test runs and a black-box refund probe. Star counts and dates are as of
that day.

## 1. The named targets: what they resolve to

| Name in brief | Canonical source | Verdict |
| --- | --- | --- |
| @volter/twin | npm `@volter/twin` 0.1.3 (Apache-2.0). Its `repository` field points to `github.com/volter-ai/twin`, which returns **404** (private or deleted). Stripe pack: npm `@volter/twin-stripe` 3.0.2, source in `volter-ai/twin-packs-open` (Apache-2.0, ★0, created 2026-10-06) | FOUND (kernel source repo NOT FOUND; npm package only) |
| Seahaven | `github.com/Kiln-AI/Seahaven` (MIT, ★11, Python ≥3.14, created 2026-09-11). Not `i-am-shodan/Seahaven` (a C# test-data generator, last push 2023) | FOUND |
| Kiln-AI/stripe_world | `github.com/Kiln-AI/stripe_world` (★11, Python, created 2026-09-18). **No LICENSE file**; GitHub reports NOASSERTION because it detects only `THIRD_PARTY_LICENSES.md` | FOUND; licence problem |
| stunt | `github.com/Untargetable/stunt` (MIT, ★2, PyPI `stunt`): a mitmproxy addon to mock, modify, delay and throttle HTTP. Best match; UNVERIFIED that this is the project meant | FOUND (probable) |
| agent-emulate | npm `agent-emulate` links to `Vinniai/agent-emulate` (Apache-2.0, ★1, OAuth/OIDC emulation) but carries vercel-labs/emulate's description. Closest maintained equivalent: `github.com/vercel-labs/emulate` (Apache-2.0, ★1,880) | FOUND → evaluated vercel-labs/emulate |
| agentic-ci | `github.com/opendatahub-io/agentic-ci` (Apache-2.0, ★11; PyPI `agentic-ci`): runs coding agents in sandboxed CI | FOUND |
| agenteval-framework | No canonical repo by that name (`Subhadip0904/AgentEval-Framework` ★0). Closest maintained: `agentevals-dev/agentevals` (Apache-2.0, ★162, OTel-trace evals) | NOT FOUND → equivalent |
| ghostlab | `github.com/sajjadGG/Ghostlab` (MIT, ★2, PyPI `ghostlab`): end-to-end lab for agents and MCP servers | FOUND |
| AgentRails | `github.com/rforgeon/AgentRails` (★45, no SPDX licence, last push 2025-02-06): an agent-monitoring dashboard, not a safety rail | FOUND; not relevant |
| terfyn | `github.com/Terfyn/terfyn` (Apache-2.0, ★4, Go): capability plan/apply for agents | FOUND |
| tfminder | `github.com/MarckMorris/tfminder` (Apache-2.0, ★0, Python): MCP gate so agents can plan but not self-approve Terraform | FOUND |

## 2. Broader ecosystem (searched, not cloned unless stated)

| Project | Licence | ★ | Why it matters |
| --- | --- | --- | --- |
| stripe/stripe-mock (cloned) | MIT | 1,653 | Stripe's own mock; stateless by design |
| stripe/openapi | MIT | 504 | The source of truth for object shapes; used by stripe-mock, stripe_world and Volter |
| stripe/stripe-cli | Apache-2.0 | 2,197 | `stripe trigger`, `listen`: real webhook fixtures, sandbox conformance |
| adrienverge/localstripe (cloned) | GPL-3.0 | 242 | Stateful Stripe server with webhooks; copyleft |
| stripe-ruby-mock (cloned) | MIT | 997 | In-process Ruby mock |
| sierra-research/tau2-bench (cloned) | MIT | 2,189 | Policy + tools + DB + user simulator; the reference eval design |
| StonyBrookNLP/appworld | Apache-2.0 | 529 | Controllable world of apps for function-calling evals |
| ethz-spylab/agentdojo | MIT | 899 | Prompt-injection attack/defence environments |
| UKGovernmentBEIS/inspect_ai | MIT | 2,960 | Eval framework (tasks, solvers, scorers, sandboxes, log viewer) |
| Shopify/toxiproxy (cloned) | MIT | 12,394 | TCP-level fault injection (latency, timeout, reset) |
| SpectoLabs/hoverfly | Apache-2.0 | 2,522 | Record/replay HTTP service virtualisation |
| wiremock/wiremock | Apache-2.0 | 7,391 | HTTP stubbing with stateful "scenarios" and fault responses |
| stoplightio/prism, mockoon, microcks | Apache/MIT | 5,051 / 8,440 / 2,060 | OpenAPI-driven mocks; stateless or lightly stateful |
| modelcontextprotocol/inspector | NOASSERTION | 11,041 | Manual MCP testing |
| temporalio/temporal | MIT | 23,541 | Deterministic replay of workflows (reference for the replay model only) |

## 3. Black-box probe (same script against each Stripe twin)

`POST /v1/customers` → PaymentIntent $100.00 confirmed → `POST /v1/refunds` $60 → `POST /v1/refunds`
$50 (must fail) → `GET /v1/events`. Scripts: run from `research/`, logged 2026-10-08.

| Twin | 2nd refund | Error | Events |
| --- | --- | --- | --- |
| @volter/twin-stripe 3.0.2 | rejected | `amount_too_large` "Refund amount (5000) is greater than unrefunded amount on charge (4000)." | customer.created → payment_intent.created → charge.succeeded → payment_intent.succeeded → refund.created → charge.refunded |
| Kiln-AI/stripe_world 83c2762 | rejected | no `code`; "Refund amount ($50.00) is greater than unrefunded amount on charge ($40.00)" | same six types |
| vercel-labs/emulate c77cb73 | **no route**: `/v1/refunds` 404; PaymentIntent stays `requires_confirmation` | — | — |

Which error body matches real Stripe exactly is **UNVERIFIED**. It needs a sandbox recording
(Phase 18 conformance).

## 4. Test suites run locally (macOS, 2026-10-08)

| Project | Command | Result |
| --- | --- | --- |
| Kiln-AI/Seahaven | `uv run --python 3.14 pytest` | 2,113 passed, 21 failed, 23 skipped. All 21 failures are in world-loading/lint tests ("tool 'write_note' is registered twice"); the kernel tests pass. Its CI badge claims green on Linux; root cause on macOS UNVERIFIED |
| Kiln-AI/stripe_world | `uv run --python 3.14 pytest` | 1,104 passed, 2 skipped |
| vercel-labs/emulate (stripe pkg) | `vitest run` | 23 passed |
| @volter/twin-stripe | not run (no test suite in the npm tarball); booted with `node --experimental-strip-types` and probed instead | probe passed |
| others | not run | UNVERIFIED |

## 5. Scores (0–10, weighted as the brief specifies)

Columns: arch 10 · state 15 · API fidelity 10 · agent compatibility 10 · extensibility 10 · deterministic
replay 10 · fault injection 5 · CI 10 · DX 5 · testing maturity 5 · licence 5 · maintenance 5.

| Project | arch | state | fid | agent | ext | replay | fault | ci | dx | test | lic | maint | **/10** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Kiln-AI/Seahaven (kernel) | 9 | 9 | 5 | 9 | 9 | 9 | 2 | 5 | 8 | 8 | 10 | 7 | **7.70** |
| Kiln-AI/stripe_world | 8 | 9 | 9 | 9 | 6 | 8 | 3 | 5 | 7 | 9 | 1 | 6 | **7.15** |
| @volter/twin + twin-stripe | 8 | 9 | 8 | 7 | 8 | 7 | 3 | 6 | 5 | 5 | 9 | 4 | **7.05** |
| vercel-labs/emulate | 7 | 6 | 4 | 6 | 8 | 4 | 1 | 8 | 9 | 7 | 10 | 9 | **6.40** |
| sierra-research/tau2-bench | 7 | 7 | 2 | 9 | 7 | 6 | 1 | 3 | 6 | 6 | 10 | 9 | **6.05** |
| sajjadGG/Ghostlab | 6 | 2 | 1 | 9 | 6 | 5 | 2 | 6 | 6 | 5 | 10 | 4 | **4.95** |
| Shopify/toxiproxy | 8 | 0 | 0 | 2 | 6 | 3 | 9 | 8 | 8 | 7 | 10 | 9 | **4.85** |
| Terfyn/terfyn | 7 | 3 | 0 | 7 | 6 | 6 | 1 | 5 | 6 | 5 | 10 | 3 | **4.80** |
| adrienverge/localstripe | 5 | 7 | 5 | 5 | 4 | 2 | 1 | 5 | 6 | 4 | 2 | 5 | **4.55** |
| stripe/stripe-mock | 6 | 0 | 6 | 4 | 2 | 3 | 0 | 7 | 8 | 7 | 10 | 9 | **4.50** |
| agentevals-dev/agentevals | 6 | 0 | 0 | 8 | 6 | 5 | 0 | 6 | 6 | 5 | 10 | 6 | **4.45** |
| opendatahub-io/agentic-ci | 6 | 0 | 0 | 7 | 6 | 2 | 0 | 9 | 6 | 5 | 10 | 6 | **4.35** |
| MarckMorris/tfminder | 7 | 2 | 0 | 8 | 4 | 4 | 0 | 5 | 6 | 5 | 10 | 3 | **4.30** |
| stripe-ruby-mock | 4 | 5 | 4 | 1 | 3 | 2 | 3 | 5 | 5 | 6 | 10 | 5 | **4.10** |
| Untargetable/stunt | 5 | 0 | 0 | 2 | 5 | 4 | 7 | 4 | 7 | 4 | 10 | 3 | **3.55** |
| rforgeon/AgentRails | 3 | 1 | 0 | 4 | 3 | 1 | 0 | 1 | 4 | 2 | 2 | 1 | **1.80** |

Scores are judgements from the evidence above, not measurements. Where tests were not run, the
testing column is a reading of the repo's test layout and is UNVERIFIED. Seahaven's API fidelity is
5 because it is a kernel with no vendor API of its own.

## 6. What the matrix says

1. **Nobody covers the brief's differentiators.** None of the top three ships seeded fault
   injection, webhook delay/duplicate/reorder, a CI failure → local replay loop, or dry-run of a
   production snapshot with approval. Volter comes closest on approval (`plan` / `review` bound to
   a hash of the pending action IDs).
2. **The twin is commoditising.** Two independent, days-old projects already pass the stateful
   refund probe. Building yet another Stripe twin from scratch is the wrong place to spend effort.
3. **Licence decides the foundation.** The best Stripe twin (stripe_world, 1,104 passing tests,
   sandbox cassettes) has no licence, so it can only be read. Its MIT kernel (Seahaven) can be
   used.
4. **Volter is a direct competitor, not just a library.** Its "World" product (`npx volter world
   up`, plan, review, push to the real vendor) overlaps the dry-run and apply half of our product.
   Its kernel's source repo is not public.
