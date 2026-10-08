# vercel-labs/emulate

`github.com/vercel-labs/emulate` · Apache-2.0 · TypeScript (pnpm/turbo monorepo) · ★1,880 ·
created 2026-03-20 · studied at c77cb73.

## Architecture

`packages/@emulators/core` (Hono-style HTTP server, in-memory `Store` of typed collections,
`persistence.ts`, `webhooks.ts`, inspector UI, `defineEmulator()` for custom providers) plus one
package per vendor (Vercel, GitHub, Google, Slack, Stripe, AWS, Linear, Twilio …) and Next/Nuxt
adapters. `npx emulate` starts all services on loopback ports.

## Stripe emulator (about 1,900 lines)

Routes: customers, payment_methods (list only), customer_sessions, payment_intents (create,
retrieve, update, confirm, cancel, list), charges (retrieve, list), products, prices,
checkout sessions, plus a hosted checkout page and signed webhook delivery (`Stripe-Signature`).
**No refunds, no events endpoint** (`POST /v1/refunds` → 404 in our probe).

## State model

In-memory collections with integer auto-ids and `StoreSnapshot` serialisation; optional persistence.

## Testing

vitest; Stripe package 23/23 passed locally.

## Reusable pieces

Apache-2.0. The `defineEmulator` plugin shape and signed-webhook delivery are good references;
the Stripe money path is too thin to build on.

## Missing

Refund graph, deterministic clock (UNVERIFIED), seeded faults, replay, dry-run.

## Opportunity

Best DX and distribution (Vercel). If we need Shopify/Zendesk-like breadth later, a compatibility
adapter could run emulate services as additional twins; out of MVP scope.
