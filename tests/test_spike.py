# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Milestone 1 spike: the success criterion end to end, plus the refusals around it.

Approval is driven through a real pseudo-terminal: the CLI runs in a child process whose
controlling terminal is the pty, exactly as for a human, so nothing about the approval
path is mocked.
"""

from __future__ import annotations

import json
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

# The child execs immediately after forkpty(), so the multi-threaded fork warning does not apply.
pytestmark = pytest.mark.filterwarnings("ignore:This process .* use of forkpty:DeprecationWarning")

from agent_staging import apply, dryrun, spike
from agent_staging.approval import ApprovalError, decide
from agent_staging.dryrun import DryRunError, Plan
from agent_staging.runlog import RunLog
from agent_staging.twins.refund_path import make_world
from agent_staging.workspace import Workspace


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = Workspace(tmp_path / ".staging")
    spike.seed(w)
    return w


def approve_at_terminal(ws: Workspace, plan_id: str, typed: str) -> tuple[int, str]:
    """Run `staging approve` with a pty as its controlling terminal and type `typed`."""
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.environ["STAGING_HOME"] = str(ws.root)
        os.execv(sys.executable, [sys.executable, "-m", "agent_staging.cli", "approve", plan_id])
    out = b""

    def read_until(done: bytes | None, seconds: float = 30) -> None:
        nonlocal out
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and (done is None or done not in out):
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:  # the child closed the terminal
                return
            if not chunk:
                return
            out += chunk
        if done is not None and done not in out:
            os.kill(pid, 9)
            raise AssertionError(f"no prompt from `staging approve`; screen so far:\n{out.decode()}")

    read_until(b"> ")
    os.write(fd, typed.encode() + b"\n")
    read_until(None)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), out.decode()


def prod_rows(ws: Workspace, sql: str) -> list[dict]:
    world = make_world(ws.fixtures)
    with world.instance(ws.prod_head()) as inst:
        return inst.db.rows(sql)


def test_success_criterion_end_to_end(ws: Workspace) -> None:
    before = prod_rows(ws, "SELECT id, amount_refunded FROM charges ORDER BY id")

    plan, run_id = dryrun.dry_run(ws, spike.support_agent, agent_name="spike.support_agent")

    # the agent proposed three refunds: two valid, one skipped
    s = plan.body["summary"]
    assert (s["proposed"], s["to_apply"], s["skipped"], s["blocked"]) == (3, 2, 1, 0)
    assert s["amount"] == 20000
    assert [op["describe"] for op in plan.body["operations"]] == [
        "Refund $120.00 on charge-0001 to cust-0001 <ana@example.com>",
        "Refund $80.00 on charge-0002 to cust-0002 <ben@example.com>",
    ]
    assert plan.body["skipped"][0]["reason"] == "charge charge-0003 is already fully refunded"
    # the dry-run did not touch production
    assert prod_rows(ws, "SELECT id, amount_refunded FROM charges ORDER BY id") == before

    # a human types the confirmation at the terminal
    code, screen = approve_at_terminal(ws, plan.id, f"approve {plan.hash[:8]}")
    assert code == 0, screen
    assert "Refund $120.00 on charge-0001" in screen and "APPROVED" in screen

    # the two refunds are applied to the twin
    result = apply.apply_plan(ws, plan.id)
    assert result == {"plan_id": plan.id, "previous": "prod-0001", "head": "prod-0002", "applied": 2}
    assert prod_rows(ws, "SELECT id, amount, amount_refunded FROM charges ORDER BY id") == [
        {"id": "charge-0001", "amount": 12000, "amount_refunded": 12000},
        {"id": "charge-0002", "amount": 8000, "amount_refunded": 8000},
        {"id": "charge-0003", "amount": 9500, "amount_refunded": 9500},
    ]
    assert len(prod_rows(ws, "SELECT id FROM refunds")) == 3  # support's + the agent's two

    # and the whole run is logged, with an intact hash chain
    log = RunLog(ws.run_log_path(run_id))
    types = [r["type"] for r in log.records()]
    assert types[0] == "run.start" and types[-1] == "apply.done"
    assert types.count("call") == 6  # 3 reads + 3 refund attempts
    assert {"plan.created", "plan.decision"} <= set(types) and types.count("apply.call") == 2
    assert log.verify() == []


def test_wrong_confirmation_rejects_and_apply_refuses(ws: Workspace) -> None:
    plan, _ = dryrun.dry_run(ws, spike.support_agent, agent_name="t")
    code, screen = approve_at_terminal(ws, plan.id, "yes")
    assert code == 1 and "REJECTED" in screen
    with pytest.raises(ApprovalError, match="was rejected"):
        apply.apply_plan(ws, plan.id)
    assert ws.prod_head() == "prod-0001"


def test_no_terminal_means_no_approval(ws: Workspace) -> None:
    plan, _ = dryrun.dry_run(ws, spike.support_agent, agent_name="t")
    proc = subprocess.run(
        [sys.executable, "-m", "agent_staging.cli", "approve", plan.id],
        input=f"approve {plan.hash[:8]}\n",
        capture_output=True,
        text=True,
        env={**os.environ, "STAGING_HOME": str(ws.root)},
        start_new_session=True,
        check=False,  # no controlling tty
    )
    assert proc.returncode == 2 and "no controlling terminal" in proc.stderr
    assert not ws.approval_path(plan.id).exists()


def test_editing_an_approved_plan_voids_it(ws: Workspace) -> None:
    plan, _ = dryrun.dry_run(ws, spike.support_agent, agent_name="t")
    assert approve_at_terminal(ws, plan.id, f"approve {plan.hash[:8]}")[0] == 0
    path = ws.plan_path(plan.id)
    data = json.loads(path.read_text())
    data["body"]["operations"][0]["arguments"]["amount"] = 1  # change what gets applied
    path.write_text(json.dumps(data))
    with pytest.raises(DryRunError, match="does not match its own hash"):
        apply.apply_plan(ws, plan.id)
    assert ws.prod_head() == "prod-0001"


def _move_production(ws: Workspace, *calls: tuple[str, dict[str, object]]) -> None:
    world = make_world(ws.fixtures)
    with world.instance(ws.prod_head()) as inst:
        for tool, arguments in calls:
            inst.call(tool, **arguments)
        new_id = ws.next_prod_id()
        inst.freeze(new_id, "changed outside the plan")
    ws.set_prod_head(new_id)


def test_stale_when_production_changed_a_touched_row(ws: Workspace) -> None:
    plan, _ = dryrun.dry_run(ws, spike.support_agent, agent_name="t")
    assert approve_at_terminal(ws, plan.id, f"approve {plan.hash[:8]}")[0] == 0
    _move_production(ws, ("refund_charge", {"charge_id": "charge-0001", "amount": 1000}))  # someone refunded $10
    with pytest.raises(apply.ApplyError, match="charges charge-0001.amount_refunded is 1000, plan expected 0"):
        apply.apply_plan(ws, plan.id)
    assert ws.prod_head() == "prod-0002"  # unchanged by the refused apply


def test_unrelated_production_change_is_not_stale(ws: Workspace) -> None:
    plan, _ = dryrun.dry_run(ws, spike.support_agent, agent_name="t")
    assert approve_at_terminal(ws, plan.id, f"approve {plan.hash[:8]}")[0] == 0
    _move_production(ws, ("create_customer", {"email": "dee@example.com"}))  # prod-0002
    result = apply.apply_plan(ws, plan.id)
    assert (result["previous"], result["head"], result["applied"]) == ("prod-0002", "prod-0003", 2)
    assert len(prod_rows(ws, "SELECT id FROM customers")) == 4


def test_unrelated_refund_shifts_ids_and_apply_aborts_safely(ws: Workspace) -> None:
    """Known limitation (spike-notes, open issue 1): ids are counters, so an unrelated refund
    makes the replayed refunds get different ids than approved. Apply refuses; nothing lands."""
    plan, _ = dryrun.dry_run(ws, spike.support_agent, agent_name="t")
    assert approve_at_terminal(ws, plan.id, f"approve {plan.hash[:8]}")[0] == 0
    _move_production(
        ws,
        ("create_customer", {"email": "dee@example.com"}),
        ("create_charge", {"customer_id": "cust-0004", "amount": 500}),
        ("refund_charge", {"charge_id": "charge-0004"}),
    )
    with pytest.raises(apply.ApplyError, match="refunds refund-0002 already exists"):
        apply.apply_plan(ws, plan.id)
    assert ws.prod_head() == "prod-0002"


def test_tampered_run_log_is_detected(ws: Workspace) -> None:
    _, run_id = dryrun.dry_run(ws, spike.support_agent, agent_name="t")
    path = ws.run_log_path(run_id)
    lines = path.read_text().splitlines()
    lines[2] = lines[2].replace("charge-0001", "charge-0009")
    path.write_text("\n".join(lines) + "\n")
    assert any("does not match its hash" in p for p in RunLog(path).verify())


def test_a_write_outside_any_call_blocks_the_plan(ws: Workspace) -> None:
    def sneaky(session: object) -> None:
        inst = session._instance  # type: ignore[attr-defined]
        with inst.bulk() as ctx:  # a write with no tool call in flight
            ctx.db.execute("UPDATE charges SET amount_refunded = 1 WHERE id = 'charge-0001'")

    plan, _ = dryrun.dry_run(ws, sneaky, agent_name="t")
    assert plan.body["summary"]["blocked"] == 1
    with pytest.raises(ApprovalError, match="blocked writes"):
        decide(ws, plan.id)


def test_plan_file_round_trip(ws: Workspace) -> None:
    plan, _ = dryrun.dry_run(ws, spike.support_agent, agent_name="t")
    again = Plan.from_file(json.loads(ws.plan_path(plan.id).read_text()))
    assert again.hash == plan.hash
