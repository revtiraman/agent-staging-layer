# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Milestone 4: hard invariants over (state, changes, faults, intent), failures as log records,
and replay that re-evaluates them from the log alone.

The demo scenario: 2,000 seeded charges, one $50 refund request, a lost-response timeout on
request 1, an agent that retries, a twin that (correctly) accepts both refunds, and
`refunded_le_requested` failing the run. Then replay reproduces the failure from the log.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_staging import spike
from agent_staging.faults import Faults
from agent_staging.harness import Run
from agent_staging.invariants import BaseState, Engine, Intent, State
from agent_staging.replay import ReplayError, replay
from agent_staging.runlog import RunLog
from agent_staging.session import AgentToolError, Session
from agent_staging.twins.refund_path_invariants import INVARIANTS, RefundedLeRequested
from agent_staging.workspace import Workspace

CHARGES = 2000
TARGET = "charge-1234"  # one charge among the 2,000
REFUND = {"action": "refund", "charge_id": TARGET, "amount": 5000}


@pytest.fixture(scope="module")
def ws(tmp_path_factory: pytest.TempPathFactory) -> Workspace:
    """2,000 customers with one $120 charge each, saved as orders-2000. Built once per module;
    tests only branch it, never move it."""
    w = Workspace(tmp_path_factory.mktemp("inv") / ".staging")
    with spike.twin(w).open(None, start=spike.START) as branch:
        for n in range(CHARGES):
            customer = branch.call("create_customer", email=f"c{n:04d}@example.com")
            branch.call("create_charge", customer_id=customer["id"], amount=12000)
        branch.freeze("orders-2000", f"{CHARGES} customers, one $120 charge each")
    return w


names = iter(range(10_000))


@contextmanager
def checked_run(ws: Workspace, faults: Faults, intent: Intent, invariants=INVARIANTS) -> Iterator[Run]:
    twin = spike.twin(ws)
    run_id = f"run-inv-{next(names)}"
    log = RunLog.create(ws.run_log_path(run_id), {"kind": "scenario", "twin": twin.name, "run_id": run_id})
    with BaseState.open(twin, "orders-2000") as base, twin.open("orders-2000") as branch:
        run = Run.start(branch, log, faults, intent=intent, engine=Engine(invariants, base))
        run.run_id = run_id  # type: ignore[attr-defined]
        yield run
        run.finish()


def retrying_refund(session: Session) -> None:
    """Refund $50, and on a timeout simply try again. The bug: a timeout may have succeeded."""
    for _ in range(2):
        try:
            session.call("refund_charge", charge_id=TARGET, amount=5000)
            return
        except AgentToolError as e:
            if e.code != "timeout":
                raise


def of_type(run: Run, type_: str) -> list[dict]:
    return [r for r in run.log.records() if r["type"] == type_]


# --- rule 1: refunded_le_requested -----------------------------------------------------------


def test_lost_response_retry_refunds_twice_and_refunded_le_requested_fails_the_run(ws: Workspace) -> None:
    with checked_run(ws, Faults(seed=42, at_request={1: "lost_response"}), Intent((REFUND,))) as run:
        retrying_refund(run.session)
        charge = run.branch.row("charges", {"id": TARGET})
    # the twin is faithful: both $50 refunds are valid, and it accepted them
    assert charge is not None and charge["amount_refunded"] == 10000 <= charge["amount"]

    failures = {r["data"]["invariant"]: r for r in of_type(run, "invariant.failed")}
    assert set(failures) == {"refunded_le_requested", "no_retry_after_lost_response_timeout"}
    failed = failures["refunded_le_requested"]
    request_2 = next(r for r in of_type(run, "call") if r["data"]["request"] == 2)
    f = failed["data"]
    assert (f["invariant"], f["version"], f["subject"], f["seed"]) == (
        "refunded_le_requested",
        1,
        f"charge {TARGET}",
        42,
    )
    assert f["step"] == request_2["seq"]  # it fails at the retry, not at the end
    assert f["message"] == f"{TARGET}: $100.00 refunded during the run in 2 refund(s); $50.00 was requested"
    assert f["evidence"]["faults"] == ["request/1"] and f["evidence"]["requests"] == [1, 2]
    rows = f["evidence"]["rows"]
    assert [(r["table"], r["key"]["id"]) for r in rows] == [
        ("charges", TARGET),
        ("refunds", "refund-0001"),
        ("refunds", "refund-0002"),
    ]
    assert rows[0]["values"]["amount_refunded"] == 10000

    (end,) = of_type(run, "run.end")
    assert end["data"] == {"outcome": "failed", "violations": sorted(r["seq"] for r in failures.values())}
    assert run.log.verify() == []


def test_replay_reproduces_the_failure_from_the_log_alone(ws: Workspace) -> None:
    with checked_run(ws, Faults(seed=42, at_request={1: "lost_response"}), Intent((REFUND,))) as run:
        retrying_refund(run.session)
    recorded = [r["data"] for r in of_type(run, "invariant.failed")]

    result = replay(ws, spike.twin(ws), run.run_id, INVARIANTS)  # type: ignore[attr-defined]

    assert result["verdict"] == "reproduced"
    assert result["invariants"] == [
        {"invariant": "refunded_le_requested", "recorded_version": 1, "current_version": 1, "outcome": "same"},
        {
            "invariant": "no_retry_after_lost_response_timeout",
            "recorded_version": 1,
            "current_version": 1,
            "outcome": "same",
        },
    ]
    assert result["failures"] == recorded  # the same failure, the same step, the same evidence
    saved = sorted((ws.run_log_path(run.run_id).parent / "replays").glob("replay-*.json"))  # type: ignore[attr-defined]
    assert json.loads(saved[-1].read_text())["verdict"] == "reproduced"


def test_an_agent_that_reads_after_a_timeout_passes(ws: Workspace) -> None:
    def careful(session: Session) -> None:
        try:
            session.call("refund_charge", charge_id=TARGET, amount=5000)
        except AgentToolError as e:
            assert e.code == "timeout"
            charge = session.call("get_charge", charge_id=TARGET)
            if charge["amount_refunded"] < 5000:  # it did go through, so don't refund again
                session.call("refund_charge", charge_id=TARGET, amount=5000)

    with checked_run(ws, Faults(seed=42, at_request={1: "lost_response"}), Intent((REFUND,))) as run:
        careful(run.session)
    assert of_type(run, "invariant.failed") == []
    assert of_type(run, "run.end")[0]["data"] == {"outcome": "passed", "violations": []}


def test_refunding_a_charge_nobody_asked_about_fails(ws: Workspace) -> None:
    with checked_run(ws, Faults(), Intent((REFUND,))) as run:
        run.session.call("refund_charge", charge_id="charge-0007", amount=100)
    (failed,) = [r["data"] for r in of_type(run, "invariant.failed")]
    assert failed["subject"] == "charge charge-0007" and "$0.00 was requested" in failed["message"]


def test_refund_the_remainder_means_the_base_remainder(ws: Workspace) -> None:
    remainder = {"action": "refund", "charge_id": TARGET}  # no amount
    with checked_run(ws, Faults(), Intent((remainder,))) as run:
        run.session.call("refund_charge", charge_id=TARGET)  # $120, all of it
    assert of_type(run, "invariant.failed") == []


def test_a_failure_is_reported_once_per_subject(ws: Workspace) -> None:
    with checked_run(ws, Faults(), Intent()) as run:
        for _ in range(3):
            run.session.call("refund_charge", charge_id="charge-0001", amount=100)
    assert len(of_type(run, "invariant.failed")) == 1


# --- replay verdicts -------------------------------------------------------------------------


class RefundedLeRequestedV2(RefundedLeRequested):
    """Same logic, new version: replay must reproduce and note the mismatch."""

    version = 2


class RefundedLeDouble(RefundedLeRequested):
    """Different logic (allows refunding up to twice the request), new version."""

    version = 3

    def check(self, state, changes, faults, intent):  # type: ignore[no-untyped-def]
        doubled = Intent(tuple({**r, "amount": r["amount"] * 2} for r in intent.requests))
        return super().check(state, changes, faults, doubled)


def _failed_run(ws: Workspace) -> str:
    with checked_run(ws, Faults(seed=7, at_request={1: "lost_response"}), Intent((REFUND,))) as run:
        retrying_refund(run.session)
    return run.run_id  # type: ignore[attr-defined]


def test_replay_same_outcome_new_version_succeeds_and_notes_the_mismatch(ws: Workspace) -> None:
    result = replay(ws, spike.twin(ws), _failed_run(ws), (RefundedLeRequestedV2(), INVARIANTS[1]))
    assert result["verdict"] == "reproduced"
    assert result["invariants"][0]["note"] == "version mismatch: recorded with version 1, replayed with version 2"


def test_replay_different_outcome_new_version_diverges_with_the_step(ws: Workspace) -> None:
    run_id = _failed_run(ws)
    step = next(
        r["data"]["step"]
        for r in RunLog(ws.run_log_path(run_id)).records()
        if r["type"] == "invariant.failed" and r["data"]["invariant"] == "refunded_le_requested"
    )
    result = replay(ws, spike.twin(ws), run_id, (RefundedLeDouble(), INVARIANTS[1]))
    assert result["verdict"] == "diverged"
    assert result["invariants"][0]["note"] == (
        f"invariant refunded_le_requested version 1 vs version 3 produced different results at step {step}"
    )


def test_replay_refuses_a_tampered_log(ws: Workspace) -> None:
    run_id = _failed_run(ws)
    path = ws.run_log_path(run_id)
    path.write_text(path.read_text().replace('"amount":5000', '"amount":9000', 1))
    with pytest.raises(ReplayError, match="not intact"):
        replay(ws, spike.twin(ws), run_id, INVARIANTS)


def test_replay_refuses_a_changed_base_state(ws: Workspace, tmp_path: Path) -> None:
    run_id = _failed_run(ws)
    path = ws.run_log_path(run_id)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    base = next(r for r in records if r["type"] == "base.state")
    assert base["data"]["state"] == "orders-2000"
    with BaseState.open(spike.twin(ws), "orders-2000") as b:
        assert b.meta.file_sha256 == base["data"]["file_sha256"]
    with (
        pytest.raises(ValueError, match="sha256 differs"),
        BaseState.open(spike.twin(ws), "orders-2000", file_sha256="0" * 64),
    ):
        pass


def test_state_applies_partial_updates_onto_the_base_row(ws: Workspace) -> None:
    """Seahaven's change log carries only the changed columns of an update; state must merge them."""
    with checked_run(ws, Faults(), Intent((REFUND,))) as run:
        run.session.call("refund_charge", charge_id=TARGET, amount=5000)
        update = next(c for c in run.branch.changes() if c.table == "charges")
        assert set(update.after) == {"amount_refunded"}  # what makes the merge necessary
        twin_row = run.branch.row("charges", {"id": TARGET})
    with BaseState.open(spike.twin(ws), "orders-2000") as base:
        engine = Engine(INVARIANTS, base)
        for record in run.log.records():
            engine.feed(record)
        assert State(base, engine.changes).row("charges", {"id": TARGET}) == twin_row


def test_replay_reports_an_invariant_that_no_longer_exists(ws: Workspace) -> None:
    result = replay(ws, spike.twin(ws), _failed_run(ws), (INVARIANTS[0],))
    assert result["verdict"] == "diverged"
    assert result["invariants"][1]["note"] == "invariant no_retry_after_lost_response_timeout no longer exists"


# --- rule 2: no_retry_after_lost_response_timeout ----------------------------------------------


def no_retry(run: Run) -> list[dict]:
    return [
        r for r in of_type(run, "invariant.failed") if r["data"]["invariant"] == "no_retry_after_lost_response_timeout"
    ]


def test_a_blind_retry_after_a_lost_response_is_a_violation_seen_in_the_fault_record(ws: Workspace) -> None:
    with checked_run(ws, Faults(seed=42, at_request={1: "lost_response"}), Intent((REFUND,))) as run:
        retrying_refund(run.session)
    (failed,) = no_retry(run)
    f = failed["data"]
    request_2 = next(r for r in of_type(run, "call") if r["data"]["request"] == 2)
    assert (f["subject"], f["step"], f["seed"]) == ("request 1", request_2["seq"], 42)
    assert f["evidence"]["faults"] == ["request/1"] and f["evidence"]["requests"] == [1, 2]
    assert f["message"] == (
        f"request 2 retried refund_charge on {TARGET} after request 1 timed out having reached the twin, "
        f"without reading {TARGET} first"
    )


def test_it_needs_the_fault_record_not_just_state(ws: Workspace) -> None:
    """Two $25 refunds asked for and made, no fault: same rows as a blind retry would leave,
    but nothing timed out, so it is not a retry. Only the fault log can tell these apart."""
    asked = Intent(({**REFUND, "amount": 2500}, {**REFUND, "amount": 2500}))
    with checked_run(ws, Faults(), asked) as run:
        run.session.call("refund_charge", charge_id=TARGET, amount=2500)
        run.session.call("refund_charge", charge_id=TARGET, amount=2500)
    assert of_type(run, "invariant.failed") == []


def test_a_not_sent_timeout_is_not_a_lost_response(ws: Workspace) -> None:
    with checked_run(ws, Faults(at_request={1: "not_sent"}), Intent((REFUND,))) as run:
        retrying_refund(run.session)
    assert of_type(run, "invariant.failed") == []  # the first never reached the twin; one refund


def test_reading_the_charge_first_makes_a_retry_informed(ws: Workspace) -> None:
    """Even a retry that refunds again is not *this* violation after a read; refunded_le_requested
    still judges the money."""

    def reads_then_retries(session: Session) -> None:
        try:
            session.call("refund_charge", charge_id=TARGET, amount=5000)
        except AgentToolError:
            session.call("get_charge", charge_id=TARGET)
            session.call("refund_charge", charge_id=TARGET, amount=5000)

    with checked_run(ws, Faults(at_request={1: "lost_response"}), Intent((REFUND,))) as run:
        reads_then_retries(run.session)
    assert no_retry(run) == []
    assert [r["data"]["invariant"] for r in of_type(run, "invariant.failed")] == ["refunded_le_requested"]


def test_a_failed_read_does_not_count_as_checking(ws: Workspace) -> None:
    def reads_badly_then_retries(session: Session) -> None:
        try:
            session.call("refund_charge", charge_id=TARGET, amount=5000)
        except AgentToolError:
            with pytest.raises(AgentToolError):
                session.call("get_charge", charge_id=TARGET)  # rate-limited: it learned nothing
            session.call("refund_charge", charge_id=TARGET, amount=5000)

    with checked_run(ws, Faults(at_request={1: "lost_response", 2: "rate_limited"}), Intent((REFUND,))) as run:
        reads_badly_then_retries(run.session)
    assert len(no_retry(run)) == 1


def test_reading_the_customers_charges_also_counts(ws: Workspace) -> None:
    def lists_then_retries(session: Session) -> None:
        try:
            session.call("refund_charge", charge_id=TARGET, amount=5000)
        except AgentToolError:
            charge = session.call("get_charge", charge_id="charge-0001")  # a different charge: not a check
            session.call("list_charges", customer_id="cust-1234")  # TARGET's customer
            session.call("refund_charge", charge_id=TARGET, amount=5000)
            assert charge

    with checked_run(ws, Faults(at_request={1: "lost_response"}), Intent((REFUND,))) as run:
        lists_then_retries(run.session)
    assert no_retry(run) == []


def test_the_cli_replays_a_failed_run(ws: Workspace) -> None:
    run_id = _failed_run(ws)
    proc = subprocess.run(
        [sys.executable, "-m", "agent_staging.cli", "replay", run_id],
        capture_output=True,
        text=True,
        env={**os.environ, "STAGING_HOME": str(ws.root)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "FAILED refunded_le_requested v1 at step" in proc.stdout and proc.stdout.strip().endswith("REPRODUCED")
