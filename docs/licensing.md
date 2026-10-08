# Licensing

Updated 2026-10-08 (milestone 1). Our licence: **Apache-2.0** (`LICENSE`, `NOTICE`). **No third-party code has been copied into this repository.**
Licences were read from each project's LICENSE file and package metadata, not from GitHub's
detector alone.

| Project | Licence | Code reused? | Architectural ideas reused? | Attribution required? | Modifications | Risk |
| --- | --- | --- | --- | --- | --- | --- |
| Kiln-AI/Seahaven | MIT | **Dependency**, pinned `seahaven==0.5.0`, unmodified | Fixtures-as-branches, before/after change log, seeded clock/ids | Yes: keep its copyright notice in `THIRD_PARTY_NOTICES.md` when distributed | None (composition, not fork) | Low (licence); medium (project is 4 weeks old) |
| stripe/openapi | MIT | **Planned**: a pruned spec for request validation and response shapes | — | Yes: copyright notice with the spec file | Pruned to the operations we serve | Low |
| Kiln-AI/stripe_world | **No licence file** (default copyright) | **No.** Read and run for research only | Middleware layering, recorded-cassette conformance (re-implemented independently) | n/a | n/a | **High if copied.** Don't copy until Kiln publishes a licence |
| @volter/twin, @volter/twin-stripe | Apache-2.0 | No | Plan hash-bound approval, stale-base refusal, push ledger (re-implemented) | Only if code is used (NOTICE) | n/a | Low (licence); high strategic (direct competitor, kernel source not public) |
| vercel-labs/emulate | Apache-2.0 | No | `defineEmulator` plugin shape, signed webhook delivery | Only if code is used | n/a | Low |
| stripe/stripe-mock | MIT | No | Spec-driven param validation | Only if code is used | n/a | Low |
| adrienverge/localstripe | **GPL-3.0** | **No, and must not be** | Webhook config route idea only | n/a | n/a | High if copied or linked (copyleft would reach our core) |
| stripe-ruby-mock | MIT | No | Per-call error injection API shape | n/a | n/a | Low |
| sierra-research/tau2-bench | MIT | No | Grade final state; user-simulator actor | n/a | n/a | Low |
| Shopify/toxiproxy | MIT | No (not used in MVP) | Fault ("toxic") vocabulary, control API | n/a | n/a | Low |
| Untargetable/stunt | MIT | No | YAML rule ergonomics | n/a | n/a | Low |
| sajjadGG/Ghostlab | MIT | No | LLM-as-user actor (post-MVP) | n/a | n/a | Low |
| opendatahub-io/agentic-ci | Apache-2.0 | No | Harness/backend split | n/a | n/a | Low |
| MarckMorris/tfminder | Apache-2.0 | No | Agent can't self-approve; typed confirmation | n/a | n/a | Low |
| Terfyn/terfyn | Apache-2.0 | No | Tamper-evident (hash-chained) run trace | n/a | n/a | Low |
| agentevals-dev/agentevals | Apache-2.0 | No | Emit runs as OTel spans | n/a | n/a | Low |
| rforgeon/AgentRails | No SPDX licence detected | No | No | n/a | n/a | n/a |

## Rules

- "Ideas reused" means re-implemented from understanding, without copying code, comments or
  structure line by line.
- Before any dependency is added, its row here is updated from "Planned" to the pinned version.
- Our licence is Apache-2.0; Seahaven (MIT) and the Stripe spec (MIT) are compatible with it.
  Every source file starts with `SPDX-License-Identifier: Apache-2.0`.
- Stripe names and trademarks: the twin says "not affiliated with Stripe"; vocabulary comes from the
  MIT spec.
