# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Apply an approved plan to production.

Order (each step refuses before any write):
  1. The plan file still hashes to what was approved, and the approval is one we can trust.
  2. Production has not moved in a way that touches the plan (stale check).
  3. Re-execute exactly the approved calls on a fresh branch of production.
  4. Check the rows they changed equal the plan's rows (clock-assigned columns excepted).
  5. Only then freeze the result as the new production state and move HEAD.
If 3 or 4 fails, the branch is discarded and production is unchanged.

Ids: columns the twin assigns ids to are still compared literally, so an id that shifted
between the dry-run and now aborts the apply (safe, but too strict). Milestone 6 maps
them by name instead (docs/spike-notes.md, "Planted for milestone 6").
"""

from __future__ import annotations

from typing import Any

from agent_staging.approval import ApprovalError, load_plan
from agent_staging.kernel import Branch, TwinKernel, TwinToolError
from agent_staging.runlog import RunLog
from agent_staging.workspace import Workspace


class ApplyError(Exception):
    pass


def _strip_clock(change: dict[str, Any], assigned: dict[str, dict[str, str]]) -> dict[str, Any]:
    clock_cols = {col for col, kind in assigned.get(change["table"], {}).items() if kind == "time"}

    def side(row: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if row is None else {k: v for k, v in row.items() if k not in clock_cols}

    return {**change, "before": side(change["before"]), "after": side(change["after"])}


def stale_reasons(branch: Branch, operations: list[dict[str, Any]]) -> list[str]:
    """What in current production contradicts the rows the plan was computed from.

    Stale means: a column the plan updates no longer holds the value the plan saw, a row the
    plan inserts already exists, or a row it deletes is gone. Rows the plan doesn't touch can
    change freely (decision 5).
    """
    reasons = []
    for op in operations:
        for c in op["changes"]:
            row = branch.row(c["table"], c["key"])
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


def apply_plan(ws: Workspace, twin: TwinKernel, plan_id: str) -> dict[str, Any]:
    plan = load_plan(ws, plan_id)
    log = RunLog(ws.run_log_path(plan.body["run_id"]))
    if not ws.approval_path(plan.id).exists():
        raise ApprovalError(f"{plan.id} has not been approved: run `staging approve {plan.id}`")
    approval = ws.read_json(ws.approval_path(plan.id))
    if approval["decision"] != "approved":
        raise ApprovalError(f"{plan.id} was {approval['decision']}")
    if approval["plan_hash"] != plan.hash:
        raise ApprovalError(f"{plan.id} changed after it was approved")
    if approval.get("signature") is not None:
        # NOT IMPLEMENTED (milestone 6). Refuse rather than appear to have checked it.
        raise ApprovalError(f"{plan.id} carries a signature, and signature checking is not implemented yet")
    if approval.get("applied_to"):
        raise ApplyError(f"{plan.id} was already applied as {approval['applied_to']}")
    if plan.body["world"]["name"] != twin.name:
        raise ApplyError(f"{plan.id} was made on {plan.body['world']['name']}, not {twin.name}")

    head = ws.prod_head()
    if head is None:
        raise ApplyError("no production state")
    base = plan.body["base"]
    if head == base["state"] and twin.state(head).file_sha256 != base["file_sha256"]:
        raise ApplyError(f"state {head} is not the file the plan was made from")

    operations = plan.body["operations"]
    assigned = plan.body["assigned"]
    with twin.open(head) as prod:
        reasons = stale_reasons(prod, operations) if head != base["state"] else []
        if reasons:
            log.append("apply.refused", {"plan_id": plan.id, "head": head, "stale": reasons})
            raise ApplyError("production changed since the plan was made:\n  " + "\n  ".join(reasons))
        for op in operations:
            start = len(prod.changes())
            try:
                prod.call(op["tool"], **op["arguments"])
            except TwinToolError as e:
                log.append("apply.aborted", {"plan_id": plan.id, "i": op["i"], "error": e.message})
                raise ApplyError(f"{op['describe']} failed on production: {e}; nothing was applied") from None
            got = [_strip_clock(c.to_dict(), assigned) for c in prod.changes()[start:]]
            want = [_strip_clock(c, assigned) for c in op["changes"]]
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
