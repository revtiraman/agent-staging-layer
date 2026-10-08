# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Apply an approved plan to production.

Order (each step refuses before any write):
  1. The plan file still hashes to what was approved.
  2. Production has not moved in a way that touches the plan (stale check).
  3. Re-execute exactly the approved calls on a fresh production instance.
  4. Check the rows they changed equal the plan's rows (volatile columns excepted).
  5. Only then freeze the result as the new production state and move HEAD.
If 3 or 4 fails, the instance is discarded and production is unchanged.
"""

from __future__ import annotations

from typing import Any

import seahaven

from agent_staging.approval import ApprovalError, load_plan
from agent_staging.dryrun import _change_dict, find_fixture
from agent_staging.runlog import RunLog
from agent_staging.twins import refund_path
from agent_staging.workspace import Workspace


class ApplyError(Exception):
    pass


def _strip_volatile(change: dict[str, Any]) -> dict[str, Any]:
    volatile = refund_path.VOLATILE_COLUMNS.get(change["table"], frozenset())

    def side(row: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if row is None else {k: v for k, v in row.items() if k not in volatile}

    return {**change, "before": side(change["before"]), "after": side(change["after"])}


def stale_reasons(inst: seahaven.Instance, operations: list[dict[str, Any]]) -> list[str]:
    """What in current production contradicts the rows the plan was computed from.

    Stale means: a column the plan updates no longer holds the value the plan saw, a row the
    plan inserts already exists, or a row it deletes is gone. Rows the plan doesn't touch can
    change freely (decision 5).
    """
    reasons = []
    for op in operations:
        for c in op["changes"]:
            where = " AND ".join(f"{k} = ?" for k in c["key"])
            row = inst.db.one(f"SELECT * FROM {c['table']} WHERE {where}", *c["key"].values())
            label = f"{c['table']} {','.join(map(str, c['key'].values()))}"
            if c["op"] == "insert" and row is not None:
                reasons.append(f"{label} already exists")
            elif c["op"] in ("update", "delete"):
                if row is None:
                    reasons.append(f"{label} no longer exists")
                elif c["op"] == "update":
                    for col, seen in c["before"].items():
                        if row[col] != seen:
                            reasons.append(f"{label}.{col} is {row[col]}, plan expected {seen}")
    return reasons


def apply_plan(ws: Workspace, plan_id: str) -> dict[str, Any]:
    plan = load_plan(ws, plan_id)
    log = RunLog(ws.run_log_path(plan.body["run_id"]))
    if not ws.approval_path(plan.id).exists():
        raise ApprovalError(f"{plan.id} has not been approved: run `staging approve {plan.id}`")
    approval = ws.read_json(ws.approval_path(plan.id))
    if approval["decision"] != "approved":
        raise ApprovalError(f"{plan.id} was {approval['decision']}")
    if approval["plan_hash"] != plan.hash:
        raise ApprovalError(f"{plan.id} changed after it was approved")
    if approval.get("applied_to"):
        raise ApplyError(f"{plan.id} was already applied as {approval['applied_to']}")

    world = refund_path.make_world(ws.fixtures)
    head = ws.prod_head()
    if head is None:
        raise ApplyError("no production state")
    base = plan.body["base"]
    if head == base["fixture"] and find_fixture(world, head).meta.file_sha256 != base["file_sha256"]:
        raise ApplyError(f"fixture {head} is not the file the plan was made from")

    operations = plan.body["operations"]
    with world.instance(head) as prod:
        reasons = stale_reasons(prod, operations) if head != base["fixture"] else []
        if reasons:
            log.append("apply.refused", {"plan_id": plan.id, "head": head, "stale": reasons})
            raise ApplyError("production changed since the plan was made:\n  " + "\n  ".join(reasons))
        for op in operations:
            start = len(prod.change_log())
            try:
                prod.call(op["tool"], **op["arguments"])
            except seahaven.ToolError as e:
                log.append("apply.aborted", {"plan_id": plan.id, "i": op["i"], "error": str(e)})
                raise ApplyError(f"{op['describe']} failed on production: {e}; nothing was applied") from None
            got = [_strip_volatile(_change_dict(r)) for r in prod.change_log()[start:]]
            want = [_strip_volatile(c) for c in op["changes"]]
            if got != want:
                log.append("apply.aborted", {"plan_id": plan.id, "i": op["i"], "want": want, "got": got})
                raise ApplyError(f"{op['describe']} changed different rows than approved; nothing was applied")
            log.append("apply.call", {"plan_id": plan.id, "i": op["i"], "tool": op["tool"], "describe": op["describe"]})
        new_id = ws.next_prod_id()
        prod.freeze(new_id, f"applied {plan.id} (approved by {approval['actor']['id']})")
    ws.set_prod_head(new_id)
    approval["applied_to"] = new_id
    ws.write_json(ws.approval_path(plan.id), approval, exclusive=False)
    result = {"plan_id": plan.id, "previous": head, "head": new_id, "applied": len(operations)}
    log.append("apply.done", result)
    return result
