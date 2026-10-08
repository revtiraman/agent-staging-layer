# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Milestone 5: re-execution replay. The environment is rebuilt from the log, every fault decision
is read back from it, and the agent's recorded requests are re-fed in place. The new log must match
the original record by record (wall-clock times and hashes aside)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_staging import dryrun, spike
from agent_staging.faults import Faults
from agent_staging.harness import Run
from agent_staging.invariants import BaseState, Engine, Intent
from agent_staging.kernel import Event, TwinToolError
from agent_staging.reexec import Divergence, execute
from agent_staging.runlog import RunLog
from agent_staging.session import AgentToolError, Session
from agent_staging.twins.refund_path_invariants import INVARIANTS, RefundedLeRequested
from agent_staging.twins.seahaven_twin import SeahavenBranch
from agent_staging.workspace import Workspace

REFUND = {"action": "refund", "charge_id": "charge-0001", "amount": 5000}
names = iter(range(10_000))


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = Workspace(tmp_path / ".staging")
    spike.seed(w)
    return w


@contextmanager
def checked_run(ws: Workspace, faults: Faults, intent: Intent, invariants=INVARIANTS) -> Iterator[Run]:
    twin = spike.twin(ws)
    run_id = f"run-x-{next(names)}"
    log = RunLog.create(ws.run_log_path(run_id), {"kind": "scenario", "run_id": run_id})
    with BaseState.open(twin, "prod-0001") as base, twin.open("prod-0001") as branch:
        run = Run.start(branch, log, faults, intent=intent, engine=Engine(invariants, base))
        run.run_id = run_id  # type: ignore[attr-defined]
        yield run
        run.finish()


def retrying_refund(session: Session) -> None:
    for _ in range(2):
        try:
            session.call("refund_charge", charge_id="charge-0001", amount=5000)
            return
        except AgentToolError as e:
            if e.code != "timeout":
                raise


def demo_run(ws: Workspace) -> str:
    with checked_run(ws, Faults(seed=42, at_request={1: "lost_response"}), Intent((REFUND,))) as run:
        retrying_refund(run.session)
    return run.run_id  # type: ignore[attr-defined]


def test_the_demo_failure_re_executes_identically(ws: Workspace) -> None:
    run_id = demo_run(ws)
    result = execute(ws, spike.twin(ws), run_id, INVARIANTS)
    assert result["verdict"] == "reproduced", result["detail"]
    assert result["compared"] > 10
    new = RunLog(Path(result["replay_log"])).records()
    assert [r["data"]["invariant"] for r in new if r["type"] == "invariant.failed"] == [
        "refunded_le_requested",
        "no_retry_after_lost_response_timeout",
    ]
    assert next(r for r in new if r["type"] == "run.end")["data"]["outcome"] == "failed"
    assert RunLog(Path(result["replay_log"])).verify() == []


def test_a_run_with_every_fault_kind_and_handler_calls_re_executes_identically(ws: Workspace) -> None:
    """Drawn delays, duplicates, same-instant reorders, 429s and both timeouts, and a webhook handler
    that itself makes requests (which must be re-issued inside the same delivery)."""
    faults = Faults(
        seed=13,
        webhook_delay=(0, 3),
        duplicate_rate=0.5,
        duplicate_gap=(0, 1),
        reorder_rate=0.7,
        rate_limit_rate=0.2,
        timeout_rate=0.2,
    )

    def on_event(session: Session, event: Event) -> None:
        try:
            session.call("get_charge", charge_id=event.payload["charge_id"])
        except AgentToolError:
            pass

    with checked_run(ws, faults, Intent((REFUND,))) as run:
        run.session.on_event(on_event)
        for k in range(8):
            try:
                run.session.call("refund_charge", charge_id=f"charge-000{1 + k % 2}", amount=100)
            except AgentToolError:
                pass
            if k % 3 == 0:
                run.advance(1.5)
        run.advance(10)
    kinds = {r["data"]["kind"] for r in run.log.records() if r["type"] == "fault"}
    assert {"webhook.schedule", "webhook.duplicate"} <= kinds and kinds & {"request.rate_limited", "request.lost_response"}
    handler_calls = [r for r in run.log.records() if r["type"] == "call" and r["data"]["tool"] == "get_charge"]
    assert handler_calls  # the handler did make requests

    result = execute(ws, spike.twin(ws), run.run_id, INVARIANTS)  # type: ignore[attr-defined]
    assert result["verdict"] == "reproduced", result["detail"]


def test_a_twin_that_behaves_differently_is_caught_at_the_record(ws: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = demo_run(ws)
    original = SeahavenBranch.call

    def stingy(self, tool, /, **arguments):  # type: ignore[no-untyped-def]
        if tool == "refund_charge" and len(self.calls()) >= 1:
            raise TwinToolError("rate_limited_by_bank", "the bank refused a second refund")
        return original(self, tool, **arguments)

    monkeypatch.setattr(SeahavenBranch, "call", stingy)
    result = execute(ws, spike.twin(ws), run_id, INVARIANTS)
    assert result["verdict"] == "diverged"
    request_2 = next(
        r for r in RunLog(ws.run_log_path(run_id)).records() if r["type"] == "call" and r["data"]["request"] == 2
    )
    # the original logged request 2's change records first; the re-execution has none, so the
    # first difference is where request 2's refund row should have been
    assert result["detail"].startswith("diverged at seq ")
    assert int(result["detail"].split()[3].rstrip(":")) <= request_2["seq"]


class V2(RefundedLeRequested):
    version = 2


class Doubled(RefundedLeRequested):
    version = 3

    def check(self, state, changes, faults, intent):  # type: ignore[no-untyped-def]
        doubled = Intent(tuple({**r, "amount": r["amount"] * 2} for r in intent.requests))
        return super().check(state, changes, faults, doubled)


def test_same_outcome_new_invariant_version_reproduces_with_a_note(ws: Workspace) -> None:
    result = execute(ws, spike.twin(ws), demo_run(ws), (V2(), INVARIANTS[1]))
    assert result["verdict"] == "reproduced"
    assert result["notes"] == [
        "version mismatch: refunded_le_requested recorded with version 1, re-executed with version 2"
    ]


def test_different_outcome_new_invariant_version_diverges_with_both_versions(ws: Workspace) -> None:
    result = execute(ws, spike.twin(ws), demo_run(ws), (Doubled(), INVARIANTS[1]))
    assert result["verdict"] == "diverged"
    assert result["detail"].startswith("invariant refunded_le_requested version 1 vs version 3 produced different")


def test_a_dry_run_log_re_executes_up_to_its_end(ws: Workspace) -> None:
    plan, run_id = dryrun.dry_run(ws, spike.twin(ws), spike.support_agent, agent_name="t")
    result = execute(ws, spike.twin(ws), run_id, INVARIANTS)
    assert result["verdict"] == "reproduced", result["detail"]
    assert plan.body["summary"]["to_apply"] == 2


def test_a_tampered_log_is_refused(ws: Workspace) -> None:
    run_id = demo_run(ws)
    path = ws.run_log_path(run_id)
    path.write_text(path.read_text().replace('"amount":5000', '"amount":9000', 1))
    with pytest.raises(Divergence, match="not intact"):
        execute(ws, spike.twin(ws), run_id, INVARIANTS)
