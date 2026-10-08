# Draft: licence request to Kiln (not sent)

Where: a new issue on https://github.com/Kiln-AI/stripe_world (issues are enabled; no existing
issue asks this). The maintainer is @scosman (Steve Cosman), who also wrote Seahaven.

---

**Title:** Add a licence (MIT, like Seahaven?)

Hi @scosman,

stripe_world doesn't have a LICENSE file. `THIRD_PARTY_LICENSES.md` covers Stripe's OpenAPI spec,
and Seahaven itself is MIT, but the world's own code has no licence. As it stands, nobody can
legally depend on it or build on it.

Would you be open to adding one? MIT would match Seahaven.

For context: I'm building a staging layer for AI agents on top of Seahaven. It adds seeded fault
injection, delayed and duplicated webhooks, deterministic replay of failed CI runs, and a dry-run
with human approval. I ran stripe_world locally: its tests pass (1,104), and its refund handling
is the most faithful I've found. I'd much rather depend on it, and send back the webhook
delivery the README lists as missing, than write a second Stripe world.

If you'd prefer a different licence, or want to keep it unlicensed, that's useful to know too.

Thanks,
Revtiraman
