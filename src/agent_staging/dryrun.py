# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Dry-run: run an agent on a branch of production, turn what it did into a plan.

The plan is built from two logs of the branch (agent_staging.kernel):
  * calls: one record per call, with tool, arguments and error;
  * changes: one record per changed row, with `i`, the ordinal of the call that made it,
    and the row before and after.
Joining them on `i` gives, for every write, the call that caused it, which is what lets
the diff say "Refund $120.00 on charge-0001" instead of "a row changed in refunds".
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_staging.harness import Run
from agent_staging.kernel import Branch, Change, StateMeta, TwinKernel, money
from agent_staging.runlog import RunLog, sha256
from agent_staging.session import Session
from agent_staging.workspace import Workspace

PLAN_FORMAT = "agent-staging.plan/2"  # /2: kernel interface, per-currency amounts, assigned columns

Agent = Callable[[Session], None]


class DryRunError(Exception):
    pass


@dataclass(frozen=True)
class Plan:
    body: dict[str, Any]  # everything that is hashed and shown

    @property
    def hash(self) -> str:
        return sha256(self.body)

    @property
    def id(self) -> str:
        return "plan-" + self.hash[:12]

    def to_file(self) -> dict[str, Any]:
        return {"id": self.id, "hash": self.hash, "body": self.body}

    @classmethod
    def from_file(cls, data: dict[str, Any]) -> Plan:
        plan = cls(data["body"])
        if plan.hash != data["hash"] or plan.id != data["id"]:
            raise DryRunError(f"plan file {data.get('id')} does not match its own hash; it was edited")
        return plan


def build_plan(twin: TwinKernel, branch: Branch, *, base: StateMeta, run_id: str) -> Plan:
    by_call: dict[int | None, list[Change]] = defaultdict(list)
    for change in branch.changes():
        by_call[change.i].append(change)

    operations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for call in branch.calls():
        changes = by_call.pop(call.i, [])
        mutating = twin.is_mutating(call.tool)
        if call.error is not None:
            if mutating:
                d = twin.describe(branch, call.tool, call.arguments, [])
                skipped.append(
                    {
                        "i": call.i,
                        "tool": call.tool,
                        "arguments": call.arguments,
                        "describe": d.text,
                        "amount": d.amount,
                        "currency": d.currency,
                        "reason": call.error["message"],
                        "code": call.error["code"],
                    }
                )
            continue
        if not changes:
            continue  # a read
        if not mutating:
            blocked.append(
                {
                    "i": call.i,
                    "tool": call.tool,
                    "reason": "tool wrote rows but is not declared as mutating",
                    "changes": [c.to_dict() for c in changes],
                }
            )
            continue
        d = twin.describe(branch, call.tool, call.arguments, changes)
        operations.append(
            {
                "i": call.i,
                "tool": call.tool,
                "arguments": call.arguments,
                "describe": d.text,
                "amount": d.amount,
                "currency": d.currency,
                "irreversible": twin.is_irreversible(call.tool),
                "destructive": any(c.op == "delete" for c in changes),
                "changes": [c.to_dict() for c in changes],
            }
        )
    for i, changes in by_call.items():  # writes with no call in flight, e.g. inside bulk()
        blocked.append(
            {"i": i, "tool": None, "reason": "rows changed outside any call", "changes": [c.to_dict() for c in changes]}
        )

    tables = sorted({c["table"] for op in operations for c in op["changes"]})
    amounts: dict[str, int] = defaultdict(int)
    for op in operations:
        amounts[op["currency"]] += op["amount"]
    body = {
        "format": PLAN_FORMAT,
        "run_id": run_id,
        "world": {"name": base.world, "version": base.world_version, "schema_hash": base.schema_hash},
        "base": {"state": base.id, "file_sha256": base.file_sha256, "now": base.now.isoformat()},
        # Which columns of these tables the system fills in. Values there are only valid on the
        # branch that made them: milestone 6 maps them instead of comparing (spike-notes).
        "assigned": {t: dict(twin.assigned_columns(t)) for t in tables},
        "operations": operations,
        "skipped": skipped,
        "blocked": blocked,
        "summary": {
            "proposed": len(operations) + len(skipped),
            "to_apply": len(operations),
            "skipped": len(skipped),
            "blocked": len(blocked),
            "destructive": sum(op["destructive"] for op in operations),
            "irreversible": sum(op["irreversible"] for op in operations),
            "amounts": dict(sorted(amounts.items())),
        },
    }
    return Plan(body)


def dry_run(ws: Workspace, twin: TwinKernel, agent: Agent, *, agent_name: str) -> tuple[Plan, str]:
    """Branch production, run the agent there, write the plan. Production is not touched."""
    head = ws.prod_head()
    if head is None:
        raise DryRunError("no production state yet: run `staging spike seed` first")
    base = twin.state(head)
    run_id = ws.new_run_id()
    log = RunLog.create(
        ws.run_log_path(run_id), {"kind": "dry-run", "agent": agent_name, "twin": twin.name, "base": head}
    )
    with twin.open(head) as branch:
        run = Run.start(branch, log)
        agent(run.session)
        run.finish()  # logs every change, including any made outside a call
        plan = build_plan(twin, branch, base=base, run_id=run_id)
    ws.write_json(ws.plan_path(plan.id), plan.to_file())
    log.append("plan.created", {"plan_id": plan.id, "hash": plan.hash, "summary": plan.body["summary"]})
    return plan, run_id


def _amounts(amounts: dict[str, int]) -> str:
    return " + ".join(money(cents, cur) for cur, cents in amounts.items()) or money(0)


def render(plan: Plan) -> str:
    b = plan.body
    s = b["summary"]
    lines = [f"PLAN {plan.id}   base {b['base']['state']}   run {b['run_id']}", ""]
    for op in b["operations"]:
        flags = "".join(
            [" [DESTRUCTIVE]" if op["destructive"] else "", " [IRREVERSIBLE]" if op["irreversible"] else ""]
        )
        lines.append(f"  + {op['describe']}{flags}")
        for c in op["changes"]:
            key = ",".join(str(v) for v in c["key"].values())
            if c["op"] == "update":
                cols = ", ".join(f"{k}: {c['before'][k]} -> {c['after'][k]}" for k in c["after"])
                lines.append(f"      ~ {c['table']} {key}: {cols}")
            elif c["op"] == "insert":
                lines.append(f"      + {c['table']} {key}")
            else:
                lines.append(f"      - {c['table']} {key}")
    for sk in b["skipped"]:
        lines.append(f"  ~ SKIPPED  {sk['describe']}: {sk['reason']}")
    for bl in b["blocked"]:
        lines.append(f"  ! BLOCKED  {bl['tool'] or '(no call)'}: {bl['reason']}")
    lines += [
        "",
        (
            f"Summary: {s['proposed']} proposed · {s['to_apply']} to apply ({_amounts(s['amounts'])}) · "
            f"{s['skipped']} skipped · {s['destructive']} destructive · {s['irreversible']} irreversible · "
            f"{s['blocked']} blocked"
        ),
        f"Hash:    sha256:{plan.hash}",
    ]
    return "\n".join(lines)
