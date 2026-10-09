# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Milestone 5: the soft-invariant layer. One advisor, end to end, and the two guarantees
checked in code: soft results never change a run's outcome, and nothing that gates imports
the soft module."""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from agent_staging import spike
from agent_staging.advisory import (
    AdvisoryError,
    AdvisoryNote,
    Judgement,
    KeywordJudge,
    RefundReasonFitsRequest,
    review,
)
from agent_staging.faults import Faults
from agent_staging.harness import Run
from agent_staging.invariants import BaseState, Engine, Intent
from agent_staging.reexec import execute
from agent_staging.replay import replay
from agent_staging.runlog import RunLog
from agent_staging.session import AgentToolError
from agent_staging.twins.refund_path_invariants import INVARIANTS
from agent_staging.workspace import Workspace

SRC = Path(__file__).parents[1] / "src" / "agent_staging"
names = iter(range(10_000))


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = Workspace(tmp_path / ".staging")
    spike.seed(w)
    return w


class CountingJudge(KeywordJudge):
    def __init__(self) -> None:
        self.calls = 0

    def judge(self, question: str, context: Mapping[str, Any]) -> Judgement:
        self.calls += 1
        return super().judge(question, context)


@contextmanager
def finished_run(ws: Workspace, intent: Intent, faults: Faults | None = None) -> Iterator[tuple[Run, BaseState]]:
    """A checked run, finished (run.end written), with its base state still open for advisors."""
    twin = spike.twin(ws)
    run_id = f"run-a-{next(names)}"
    log = RunLog.create(ws.run_log_path(run_id), {"kind": "scenario"})
    with BaseState.open(twin, "prod-0001") as base, twin.open("prod-0001") as branch:
        run = Run.start(branch, log, faults or Faults(), intent=intent, engine=Engine(INVARIANTS, base))
        run.run_id = run_id  # type: ignore[attr-defined]
        yield run, base


def ask(charge: str, amount: int, note: str) -> dict[str, Any]:
    return {"action": "refund", "charge_id": charge, "amount": amount, "note": note}


def types(log: RunLog) -> list[str]:
    return [r["type"] for r in log.records()]


# --- end to end ------------------------------------------------------------------------------


def test_a_soft_failure_is_recorded_and_cannot_fail_the_run(ws: Workspace) -> None:
    intent = Intent((ask("charge-0001", 5000, "I was charged twice for the same order"),))
    with finished_run(ws, intent) as (run, base):
        run.session.call("refund_charge", charge_id="charge-0001", amount=5000, reason="requested_by_customer")
        outcome = run.finish()
        written = review(run.log, base, [RefundReasonFitsRequest()], KeywordJudge())

    assert outcome == {"outcome": "passed", "violations": []}
    (note,) = [w["data"] for w in written]
    assert (note["advisor"], note["verdict"], note["subject"]) == (
        "refund_reason_fits_request",
        "does_not_fit",
        "refund refund-0002",
    )
    assert note["judge"]["context"] == {
        "customer_said": "I was charged twice for the same order",
        "refund_reason": "requested_by_customer",
    }
    assert note["judge"]["rationale"] == "the customer's words match 'duplicate', not 'requested_by_customer'"
    log_types = types(run.log)
    assert log_types.index("run.end") < log_types.index("advisory.note")  # the verdict came first
    (end,) = [r for r in run.log.records() if r["type"] == "run.end"]
    assert end["data"]["outcome"] == "passed"  # still: nothing rewrote it
    assert run.log.verify() == []


def test_a_fitting_reason_is_recorded_as_fits(ws: Workspace) -> None:
    intent = Intent((ask("charge-0001", 5000, "the item never arrived"),))
    with finished_run(ws, intent) as (run, base):
        run.session.call("refund_charge", charge_id="charge-0001", amount=5000)
        run.finish()
        (note,) = review(run.log, base, [RefundReasonFitsRequest()], KeywordJudge())
    assert note["data"]["verdict"] == "fits"


def test_a_hard_failure_is_not_softened_by_a_fitting_note(ws: Workspace) -> None:
    intent = Intent((ask("charge-0001", 5000, "the item never arrived"),))
    with finished_run(ws, intent, Faults(at_request={1: "lost_response"})) as (run, base):
        for _ in range(2):
            try:
                run.session.call("refund_charge", charge_id="charge-0001", amount=5000)
                break
            except AgentToolError as e:
                assert e.code == "timeout"
        outcome = run.finish()
        notes = review(run.log, base, [RefundReasonFitsRequest()], KeywordJudge())
    assert outcome["outcome"] == "failed"
    assert {n["data"]["verdict"] for n in notes} == {"fits"}
    assert next(r for r in run.log.records() if r["type"] == "run.end")["data"]["outcome"] == "failed"


def test_advisors_refuse_to_run_before_the_outcome_is_written(ws: Workspace) -> None:
    with finished_run(ws, Intent()) as (run, base):
        with pytest.raises(AdvisoryError, match="only after run.end"):
            review(run.log, base, [RefundReasonFitsRequest()], KeywordJudge())
        run.finish()


def test_no_judge_means_skipped_not_passed(ws: Workspace) -> None:
    with finished_run(ws, Intent((ask("charge-0001", 5000, "never arrived"),))) as (run, base):
        run.session.call("refund_charge", charge_id="charge-0001", amount=5000)
        run.finish()
        (skipped,) = review(run.log, base, [RefundReasonFitsRequest()], None)
    assert skipped["type"] == "advisory.skipped" and skipped["data"]["reason"] == "no judge configured"


# --- replay reads recorded judgements, never asks again --------------------------------------


def test_replay_reports_recorded_judgements_without_calling_the_judge(ws: Workspace) -> None:
    judge = CountingJudge()
    with finished_run(ws, Intent((ask("charge-0001", 5000, "charged twice"),))) as (run, base):
        run.session.call("refund_charge", charge_id="charge-0001", amount=5000)
        run.finish()
        recorded = [w["data"] for w in review(run.log, base, [RefundReasonFitsRequest()], judge)]
    assert judge.calls == 1

    result = replay(ws, spike.twin(ws), run.run_id, INVARIANTS)  # type: ignore[attr-defined]
    executed = execute(ws, spike.twin(ws), run.run_id, INVARIANTS)  # type: ignore[attr-defined]

    assert judge.calls == 1  # neither replay asked the judge
    assert result["verdict"] == executed["verdict"] == "reproduced"
    assert result["advisory"] == executed["advisory"] == recorded


# --- the two guarantees, structurally ---------------------------------------------------------


def _module_file(module: str) -> Path | None:
    rel = module.removeprefix("agent_staging").lstrip(".").replace(".", "/")
    for candidate in (SRC / f"{rel}.py", SRC / rel / "__init__.py"):
        if rel and candidate.exists():
            return candidate
    return None


def _imports(module: str) -> set[str]:
    """Every agent_staging module `module` imports, directly (from-imports of submodules included)."""
    path = _module_file(module)
    assert path is not None, module
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found |= {f"{node.module}.{a.name}" for a in node.names}
    return {m for m in found if m.startswith("agent_staging") and _module_file(m) is not None}


def _closure(module: str) -> set[str]:
    seen, todo = set(), [module]
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        todo += _imports(m)
    return seen


@pytest.mark.parametrize(
    "module",
    [
        "agent_staging.apply",
        "agent_staging.approval",
        "agent_staging.dryrun",
        "agent_staging.harness",
        "agent_staging.invariants",
        "agent_staging.replay",
        "agent_staging.reexec",
        "agent_staging.twins.refund_path_invariants",
    ],
)
def test_nothing_that_gates_imports_the_soft_module(module: str) -> None:
    closure = _closure(module)
    assert "agent_staging.advisory" not in closure


def test_the_import_scan_follows_real_imports() -> None:
    """If the scan saw nothing, the test above would pass for the wrong reason."""
    assert {
        "agent_staging.session",
        "agent_staging.faults",
        "agent_staging.invariants",
        "agent_staging.kernel",
    } <= _closure("agent_staging.harness")
    assert "agent_staging.harness" in _closure("agent_staging.apply")  # apply -> approval -> dryrun -> harness
    assert "agent_staging.invariants" in _closure("agent_staging.advisory")


def test_an_advisory_note_cannot_pose_as_a_hard_violation(ws: Workspace) -> None:
    class Impostor:
        name, version = "impostor", 1

        def check(self, state, changes, faults, intent):  # type: ignore[no-untyped-def]
            j = Judgement("does_not_fit", "x", "y")
            return [AdvisoryNote("s", "does_not_fit", "m", "q", {}, j)]

    with finished_run(ws, Intent()) as (run, base):
        with pytest.raises(TypeError, match="other than Violation"):
            Engine([Impostor()], base).feed({"type": "steps.end", "data": {}, "seq": 1})
        run.finish()
