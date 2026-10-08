# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""The interface an agent sees: `call(tool, **arguments)` and `on_event(handler)`, nothing else.

The agent gets the same object in a dry-run, a test or (later) against a real
system, so it cannot behave differently because it is being staged. It cannot move
the clock or see the fault profile: both are the harness's (agent_staging.harness).
Every request and every delivered event is written to the run log.

Requests are numbered from 1 in the order the agent makes them (`request`). That is not
the twin's call ordinal (`i`): a request that is rate-limited, or times out before it
is sent, never reaches the twin and has no `i`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn, Protocol

from agent_staging.faults import Delivery, RequestFault
from agent_staging.kernel import Branch, Event, TwinToolError
from agent_staging.runlog import RunLog

TIMEOUT_MESSAGE = "The request timed out. It may or may not have been applied."


class AgentToolError(Exception):
    """A tool refused the call, or the network failed. The agent reads `code` and `message`,
    like a vendor error, and `retry_after` (seconds) on a 429."""

    def __init__(self, code: str, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after


class Hooks(Protocol):
    """What the harness does around each request. Not visible to the agent."""

    def before_request(self, request: int, tool: str) -> RequestFault | None: ...

    def after_twin_call(self) -> None: ...


class Session:
    def __init__(self, branch: Branch, log: RunLog, hooks: Hooks) -> None:
        self._branch = branch
        self._log = log
        self._hooks = hooks
        self._requests = 0
        self._handlers: list[Callable[[Session, Event], None]] = []

    def call(self, tool: str, **arguments: Any) -> Any:
        self._requests += 1
        n = self._requests
        record: dict[str, Any] = {
            "request": n,
            "i": None,
            "at": self._branch.now().isoformat(),
            "tool": tool,
            "arguments": arguments,
        }
        fault = self._hooks.before_request(n, tool)
        if fault is not None and fault.kind == "rate_limited":
            retry = fault.retry_after
            self._fail(
                record,
                AgentToolError("rate_limited", f"Too many requests. Retry after {retry:g}s.", retry_after=retry),
                fault,
                reached_twin=False,
            )
        if fault is not None and fault.kind == "not_sent":
            self._fail(record, AgentToolError("timeout", TIMEOUT_MESSAGE), fault, reached_twin=False)

        record["i"] = len(self._branch.calls())
        try:
            result = self._branch.call(tool, **arguments)
        except TwinToolError as e:
            self._hooks.after_twin_call()
            self._fail(record, AgentToolError(e.code, e.message), None, reached_twin=True)
        self._hooks.after_twin_call()
        if fault is not None and fault.kind == "lost_response":
            record["result"] = result  # what the twin did; the agent never sees it
            self._fail(record, AgentToolError("timeout", TIMEOUT_MESSAGE), fault, reached_twin=True)
        self._log.append("call", record | {"result": result, "reached_twin": True})
        return result

    def _fail(
        self, record: dict[str, Any], error: AgentToolError, fault: RequestFault | None, *, reached_twin: bool
    ) -> NoReturn:
        record["error"] = {"code": error.code, "message": error.message}
        if fault is not None:
            record["fault"] = fault.kind
        record["reached_twin"] = reached_twin
        self._log.append("call", record)
        raise error

    def on_event(self, handler: Callable[[Session, Event], None]) -> None:
        """Register the agent's webhook handler. It is called with this session and the event."""
        self._handlers.append(handler)

    def _deliver(self, event: Event, delivery: Delivery) -> None:
        self._log.append(
            "event.delivered",
            {
                "delivery": delivery.id,
                "event_seq": event.seq,
                "event_id": event.id,
                "copy": delivery.copy,
                "type": event.type,
                "payload": event.payload,
                "emitted_at": event.emitted_at.isoformat(),
                "delivered_at": self._branch.now().isoformat(),
            },
        )
        for handler in self._handlers:
            handler(self, event)
