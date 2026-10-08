# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""The harness side of a run: the branch, its log, the agent's session, the clock, the faults.

The agent holds only the Session. The harness holds this, so only the harness moves time
and decides faults. Every decision is a `fault` record in the run log, with the seed and
key that produced it, written before its effect happens.

Delivery: `advance(by)` steps the clock to each due instant up to `now + by`. At each
instant it takes the batch of deliveries due exactly then, asks the fault profile for
their order (the only place reordering happens), and delivers them one by one with the
clock at that instant. Deliveries a handler causes that fall due at the same instant form
the next batch there. Nothing is delivered outside `advance`, so how finely the harness
steps time never changes what happens (tested).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from agent_staging.faults import Delivery, Faults, FaultSource, RequestFault
from agent_staging.invariants import Engine, Intent
from agent_staging.kernel import Branch, Event, as_timedelta
from agent_staging.runlog import RunLog
from agent_staging.session import Session


class HarnessError(Exception):
    pass


@dataclass
class Run:
    branch: Branch
    log: RunLog
    faults: FaultSource = field(default_factory=Faults)
    intent: Intent = field(default_factory=Intent)
    engine: Engine | None = None
    session: Session = field(init=False)
    failures: list[int] = field(default_factory=list, init=False)  # seqs of invariant.failed records
    _queue: list[Delivery] = field(default_factory=list, init=False)
    _events: dict[int, Event] = field(default_factory=dict, init=False)
    _batches: int = field(default=0, init=False)
    _logged_changes: int = field(default=0, init=False)
    _request_of_call: dict[int, int] = field(default_factory=dict, init=False)
    _ended: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.session = Session(self.branch, self.log, self)
        o = self.branch.origin
        if self.engine is not None:
            self.log.observers.append(self._check)
            if (self.engine.base.meta.id, self.engine.base.meta.file_sha256) != (o.state, o.file_sha256):
                raise HarnessError("the engine's base state is not the state this branch was opened from")
        origin = {"twin": o.twin, "state": o.state, "file_sha256": o.file_sha256, "seed": o.seed}
        self.log.append("base.state", origin | {"start": o.start.isoformat()})
        if self.engine is not None:
            self.log.append("invariants.active", self.engine.active())
        self.log.append("faults.profile", self.faults.profile())
        self.log.append("intent", self.intent.record())

    @classmethod
    def start(
        cls,
        branch: Branch,
        log: RunLog,
        faults: FaultSource | None = None,
        *,
        intent: Intent | None = None,
        engine: Engine | None = None,
    ) -> Run:
        return cls(branch, log, faults or Faults(), intent or Intent(), engine)

    def finish(self) -> dict[str, Any]:
        """End the run: log any changes made outside a request, check once more, record the outcome."""
        if self._ended:
            raise HarnessError("run already finished")
        self._log_changes()
        self.log.append("steps.end", {"at": self.branch.now().isoformat()})
        outcome = {"outcome": "failed" if self.failures else "passed", "violations": list(self.failures)}
        self.log.append("run.end", outcome)
        self._ended = True
        if self.engine is not None:
            self.log.observers.remove(self._check)
        return outcome

    def _check(self, record: dict[str, Any]) -> None:
        assert self.engine is not None
        for failure in self.engine.feed(record):
            self.failures.append(self.log.append("invariant.failed", failure)["seq"])

    def _log_changes(self) -> None:
        changes = self.branch.changes()
        for c in changes[self._logged_changes :]:
            self.log.append("change", {"request": self._request_of_call.get(c.i), "i": c.i, **c.to_dict()})
        self._logged_changes = len(changes)

    # --- Session hooks ---

    def before_request(self, request: int, tool: str) -> RequestFault | None:
        fault, record = self.faults.request(request, tool)
        if record is not None:
            self.log.append("fault", record)
        return fault

    def after_twin_call(self, request: int) -> None:
        self._request_of_call[len(self.branch.calls()) - 1] = request
        self._log_changes()
        self._collect()

    # --- time ---

    def now(self) -> datetime:
        return self.branch.now()

    def pending(self) -> list[Delivery]:
        return sorted(self._queue, key=lambda d: (d.due, d.event_seq, d.copy))

    def advance(self, by: timedelta | float) -> list[Delivery]:
        start = self.branch.now()
        target = start + as_timedelta(by)
        delivered: list[Delivery] = []
        while self._queue:
            due = min(d.due for d in self._queue)
            if due > target:
                break
            now = self.branch.now()
            if due < now:
                raise HarnessError(f"a delivery due at {due} was missed: the clock was moved outside the harness")
            self.branch.advance(due - now)
            batch = [d for d in self._queue if d.due == due]
            self._queue = [d for d in self._queue if d.due != due]
            self._batches += 1
            ordered, record = self.faults.order(batch, self._batches)
            if record is not None:
                self.log.append("fault", record)
            for delivery in ordered:
                self.session._deliver(self._events[delivery.event_seq], delivery)
                delivered.append(delivery)
        self.branch.advance(target - self.branch.now())
        self.log.append(
            "clock.advance",
            {"from": start.isoformat(), "to": self.branch.now().isoformat(), "delivered": [d.id for d in delivered]},
        )
        return delivered

    # --- scheduling ---

    def _collect(self) -> None:
        for event in self.branch.events():
            if event.seq in self._events:
                continue
            self._events[event.seq] = event
            deliveries, records = self.faults.schedule(event.seq, event.emitted_at)
            for record in records:
                self.log.append("fault", record | {"event_id": event.id, "type": event.type})
            self._queue.extend(deliveries)
