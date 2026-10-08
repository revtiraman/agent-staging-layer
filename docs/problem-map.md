# Problem map

## Today: agent → production

```
Agent (LLM + prompt + tool definitions)
  │  decides on a tool call from partial, possibly stale information
  ▼
Tools (refund_payment, update_ticket, list_orders …)
  │  thin wrappers; no idea of what has already happened
  ▼
External SaaS (Stripe, Shopify, Zendesk)
  │  real money, real customers, eventual consistency, rate limits, webhooks
  ▼
Side effects
  │  refund issued · email sent · order cancelled · ticket closed
  ▼
Potential failure
     duplicate refund (webhook late, agent retries)
     refund > charge (stale read)
     retry storm on 429 (ignores Retry-After)
     acts on a ticket a person has put on hold (concurrent edit)
     wrong batch summary (counts from webhooks that haven't arrived)
     irreversible action with no approval
```

Each failure needs **state + time + concurrency** to appear. None appears in a unit test with a
canned mock.

## With the staging layer

```
Agent                        same code; only the base URL / MCP endpoint changes
  ▼
Twin environment             stateful Stripe twin, private per run, seeded fixture
  ▼
Scenario                     seed data + actors + seeded fault schedule + actions
  ▼
Invariant                    hard rules in code, checked after every step
  │                          (optional LLM judge, reported separately, never gates)
  ▼
Replay                       run log (seed, faults, calls, state diffs) → same failure locally
  ▼
Dry-run                      snapshot real state → branch → run agent → proposed mutations → diff
  ▼
Human approval               approve/reject bound to the exact diff hash
  ▼
Production                   apply only the approved set; refuse if state drifted since the diff
```

## Failure → mechanism that catches it

| Failure | Twin state | Fault | Invariant | Replay | Dry-run |
| --- | --- | --- | --- | --- | --- |
| Duplicate refund under webhook delay | ✓ | webhook_delay | `refund_total <= charge_total` | ✓ | ✓ |
| Retry storm on 429 | ✓ | http_429 | `retries honour Retry-After` | ✓ |  |
| Acts on held ticket | ✓ (Zendesk, post-MVP) | concurrent_mutation | `respects human hold` | ✓ | ✓ |
| Wrong summary | ✓ | webhook_delay | `summary == twin state` | ✓ |  |
| Mass refund on wrong cohort | ✓ |  | `no_unapproved_destructive_action` |  | ✓ |
