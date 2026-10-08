# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Milestone 3: seeded faults, each decision a logged record with its seed.

The two constraints this file pins:
  * Every fault decision is in the run log, with the seed and key that produced it, before
    its effect. The log alone is enough to say what happened (no re-drawing).
  * Reorder happens only within a batch due at the same instant (decision 6). Order across
    instants changes only through logged delays.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest

from agent_staging import spike
from agent_staging.faults import Faults
from agent_staging.harness import Run
from agent_staging.kernel import Event
from agent_staging.runlog import RunLog
from agent_staging.session import AgentToolError, Session
from agent_staging.workspace import Workspace

T0 = spike.START


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = Workspace(tmp_path / ".staging")
    spike.seed(w)
    return w


@contextmanager
def a_run(ws: Workspace, faults: Faults, name: str = "t") -> Iterator[Run]:
    log = RunLog.create(ws.root / "runs" / name / "log.jsonl", {"kind": "test"})
    with spike.twin(ws).open("prod-0001") as branch:
        yield Run.start(branch, log, faults)


def records(run: Run, type_: str | None = None) -> list[dict]:
    return [r for r in run.log.records() if type_ is None or r["type"] == type_]


def faults_of(run: Run, kind: str) -> list[dict]:
    return [r["data"] for r in records(run, "fault") if r["data"]["kind"] == kind]


def delivered(run: Run) -> list[str]:
    return [r["data"]["delivery"] for r in records(run, "event.delivered")]


def refund_both(run: Run, *, gap: float = 0) -> None:
    """Two refunds, `gap` seconds apart: events 1 and 2."""
    run.session.call("refund_charge", charge_id="charge-0001", amount=100)
    if gap:
        run.advance(gap)
    run.session.call("refund_charge", charge_id="charge-0002", amount=100)


# --- every decision is a record with its seed --------------------------------------------------


def test_every_schedule_is_logged_with_its_seed_even_with_no_faults(ws: Workspace) -> None:
    with a_run(ws, Faults(seed=11)) as run:
        refund_both(run)
        run.advance(1)
    schedules = faults_of(run, "webhook.schedule")
    assert [(s["event_seq"], s["delay_ms"], s["seed"], s["key"]) for s in schedules] == [
        (1, 0, 11, "webhook/1"),
        (2, 0, 11, "webhook/2"),
    ]
    (profile,) = records(run, "faults.profile")
    assert profile["data"]["seed"] == 11


def test_a_decision_is_logged_before_its_effect(ws: Workspace) -> None:
    with a_run(ws, Faults(seed=1, webhook_delay=(3, 3), at_request={2: "rate_limited"})) as run:
        run.session.call("refund_charge", charge_id="charge-0001", amount=100)
        with pytest.raises(AgentToolError):
            run.session.call("refund_charge", charge_id="charge-0002", amount=100)
        run.advance(5)
    log = [(r["type"], r["data"].get("kind")) for r in records(run)]
    assert log.index(("fault", "webhook.schedule")) < log.index(("event.delivered", None))
    rate_limit = log.index(("fault", "request.rate_limited"))
    assert log[rate_limit + 1] == ("call", None)  # the decision, then the request it failed


def test_all_fault_records_carry_seed_and_key(ws: Workspace) -> None:
    f = Faults(seed=5, webhook_delay=(0, 2), duplicate_rate=1, reorder_rate=1, rate_limit_rate=0.3, timeout_rate=0.3)
    with a_run(ws, f) as run:
        for charge in ["charge-0001"] * 6 + ["charge-0002"] * 4:
            try:
                run.session.call("refund_charge", charge_id=charge, amount=100)
            except AgentToolError:
                pass
        run.advance(10)
    assert records(run, "fault")
    assert all(r["data"]["seed"] == 5 and r["data"]["key"] for r in records(run, "fault"))


# --- delay and duplicate ---------------------------------------------------------------------


def test_delays_are_drawn_per_event_within_bounds_in_ms_steps(ws: Workspace) -> None:
    with a_run(ws, Faults(seed=2, webhook_delay=(1, 30))) as run:
        for _ in range(5):
            run.session.call("refund_charge", charge_id="charge-0001", amount=100)
    delays = [s["delay_ms"] for s in faults_of(run, "webhook.schedule")]
    assert all(1000 <= d <= 30000 for d in delays) and len(set(delays)) > 1


def test_a_duplicated_webhook_arrives_twice_and_a_deduplicating_agent_acts_once(ws: Workspace) -> None:
    handled: list[str] = []
    seen: set[str] = set()

    def handler(session: Session, event: Event) -> None:
        if event.id in seen:  # the right thing: drop duplicates by event id
            return
        seen.add(event.id)
        handled.append(event.id)

    with a_run(ws, Faults(seed=3, webhook_delay=(10, 10), duplicate_rate=1, duplicate_gap=(5, 5))) as run:
        run.session.on_event(handler)
        run.session.call("refund_charge", charge_id="charge-0001", amount=100)
        run.advance(60)
    assert delivered(run) == ["1.0", "1.1"]
    assert [r["data"]["delivered_at"] for r in records(run, "event.delivered")] == [at(10), at(15)]
    assert faults_of(run, "webhook.duplicate")[0] | {"seed": 3} == {
        "kind": "webhook.duplicate",
        "seed": 3,
        "key": "webhook/1",
        "event_seq": 1,
        "copy": 1,
        "gap_ms": 5000,
        "due": at(15),
        "event_id": "evt-0002",
        "type": "refund.created",
    }
    assert handled == ["evt-0002"]


# --- reorder: the boundary (decision 6) ------------------------------------------------------


def test_reorder_happens_within_a_same_instant_batch(ws: Workspace) -> None:
    with a_run(ws, Faults(seed=4, webhook_delay=(10, 10), reorder_rate=1)) as run:
        refund_both(run)  # both emitted at T+0, both due at T+10: one batch
        run.advance(20)
    assert delivered(run) == ["2.0", "1.0"]
    (reorder,) = faults_of(run, "webhook.reorder")
    assert reorder["emitted_order"] == ["1.0", "2.0"] and reorder["delivery_order"] == ["2.0", "1.0"]
    assert reorder["due"] == at(10)


def test_reorder_never_crosses_instants_even_a_millisecond_apart(ws: Workspace) -> None:
    """The boundary: due 1 ms apart is two batches, so reorder_rate=1 cannot swap them."""
    with a_run(ws, Faults(seed=4, webhook_delay=(10, 10), reorder_rate=1)) as run:
        refund_both(run, gap=0.001)  # due at T+10.000 and T+10.001
        run.advance(20)
    assert delivered(run) == ["1.0", "2.0"]
    assert faults_of(run, "webhook.reorder") == []  # batches of one: nothing to reorder


def test_a_same_instant_batch_without_reorder_keeps_emission_order_and_is_still_logged(ws: Workspace) -> None:
    with a_run(ws, Faults(seed=4, webhook_delay=(10, 10), reorder_rate=0)) as run:
        refund_both(run)
        run.advance(20)
    assert delivered(run) == ["1.0", "2.0"]
    (batch,) = faults_of(run, "webhook.batch")
    assert batch["delivery_order"] == ["1.0", "2.0"]


def test_cross_instant_inversions_come_only_from_logged_delays(ws: Workspace) -> None:
    """With random delays, later events sometimes arrive first. Rebuild the delivery order
    from the log's decisions alone (no seed, no re-drawing) and check it is what happened."""
    inverted = 0
    for seed in range(15):
        with a_run(ws, Faults(seed=seed, webhook_delay=(0, 5), reorder_rate=0.5), name=f"s{seed}") as run:
            for _ in range(4):
                run.session.call("refund_charge", charge_id="charge-0001", amount=100)
                run.advance(0.5)
            run.advance(10)
        due = {f"{s['event_seq']}.{s['copy']}": s["due"] for s in faults_of(run, "webhook.schedule")}
        batch_orders = {
            r["data"]["due"]: r["data"]["delivery_order"]
            for r in records(run, "fault")
            if r["data"]["kind"] in ("webhook.batch", "webhook.reorder")
        }
        rebuilt: list[str] = []
        for instant in sorted(set(due.values())):
            group = [d for d in due if due[d] == instant]
            rebuilt += batch_orders.get(instant, group)
        assert rebuilt == delivered(run), seed
        inverted += delivered(run) != sorted(delivered(run), key=lambda d: int(d.split(".")[0]))
    assert inverted  # the delays did invert order at least once, so the check above means something


def test_how_finely_the_harness_steps_time_changes_nothing(ws: Workspace) -> None:
    f = Faults(seed=9, webhook_delay=(0, 3), duplicate_rate=0.5, duplicate_gap=(0, 1), reorder_rate=0.5)

    def deliveries(steps: list[float], name: str) -> list[tuple[str, str]]:
        with a_run(ws, f, name=name) as run:
            for _ in range(4):
                run.session.call("refund_charge", charge_id="charge-0001", amount=100)
            for step in steps:
                run.advance(step)
        return [(r["data"]["delivery"], r["data"]["delivered_at"]) for r in records(run, "event.delivered")]

    once = deliveries([10], "once")
    assert len(once) >= 4
    assert deliveries([0.1, 9.9], "two") == once
    assert deliveries([0.001] * 100 + [0.1] * 99, "fine") == once  # 199 steps to the same T+10


# --- request faults -------------------------------------------------------------------------


def test_429_on_request_3_with_retry_after(ws: Workspace) -> None:
    with a_run(ws, Faults(seed=6, at_request={3: "rate_limited"}, retry_after=2)) as run:
        run.session.call("get_charge", charge_id="charge-0001")
        run.session.call("get_charge", charge_id="charge-0002")
        with pytest.raises(AgentToolError) as e:
            run.session.call("refund_charge", charge_id="charge-0001")
        assert (e.value.code, e.value.retry_after) == ("rate_limited", 2)
        assert len(run.branch.calls()) == 2  # the twin never saw request 3
        run.advance(e.value.retry_after)
        run.session.call("refund_charge", charge_id="charge-0001")  # request 4, after waiting
    (fault,) = faults_of(run, "request.rate_limited")
    assert fault == {
        "kind": "request.rate_limited",
        "seed": 6,
        "key": "request/3",
        "source": "pinned",
        "request": 3,
        "tool": "refund_charge",
        "retry_after": 2,
    }
    calls = [r["data"] for r in records(run, "call")]
    assert [(c["request"], c["i"], c["reached_twin"]) for c in calls] == [
        (1, 0, True),
        (2, 1, True),
        (3, None, False),
        (4, 2, True),
    ]


def test_a_timeout_before_sending_leaves_the_twin_untouched(ws: Workspace) -> None:
    with a_run(ws, Faults(at_request={1: "not_sent"})) as run:
        with pytest.raises(AgentToolError, match="may or may not have been applied") as e:
            run.session.call("refund_charge", charge_id="charge-0001", amount=5000)
        assert e.value.code == "timeout"
        assert run.branch.changes() == [] and run.branch.events() == []


def test_a_lost_response_timeout_lets_a_naive_retry_refund_twice(ws: Workspace) -> None:
    """The failure this exists to show: the twin refunded, the agent saw a timeout, retried,
    and the customer got $100 back on a $50 request. The invariant engine (milestone 4)
    turns this into a failed run; here it is only visible, and fully logged."""

    def naive_refund(session: Session) -> None:
        for _ in range(2):
            try:
                session.call("refund_charge", charge_id="charge-0001", amount=5000)
                return
            except AgentToolError as e:
                if e.code != "timeout":
                    raise

    with a_run(ws, Faults(at_request={1: "lost_response"})) as run:
        naive_refund(run.session)
        charge = run.branch.row("charges", {"id": "charge-0001"})
    assert charge is not None and charge["amount_refunded"] == 10000  # two refunds of $50
    first = records(run, "call")[0]["data"]
    assert (first["fault"], first["reached_twin"], first["error"]["code"]) == ("lost_response", True, "timeout")
    assert first["result"]["amount"] == 5000  # what the twin did, though the agent never saw it


def test_drawn_request_faults_are_deterministic_per_seed(ws: Workspace) -> None:
    names = iter(range(100))

    def kinds(seed: int) -> list[str]:
        f = Faults(seed=seed, rate_limit_rate=0.25, timeout_rate=0.25)
        with a_run(ws, f, name=f"k{next(names)}") as run:
            for _ in range(20):
                try:
                    run.session.call("get_charge", charge_id="charge-0001")
                except AgentToolError:
                    pass
        return [d["kind"] for d in (r["data"] for r in records(run, "fault"))]

    assert kinds(1) == kinds(1)
    assert kinds(1) != kinds(2)
    assert {"request.rate_limited", "request.not_sent", "request.lost_response"} <= set(kinds(1) + kinds(2))


def test_no_rates_means_no_request_faults(ws: Workspace) -> None:
    with a_run(ws, Faults(seed=1)) as run:
        for _ in range(10):
            run.session.call("get_charge", charge_id="charge-0001")
        assert len(run.branch.calls()) == 10
    assert [r["data"]["kind"] for r in records(run, "fault")] == []


@pytest.mark.parametrize(
    "bad",
    [
        {"duplicate_rate": 1.5},
        {"webhook_delay": (5, 1)},
        {"webhook_delay": (-1, 1)},
        {"rate_limit_rate": 0.6, "timeout_rate": 0.6},
        {"at_request": {0: "rate_limited"}},
        {"at_request": {1: "explode"}},
    ],
)
def test_impossible_profiles_are_refused(bad: dict) -> None:
    with pytest.raises(ValueError):
        Faults(**bad)
