# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""The harness side of a run: the branch, its log, the agent's session, and the clock.

The agent holds only the Session. The harness holds this, so only the harness moves time,
and every move is logged so a run can be replayed (milestone 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from agent_staging.kernel import Branch, Event, as_timedelta
from agent_staging.runlog import RunLog
from agent_staging.session import Session


@dataclass
class Run:
    branch: Branch
    log: RunLog
    session: Session

    @classmethod
    def start(cls, branch: Branch, log: RunLog) -> Run:
        return cls(branch, log, Session(branch, log))

    def now(self) -> datetime:
        return self.branch.now()

    def advance(self, by: timedelta | float) -> list[Event]:
        delta = as_timedelta(by)
        before = self.branch.now()
        delivered = self.branch.advance(delta)
        self.log.append(
            "clock.advance",
            {
                "from": before.isoformat(),
                "to": self.branch.now().isoformat(),
                "delivered": [e.seq for e in delivered],
            },
        )
        return delivered
