# Clock advance request to Seahaven

Posted 2026-10-09: https://github.com/Kiln-AI/Seahaven/issues/47 (from @revtiraman).

---

**Title:** Expose advance() (or a setter) for a fixed-clock instance

Hi — I'm using Seahaven as the kernel for an agent staging layer. I need advance(duration) on a fixed-clock instance so a harness can move virtual time forward deterministically (webhook scheduling, retry windows). The public API only exposes now(), so I'm currently writing to Clock._start from outside the package. It works, but it'll break on any internal refactor. Would you consider exposing advance() or a setter on the instance? Happy to send a PR.
