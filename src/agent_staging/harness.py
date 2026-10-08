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

from agent_staging.faults import Delivery, Faults, RequestFault
from agent_staging.kernel import Branch, Event, as_timedelta
from agent_staging.runlog import RunLog
from agent_staging.session import Session


class HarnessError(Exception):
    pass


@dataclass
class Run:
    branch: Branch
    log: RunLog
    faults: Faults = field(default_factory=Faults)
    session: Session = field(init=False)
    _queue: list[Delivery] = field(default_factory=list, init=False)
    _events: dict[int, Event] = field(default_factory=dict, init=False)
    _batches: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.session = Session(self.branch, self.log, self)
        self.log.append("faults.profile", self.faults.profile())

    @classmethod
    def start(cls, branch: Branch, log: RunLog, faults: Faults | None = None) -> Run:
        return cls(branch, log, faults or Faults())

    # --- Session hooks ---

    def before_request(self, request: int, tool: str) -> RequestFault | None:
        fault, record = self.faults.request(request, tool)
        if record is not None:
            self.log.append("fault", record)
        return fault

    def after_twin_call(self) -> None:
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
