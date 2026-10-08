# Agent eval, CI, fault and approval tooling

Shorter notes on the remaining clones. All studied at the commits in `research/README.md`; tests
were not run (UNVERIFIED).

## sierra-research/tau2-bench (MIT, Python, ★2,189)

A domain = policy + agent tools + DB + tasks (+ user tools); an orchestrator runs agent ↔ user
simulator ↔ environment; an evaluator checks the final DB state against the expected state
(`src/tau2/{domains,environment,orchestrator,evaluator,user}`).
**Take:** the *grade-the-final-state* evaluator and the user-simulator actor are the right
abstractions for our scenario engine. **Not:** a foundation; its domains are synthetic (airline,
retail, telecom, banking), not vendor APIs.

## Shopify/toxiproxy (MIT, Go, ★12,394)

TCP proxy with "toxics": latency, timeout, bandwidth, slow_close, reset_peer, limit_data; HTTP
control API; directional streams. **Take:** the fault vocabulary and control-API shape. **Not
used at runtime in MVP:** TCP faults aren't seeded per request, and webhook delay, duplicates and
429 bodies are application-level, so we implement them in the twin's middleware where they
replay deterministically.

## Untargetable/stunt (MIT, Python, ★2)

mitmproxy addon: `rules.yaml` to mock, modify, delay and throttle HTTP(S). **Take:** rule-file
ergonomics for faults. Not a dependency (single maintainer, created 2026-09-06).

## sajjadGG/Ghostlab (MIT, Python, ★2)

Coding agents role-play users against your MCP tools; traces, scores, MCP Apps UI clicking.
**Take:** "test the agent as it is actually used" framing; an LLM-as-user actor is a possible
post-MVP scenario actor.

## opendatahub-io/agentic-ci (Apache-2.0, Python, ★11)

Runs Claude Code / OpenCode / Codex inside CI with local or sandboxed backends and OTel.
**Take:** harness/backends split for `agent-runner`. Not needed for MVP (our agent runs as a
normal process against the twin).

## MarckMorris/tfminder (Apache-2.0, Python, ★0)

MCP server between an agent and Terraform: the agent can plan, can't approve itself, can't apply
what wasn't reviewed; signed attestation; typed-confirmation approval in the terminal.
**Take:** the threat model for approval bypass (an agent must never be able to call `approve`).

## Terfyn/terfyn (Apache-2.0, Go, ★4)

Declares each agent's tools, budget and approval gates as versioned resources; `terfyn plan`
shows the capability diff, `apply`, `run` with human pauses, and a tamper-evident trace.
**Take:** hash-chained run log for replay-tampering mitigation.

## agentevals-dev/agentevals (Apache-2.0, Python, ★162)

Framework-agnostic evals over OpenTelemetry traces. **Take:** emit our run log as OTel spans too,
so existing eval tools can read it (Phase 16).

## rforgeon/AgentRails (no licence detected, TypeScript, ★45, last push 2025-02)

A dashboard for managing agents. Not relevant; not cloned.
