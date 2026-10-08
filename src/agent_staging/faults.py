# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Seeded faults: what can go wrong between an agent and a twin, decided reproducibly.

Two families:
  * webhook delivery: delay, duplicate, reorder;
  * requests: 429 with Retry-After, and timeout (either the request never reached the
    twin, or it did and the response was lost: the agent can't tell which).

Every decision draws from its own stream, `seed` + a stable key ("webhook/3",
"request/5"), so adding or removing one decision never shifts another. And every
decision is returned with the record the harness writes to the run log, seed and key
included. The log, not the seed, is the source of truth: replay (milestone 5) reads
these records and never draws again (docs/spike-notes.md, milestone 3).

Reorder is decided per batch of deliveries due at the *same instant* and nowhere else
(spike-notes, decision 6). Order across instants changes only through each delivery's
own delay, which is itself a logged decision.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

type RequestFaultKind = Literal["rate_limited", "not_sent", "lost_response"]
REQUEST_FAULTS: frozenset[str] = frozenset({"rate_limited", "not_sent", "lost_response"})


def stream(seed: int, key: str) -> random.Random:
    digest = hashlib.sha256(f"agent-staging.faults/1:{seed}:{key}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _ms_between(r: random.Random, bounds: tuple[float, float]) -> timedelta:
    lo, hi = (round(b * 1000) for b in bounds)
    return timedelta(milliseconds=r.randint(lo, hi))


def _check_range(name: str, bounds: tuple[float, float]) -> None:
    lo, hi = bounds
    if not 0 <= lo <= hi:
        raise ValueError(f"{name} must be (lo, hi) seconds with 0 <= lo <= hi, got {bounds}")


def _check_rate(name: str, rate: float) -> None:
    if not 0 <= rate <= 1:
        raise ValueError(f"{name} must be a probability between 0 and 1, got {rate}")


@dataclass(frozen=True)
class Delivery:
    """One attempt to deliver an event. A duplicated event has copies 0 and 1."""

    event_seq: int
    copy: int
    due: datetime

    @property
    def id(self) -> str:
        return f"{self.event_seq}.{self.copy}"


@dataclass(frozen=True)
class RequestFault:
    kind: RequestFaultKind
    retry_after: float | None = None  # seconds, for rate_limited


class FaultSource(Protocol):
    """Where the harness gets fault decisions: drawn from a seed (`Faults`), or read back from a
    run log (`agent_staging.reexec.RecordedFaults`). Each method returns the decision and the
    exact record to log."""

    def profile(self) -> dict[str, Any]: ...

    def schedule(self, event_seq: int, emitted_at: datetime) -> tuple[list[Delivery], list[dict[str, Any]]]: ...

    def order(self, batch: Sequence[Delivery], batch_no: int) -> tuple[list[Delivery], dict[str, Any] | None]: ...

    def request(self, n: int, tool: str) -> tuple[RequestFault | None, dict[str, Any] | None]: ...


@dataclass(frozen=True)
class Faults:
    """A fault profile. All zero (the default) means a perfect network, still logged."""

    seed: int = 0
    webhook_delay: tuple[float, float] = (0.0, 0.0)  # seconds after emission, uniform, ms steps
    duplicate_rate: float = 0.0  # chance an event is delivered twice
    duplicate_gap: tuple[float, float] = (0.0, 0.0)  # how long after the first copy the second is due
    reorder_rate: float = 0.0  # chance a same-instant batch of 2+ deliveries is shuffled
    rate_limit_rate: float = 0.0  # chance a request gets a 429
    retry_after: float = 1.0  # the Retry-After a 429 carries, seconds
    timeout_rate: float = 0.0  # chance a request times out
    lost_response_share: float = 0.5  # of timeouts, the share where the twin did run the request
    # Faults pinned to request numbers (1 = the agent's first call), for scenarios that need
    # "429 on request 3" exactly. These win over the rates.
    at_request: Mapping[int, RequestFaultKind] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_range("webhook_delay", self.webhook_delay)
        _check_range("duplicate_gap", self.duplicate_gap)
        for name in ("duplicate_rate", "reorder_rate", "rate_limit_rate", "timeout_rate", "lost_response_share"):
            _check_rate(name, getattr(self, name))
        if self.rate_limit_rate + self.timeout_rate > 1:
            raise ValueError("rate_limit_rate + timeout_rate must not exceed 1")
        if self.retry_after < 0:
            raise ValueError("retry_after must be >= 0")
        bad = {n: k for n, k in self.at_request.items() if n < 1 or k not in REQUEST_FAULTS}
        if bad:
            raise ValueError(f"at_request needs request numbers >= 1 and kinds in {sorted(REQUEST_FAULTS)}: {bad}")

    def profile(self) -> dict[str, Any]:
        """The profile as written into the run log's start record."""
        return {
            "seed": self.seed,
            "webhook_delay": list(self.webhook_delay),
            "duplicate_rate": self.duplicate_rate,
            "duplicate_gap": list(self.duplicate_gap),
            "reorder_rate": self.reorder_rate,
            "rate_limit_rate": self.rate_limit_rate,
            "retry_after": self.retry_after,
            "timeout_rate": self.timeout_rate,
            "lost_response_share": self.lost_response_share,
            "at_request": {str(n): k for n, k in sorted(self.at_request.items())},
        }

    # --- decisions: each returns its result and the log records that explain it ---

    def schedule(self, event_seq: int, emitted_at: datetime) -> tuple[list[Delivery], list[dict[str, Any]]]:
        """When an event is delivered, and whether twice."""
        key = f"webhook/{event_seq}"
        r = stream(self.seed, key)
        delay = _ms_between(r, self.webhook_delay)
        deliveries = [Delivery(event_seq, 0, emitted_at + delay)]
        records = [
            {
                "kind": "webhook.schedule",
                "seed": self.seed,
                "key": key,
                "event_seq": event_seq,
                "copy": 0,
                "delay_ms": delay // timedelta(milliseconds=1),
                "due": deliveries[0].due.isoformat(),
            }
        ]
        if r.random() < self.duplicate_rate:
            gap = _ms_between(r, self.duplicate_gap)
            deliveries.append(Delivery(event_seq, 1, deliveries[0].due + gap))
            records.append(
                {
                    "kind": "webhook.duplicate",
                    "seed": self.seed,
                    "key": key,
                    "event_seq": event_seq,
                    "copy": 1,
                    "gap_ms": gap // timedelta(milliseconds=1),
                    "due": deliveries[1].due.isoformat(),
                }
            )
        return deliveries, records

    def order(self, batch: Sequence[Delivery], batch_no: int) -> tuple[list[Delivery], dict[str, Any] | None]:
        """The order of deliveries due at the same instant. Emission order unless reordered.

        `batch_no` counts batches in the run, so two batches at one instant (the second made
        of deliveries emitted while the first was handled) draw from different streams."""
        batch = sorted(batch, key=lambda d: (d.event_seq, d.copy))
        if len(batch) < 2:
            return batch, None
        key = f"batch/{batch_no}"
        r = stream(self.seed, key)
        shuffled = r.random() < self.reorder_rate
        out = list(batch)
        if shuffled:
            r.shuffle(out)
            if out == batch:  # a shuffle that changes nothing would be logged as a reorder that wasn't
                out = out[1:] + out[:1]
        return out, {
            "kind": "webhook.reorder" if shuffled else "webhook.batch",
            "seed": self.seed,
            "key": key,
            "due": batch[0].due.isoformat(),
            "emitted_order": [d.id for d in batch],
            "delivery_order": [d.id for d in out],
        }

    def request(self, n: int, tool: str) -> tuple[RequestFault | None, dict[str, Any] | None]:
        """Whether the agent's `n`th request fails before or after it reaches the twin."""
        key = f"request/{n}"
        if n in self.at_request:
            kind, source = self.at_request[n], "pinned"
        else:
            r = stream(self.seed, key)
            u = r.random()
            if u < self.rate_limit_rate:
                kind = "rate_limited"
            elif u < self.rate_limit_rate + self.timeout_rate:
                kind = "lost_response" if r.random() < self.lost_response_share else "not_sent"
            else:
                return None, None
            source = "drawn"
        fault = RequestFault(kind, self.retry_after if kind == "rate_limited" else None)
        record = {
            "kind": f"request.{kind}",
            "seed": self.seed,
            "key": key,
            "source": source,
            "request": n,
            "tool": tool,
        }
        if fault.retry_after is not None:
            record["retry_after"] = fault.retry_after
        return fault, record
