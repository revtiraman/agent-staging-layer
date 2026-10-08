# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Milestone 2: the kernel interface on refund_path, and the harness-owned clock.

The webhook-delay scenario is the one milestone 3 will run with seeded faults; here it
proves the interface can express it: request at T+0, webhook due at T+40, the agent acts
at T+10 before it arrives, then the harness advances 40 s and the webhook fires at T+40.
Since milestone 3 the delay is a (fixed) fault profile, and delivery is the harness's.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agent_staging import dryrun, spike
from agent_staging.faults import Faults
from agent_staging.harness import Run
from agent_staging.kernel import Event
from agent_staging.runlog import RunLog
from agent_staging.session import AgentToolError, Session
from agent_staging.twins.refund_path import RefundPath
from agent_staging.twins.seahaven_twin import KernelError
from agent_staging.workspace import Workspace

T0 = spike.START


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = Workspace(tmp_path / ".staging")
    spike.seed(w)
    return w


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


# --- the clock ------------------------------------------------------------------------------


def test_a_branch_starts_at_its_states_time_and_moves_only_on_advance(ws: Workspace) -> None:
    twin = spike.twin(ws)
    assert twin.state("prod-0001").now == T0
    with twin.open("prod-0001") as branch:
        assert branch.now() == T0
        branch.call("get_charge", charge_id="charge-0001")
        branch.call("create_customer", email="dee@example.com")
        assert branch.now() == T0  # calls don't move time (no `tick`)
        branch.advance(10)
        assert branch.now() == at(10)
        branch.advance(timedelta(milliseconds=1500))
        assert branch.now() == at(11.5)


def test_every_door_into_the_world_sees_the_harness_time(ws: Workspace) -> None:
    """Tools (ctx.clock), SQL ('now') and freeze() all read the moved clock."""
    twin = spike.twin(ws)
    with twin.open("prod-0001") as branch:
        branch.advance(90)
        branch.call("refund_charge", charge_id="charge-0001", amount=100)
        refund = branch.row("refunds", {"id": "refund-0002"})
        assert refund is not None and refund["created_at"] == "2026-10-01T09:01:30.000Z"
        sql = branch._inst.db.one("SELECT datetime('now') AS a, CURRENT_TIMESTAMP AS b")  # type: ignore[attr-defined]
        assert sql == {
            "a": "2026-10-01T09:01:30.000Z",
            "b": "2026-10-01T09:01:30.000Z",
        }  # Seahaven renders canonical text
        meta = branch.freeze("later", "after 90 s")
    assert meta.now == at(90)
    with twin.open("later") as again:
        assert again.now() == at(90)  # a saved state keeps its time


def test_time_only_moves_forward(ws: Workspace) -> None:
    with spike.twin(ws).open("prod-0001") as branch, pytest.raises(ValueError, match="forward"):
        branch.advance(-1)


def test_an_empty_twin_needs_a_start_time(tmp_path: Path) -> None:
    with pytest.raises(KernelError, match="start time"), RefundPath(tmp_path).open(None):
        pass


def test_the_agent_cannot_move_the_clock() -> None:
    assert not hasattr(Session, "advance") and not hasattr(Session, "now")


def test_world_code_never_reads_the_wall_clock() -> None:
    """The world reads time only through the kernel. Scan the twins for wall-clock reads."""
    forbidden = {("datetime", "now"), ("datetime", "utcnow"), ("datetime", "today"), ("date", "today")}
    forbidden_time = {"time", "time_ns", "monotonic", "monotonic_ns", "perf_counter"}
    twins = Path(__file__).parents[1] / "src" / "agent_staging" / "twins"
    found = []
    for path in twins.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                pair = (node.value.id, node.attr)
                if pair in forbidden or (node.value.id == "time" and node.attr in forbidden_time):
                    found.append(f"{path.name}:{node.lineno} {pair[0]}.{pair[1]}")
            if isinstance(node, ast.Name) and node.id == "CURRENT_TIMESTAMP":
                found.append(f"{path.name}:{node.lineno}")
    assert found == []


# --- events and the webhook-delay scenario ----------------------------------------------------


class ForgetfulSupportAgent:
    """Refunds, then follows up at T+10. It trusts only the webhook, so a late webhook makes
    it think the refund failed and try again: the sequence bug a stateless mock can't catch."""

    def __init__(self) -> None:
        self.confirmed: dict[str, dict] = {}
        self.retry_refused: str | None = None

    def on_event(self, session: Session, event: Event) -> None:
        if event.type == "refund.created":
            self.confirmed[event.payload["charge_id"]] = event.payload

    def request(self, session: Session, charge_id: str) -> None:
        session.call("refund_charge", charge_id=charge_id)

    def follow_up(self, session: Session, charge_id: str) -> None:
        if charge_id in self.confirmed:
            return
        try:
            session.call("refund_charge", charge_id=charge_id)  # "no webhook, so it didn't go through"
        except AgentToolError as e:
            self.retry_refused = e.code


def run_webhook_scenario(ws: Workspace, seed: int = 7) -> tuple[ForgetfulSupportAgent, Run, list[dict]]:
    twin = spike.twin(ws)
    log = RunLog.create(ws.root / "runs" / f"scenario-{seed}" / "log.jsonl", {"kind": "scenario", "seed": seed})
    agent = ForgetfulSupportAgent()
    with twin.open("prod-0001", seed=seed) as branch:
        run = Run.start(branch, log, Faults(seed=seed, webhook_delay=(40, 40)))
        run.session.on_event(agent.on_event)

        agent.request(run.session, "charge-0001")  # T+0: the refund, webhook due T+40
        assert [d.due for d in run.pending()] == [at(40)]

        run.advance(10)  # T+10: the agent acts before the webhook arrives
        agent.follow_up(run.session, "charge-0001")
        assert agent.confirmed == {} and run.pending()

        delivered = run.advance(40)  # to T+50; the webhook falls due on the way, at T+40

        assert [(d.event_seq, d.due) for d in delivered] == [(1, at(40))]
        assert run.pending() == [] and run.now() == at(50)
        state = {
            "charge": branch.row("charges", {"id": "charge-0001"}),
            "refunds": [c.after for c in branch.changes() if c.table == "refunds" and c.op == "insert"],
        }
    return agent, run, [state]


def test_webhook_delay_scenario(ws: Workspace) -> None:
    agent, run, (state,) = run_webhook_scenario(ws)

    # the agent's retry at T+10 was refused by the world's own rule, so the state is consistent:
    assert agent.retry_refused == "already_refunded"
    assert state["charge"]["amount_refunded"] == state["charge"]["amount"] == 12000
    assert len(state["refunds"]) == 1  # one refund, not two
    # the webhook reached the agent once, and reported the refund that exists
    assert agent.confirmed == {"charge-0001": {"amount": 12000, "charge_id": "charge-0001", "refund_id": "refund-0002"}}

    records = run.log.records()
    delivered = [r["data"] for r in records if r["type"] == "event.delivered"]
    assert len(delivered) == 1
    assert delivered[0]["emitted_at"] == at(0).isoformat() and delivered[0]["delivered_at"] == at(40).isoformat()
    calls = [(r["data"]["at"], r["data"]["tool"], "error" in r["data"]) for r in records if r["type"] == "call"]
    assert calls == [(at(0).isoformat(), "refund_charge", False), (at(10).isoformat(), "refund_charge", True)]
    assert [r["data"]["to"] for r in records if r["type"] == "clock.advance"] == [
        at(10).isoformat(),
        at(50).isoformat(),
    ]
    assert run.log.verify() == []


@contextmanager
def a_run(ws: Workspace, faults: Faults | None = None, name: str = "t") -> Iterator[Run]:
    log = RunLog.create(ws.root / "runs" / name / "log.jsonl", {"kind": "test"})
    with spike.twin(ws).open("prod-0001") as branch:
        yield Run.start(branch, log, faults)


def test_the_webhook_does_not_arrive_a_millisecond_early(ws: Workspace) -> None:
    with a_run(ws, Faults(webhook_delay=(40, 40))) as run:
        run.session.call("refund_charge", charge_id="charge-0001")
        assert run.advance(timedelta(seconds=39, milliseconds=999)) == []
        assert len(run.advance(timedelta(milliseconds=1))) == 1


def test_events_due_together_arrive_in_emission_order_and_handlers_run_at_delivery_time(ws: Workspace) -> None:
    seen: list[tuple[str, datetime]] = []
    with a_run(ws, Faults(webhook_delay=(5, 5))) as run:

        def handler(session: Session, event: Event) -> None:
            seen.append((event.payload["charge_id"], run.now()))
            if event.seq == 1:  # a handler that writes emits another event
                session.call("refund_charge", charge_id="charge-0002")

        run.session.on_event(handler)
        run.session.call("refund_charge", charge_id="charge-0001", amount=100)
        run.session.call("refund_charge", charge_id="charge-0001", amount=200)
        run.advance(60)
    assert seen == [("charge-0001", at(5)), ("charge-0001", at(5)), ("charge-0002", at(10))]


def test_the_scenario_is_deterministic_under_a_seed(tmp_path: Path) -> None:
    def trace(root: Path) -> list[tuple[str, str]]:
        ws = Workspace(root)
        spike.seed(ws)
        _, run, _ = run_webhook_scenario(ws, seed=3)
        # everything except the wall-clock time each record was written
        return [(r["type"], json.dumps(r["data"], sort_keys=True, default=str)) for r in run.log.records()]

    assert trace(tmp_path / "a") == trace(tmp_path / "b")


def test_a_refused_call_emits_no_event(ws: Workspace) -> None:
    with spike.twin(ws).open("prod-0001") as branch:
        with pytest.raises(Exception, match="already fully refunded"):
            branch.call("refund_charge", charge_id="charge-0003")
        assert branch.events() == []


def test_error_codes_survive_into_the_call_log(ws: Workspace) -> None:
    with spike.twin(ws).open("prod-0001") as branch:
        with pytest.raises(Exception, match="already fully refunded"):
            branch.call("refund_charge", charge_id="charge-0003")
        assert branch.calls()[-1].error == {
            "code": "already_refunded",
            "message": "charge charge-0003 is already fully refunded",
        }


# --- attribution: verified finding about Seahaven's HTTP path --------------------------------


def test_a_tool_call_inside_bulk_loses_its_attribution_and_blocks_the_plan(ws: Workspace) -> None:
    """seahaven.http runs each request inside `bulk()` (http/runtime.py, `dispatch`). A tool
    called from in there is logged as a call, but its rows get `i = None`, so the plan can't
    tie them to it. It fails safe (BLOCKED). The Stripe twin therefore has to make each HTTP
    request one top-level kernel call (docs/kernel-interface.md, rule 3)."""

    def http_style(session: Session) -> None:
        inst = session._branch._inst  # type: ignore[attr-defined]
        with inst.bulk():
            inst.call("refund_charge", charge_id="charge-0001")

    plan, _ = dryrun.dry_run(ws, spike.twin(ws), http_style, agent_name="t")
    assert plan.body["summary"]["to_apply"] == 0
    assert plan.body["summary"]["blocked"] == 1
    assert plan.body["blocked"][0]["reason"] == "rows changed outside any call"


def test_the_plan_marks_assigned_columns(ws: Workspace) -> None:
    plan, _ = dryrun.dry_run(ws, spike.twin(ws), spike.support_agent, agent_name="t")
    assert plan.body["assigned"]["refunds"] == {"id": "id", "created_at": "time"}
    assert plan.body["base"]["now"] == T0.isoformat()
