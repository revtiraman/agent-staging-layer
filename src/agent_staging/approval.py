# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Human approval of a plan.

Rules (docs/spike-notes.md, decision 2):
  * The decision is typed on the controlling terminal (/dev/tty), never read from
    stdin, so a process piping input (an agent, a script, `yes`) cannot approve.
  * The human types `approve <first 8 hex of the plan hash>`, which they can only
    know by having the plan in front of them.
  * The approval records the full plan hash. Apply recomputes the hash from the plan
    file and refuses if it differs (any edit voids the approval).
  * There is no actor kind "agent" and no flag that skips this.

What this defends against today: a buggy agent, not a hostile one with shell access as the
same OS user, which can fake a terminal or write approvals/ directly. `signature` is
always None until milestone 6 signs `{format, plan_id, plan_hash, decision, actor, at}` with
a key the agent cannot read; apply refuses any record that carries one before then.
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_staging.dryrun import Plan, render
from agent_staging.runlog import RunLog
from agent_staging.workspace import Workspace

APPROVAL_FORMAT = "agent-staging.approval/1"


class ApprovalError(Exception):
    pass


@dataclass
class Terminal:
    """The controlling terminal. Injected in tests via a pseudo-terminal, never faked."""

    write: Callable[[str], None]
    readline: Callable[[], str]

    @classmethod
    def open(cls) -> Terminal:
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        except OSError as e:
            raise ApprovalError(
                "approval needs a human at a terminal: there is no controlling terminal "
                "(this is refused on purpose, so an agent or a pipe cannot approve)"
            ) from e

        def write(text: str) -> None:
            os.write(fd, text.encode())

        def readline() -> str:
            # A terminal is not seekable, so read raw bytes up to the newline.
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = os.read(fd, 1)
                if not chunk:
                    break
                buf += chunk
            return buf.decode(errors="replace")

        return cls(write=write, readline=readline)


def challenge(plan: Plan) -> str:
    return f"approve {plan.hash[:8]}"


def load_plan(ws: Workspace, plan_id: str) -> Plan:
    path = ws.plan_path(plan_id)
    if not path.exists():
        raise ApprovalError(f"no plan {plan_id} (plans live in {path.parent})")
    return Plan.from_file(ws.read_json(path))


def decide(ws: Workspace, plan_id: str, *, terminal: Terminal | None = None) -> dict[str, Any]:
    plan = load_plan(ws, plan_id)
    if ws.approval_path(plan.id).exists():
        raise ApprovalError(f"{plan.id} already has a decision: {ws.approval_path(plan.id)}")
    if plan.body["summary"]["blocked"]:
        raise ApprovalError(f"{plan.id} has blocked writes and cannot be approved; see the BLOCKED lines")
    if plan.body["summary"]["to_apply"] == 0:
        raise ApprovalError(f"{plan.id} has nothing to apply")
    tty = terminal or Terminal.open()
    tty.write(render(plan) + "\n\n")
    tty.write(f"To apply, type exactly:  {challenge(plan)}\nAnything else rejects.\n> ")
    answer = tty.readline().strip()
    decision = "approved" if answer == challenge(plan) else "rejected"
    record = {
        "format": APPROVAL_FORMAT,
        "plan_id": plan.id,
        "plan_hash": plan.hash,
        "decision": decision,
        "actor": {"kind": "human", "id": getpass.getuser(), "channel": "tty"},
        "typed": answer,
        "at": datetime.now(UTC).isoformat(timespec="milliseconds"),  # the human's time, not the twin's
        "signature": None,  # milestone 6
    }
    ws.write_json(ws.approval_path(plan.id), record)
    RunLog(ws.run_log_path(plan.body["run_id"])).append("plan.decision", record)
    tty.write(f"{decision.upper()}: {plan.id}\n")
    return record
