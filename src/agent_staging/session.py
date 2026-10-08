# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""The interface an agent sees: `call(tool, **arguments)` and `on_event(handler)`, nothing else.

The agent gets the same object in a dry-run, a test or (later) against a real
system, so it cannot behave differently because it is being staged. It cannot move
the clock: time is the harness's (agent_staging.harness). Every call and every
delivered event is written to the run log.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_staging.kernel import Branch, Event, TwinToolError
from agent_staging.runlog import RunLog


class AgentToolError(Exception):
    """A tool refused the call. The agent reads `code` and `message`, like a vendor error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Session:
    def __init__(self, branch: Branch, log: RunLog) -> None:
        self._branch = branch
        self._log = log
        self._handlers: list[Callable[[Session, Event], None]] = []
        branch.subscribe(self._deliver)

    def call(self, tool: str, **arguments: Any) -> Any:
        i = len(self._branch.calls())
        at = self._branch.now().isoformat()
        try:
            result = self._branch.call(tool, **arguments)
        except TwinToolError as e:
            self._log.append(
                "call",
                {
                    "i": i,
                    "at": at,
                    "tool": tool,
                    "arguments": arguments,
                    "error": {"code": e.code, "message": e.message},
                },
            )
            raise AgentToolError(e.code, e.message) from None
        self._log.append("call", {"i": i, "at": at, "tool": tool, "arguments": arguments, "result": result})
        return result

    def on_event(self, handler: Callable[[Session, Event], None]) -> None:
        """Register the agent's webhook handler. It is called with this session and the event."""
        self._handlers.append(handler)

    def _deliver(self, event: Event) -> None:
        self._log.append(
            "event.delivered",
            {
                "seq": event.seq,
                "type": event.type,
                "payload": event.payload,
                "emitted_at": event.emitted_at.isoformat(),
                "delivered_at": self._branch.now().isoformat(),
            },
        )
        for handler in self._handlers:
            handler(self, event)
