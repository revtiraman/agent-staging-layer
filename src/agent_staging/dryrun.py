# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Dry-run: run an agent on a branch of production, turn what it did into a plan.

The plan is built from two Seahaven logs of the branch instance:
  * the call log: one record per call, with tool, arguments and error;
  * the change log: one record per changed row, with `i`, the ordinal of the call that
    made it, and the row before and after.
Joining them on `i` gives, for every write, the call that caused it, which is what lets
the diff say "Refund $120.00 on charge-0001" instead of "a row changed in refunds".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import seahaven

from agent_staging.runlog import RunLog, sha256
from agent_staging.session import Session
from agent_staging.twins import refund_path
from agent_staging.workspace import Workspace

PLAN_FORMAT = "agent-staging.plan/1"

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


def _change_dict(record: seahaven.LogRecord) -> dict[str, Any]:
    return {
        "table": record.table,
        "op": record.op,
        "key": dict(record.key),
        "before": None if record.before is None else dict(record.before),
        "after": None if record.after is None else dict(record.after),
    }


def build_plan(inst: seahaven.Instance, *, base: seahaven.Fixture, run_id: str) -> Plan:
    calls = inst.call_log()
    changes_by_call: dict[int | None, list[dict[str, Any]]] = {}
    for record in inst.change_log():
        changes_by_call.setdefault(record.i, []).append(_change_dict(record))

    operations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for i, call in enumerate(calls):
        changes = changes_by_call.pop(i, [])
        mutating = call.tool in refund_path.MUTATING_TOOLS
        if call.error is not None:
            if mutating:
                text, cents = refund_path.describe(call.tool, dict(call.arguments), [], inst.db)
                skipped.append(
                    {
                        "i": i,
                        "tool": call.tool,
                        "arguments": dict(call.arguments),
                        "describe": text,
                        "amount": cents,
                        "reason": call.error,
                    }
                )
            continue
        if not changes:
            continue  # a read
        if not mutating:
            blocked.append(
                {
                    "i": i,
                    "tool": call.tool,
                    "reason": "tool wrote rows but is not declared as mutating",
                    "changes": changes,
                }
            )
            continue
        text, cents = refund_path.describe(call.tool, dict(call.arguments), changes, inst.db)
        operations.append(
            {
                "i": i,
                "tool": call.tool,
                "arguments": dict(call.arguments),
                "describe": text,
                "amount": cents,
                "irreversible": call.tool in refund_path.IRREVERSIBLE_TOOLS,
                "destructive": any(c["op"] == "delete" for c in changes),
                "changes": changes,
            }
        )
    for i, changes in changes_by_call.items():  # writes with no call in flight, e.g. inst.bulk()
        blocked.append({"i": i, "tool": None, "reason": "rows changed outside any call", "changes": changes})

    meta = base.meta
    body = {
        "format": PLAN_FORMAT,
        "run_id": run_id,
        "world": {"name": meta.world, "version": meta.world_version, "schema_hash": meta.schema_hash},
        "base": {"fixture": meta.id, "file_sha256": meta.file_sha256},
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
            "amount": sum(op["amount"] for op in operations),
        },
    }
    return Plan(body)


def find_fixture(world: seahaven.World, fixture_id: str) -> seahaven.Fixture:
    for fixture in world.fixtures():
        if fixture.meta.id == fixture_id:
            return fixture
    raise DryRunError(f"fixture {fixture_id} not found")


def dry_run(ws: Workspace, agent: Agent, *, agent_name: str) -> tuple[Plan, str]:
    """Branch production, run the agent there, write the plan. Production is not touched."""
    head = ws.prod_head()
    if head is None:
        raise DryRunError("no production state yet: run `staging spike seed` first")
    world = refund_path.make_world(ws.fixtures)
    base = find_fixture(world, head)
    run_id = ws.new_run_id()
    log = RunLog.create(ws.run_log_path(run_id), {"kind": "dry-run", "agent": agent_name, "base": head})
    with world.instance(head) as branch:
        agent(Session(branch, log))
        plan = build_plan(branch, base=base, run_id=run_id)
        for change in branch.change_log():
            log.append("change", _change_dict(change) | {"i": change.i})
    ws.write_json(ws.plan_path(plan.id), plan.to_file())
    log.append("plan.created", {"plan_id": plan.id, "hash": plan.hash, "summary": plan.body["summary"]})
    return plan, run_id


def render(plan: Plan) -> str:
    b = plan.body
    s = b["summary"]
    lines = [f"PLAN {plan.id}   base {b['base']['fixture']}   run {b['run_id']}", ""]
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
            f"Summary: {s['proposed']} proposed · {s['to_apply']} to apply ({refund_path._money(s['amount'])}) · "
            f"{s['skipped']} skipped · {s['destructive']} destructive · {s['irreversible']} irreversible · "
            f"{s['blocked']} blocked"
        ),
        f"Hash:    sha256:{plan.hash}",
    ]
    return "\n".join(lines)
