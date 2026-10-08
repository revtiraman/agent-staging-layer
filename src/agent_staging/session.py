# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""The interface an agent sees: `call(tool, **arguments)`, nothing else.

The agent gets the same object in a dry-run, a test or (later) against a real
system, so it cannot behave differently because it is being staged. Every call is
written to the run log with its result or error.
"""

from __future__ import annotations

from typing import Any

import seahaven

from agent_staging.runlog import RunLog


class AgentToolError(Exception):
    """A tool refused the call. The agent reads `code` and `message`, like a vendor error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Session:
    def __init__(self, instance: seahaven.Instance, log: RunLog) -> None:
        self._instance = instance
        self._log = log

    def call(self, tool: str, **arguments: Any) -> Any:
        i = self._instance.call_count
        try:
            result = self._instance.call(tool, **arguments)
        except seahaven.ToolError as e:
            self._log.append(
                "call", {"i": i, "tool": tool, "arguments": arguments, "error": {"code": e.code, "message": str(e)}}
            )
            raise AgentToolError(e.code, str(e)) from None
        self._log.append("call", {"i": i, "tool": tool, "arguments": arguments, "result": result})
        return result
