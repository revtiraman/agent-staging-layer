# stripe/stripe-mock · adrienverge/localstripe · stripe-ruby-mock

## stripe/stripe-mock (MIT, Go, ★1,653, 98674a1)

OpenAPI-driven HTTP server. README: it "does not attempt to reproduce the behavior of the real
Stripe API at all … responses are completely hardcoded", and "stripe-mock is stateless". Stripe
says it is not planning to add statefulness. **Use:** none at runtime. Its approach (load
`stripe/openapi`, validate request params, 404 unknown routes) is the right one for our
request-validation layer, and we can take the spec directly from `stripe/openapi` (MIT).

## adrienverge/localstripe (GPL-3.0, Python, ★242, ff8ccc4)

Stateful Stripe server (`resources.py`, `server.py`, `webhooks.py`), webhooks registered through
a `/_config/webhooks/<name>` route and signed with a secret; no Connect; latest API only.
**GPL-3.0:** not copied into our core, not linked. Reference for webhook-config ergonomics only.

## stripe-ruby-mock (MIT, Ruby, ★997, 4e553aa)

In-process Ruby mock with `StripeMock.prepare_error` for injecting card errors. Language-bound
(agents in Python/TS can't use it). Reference only: per-call error injection is a nice API shape.

## Conclusion

None is a foundation. stripe-mock confirms the market gap (stateless by design), localstripe
proves the stateful + webhook design but is copyleft, and ruby-mock is language-bound.
