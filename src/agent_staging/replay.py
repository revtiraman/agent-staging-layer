# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""`staging replay <run>`: re-evaluate a run's invariants from its log alone.

It verifies the hash chain, rebuilds the four inputs from the log and the hash-pinned base
state, feeds the same records to the same engine, and compares the failures with the
recorded `invariant.failed` records. The agent is not re-run and no fault is re-drawn
(re-execution from recorded decisions is milestone 5).

Verdicts (docs/invariant-engine.md, "Replay"):
  same outcome, same code         reproduced
  same outcome, different code    reproduced, version mismatch noted
  different outcome, either way   diverged, with the invariant, both versions and the first step
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from agent_staging.invariants import BaseState, Engine, Invariant
from agent_staging.kernel import TwinKernel
from agent_staging.runlog import RunLog, canonical
from agent_staging.workspace import Workspace

REPLAY_FORMAT = "agent-staging.replay/1"
SKIP = frozenset({"invariant.failed", "run.end"})  # outputs of the engine, not its inputs


class ReplayError(Exception):
    pass


def _one(records: Sequence[Mapping[str, Any]], type_: str) -> Mapping[str, Any]:
    found = [r["data"] for r in records if r["type"] == type_]
    if len(found) != 1:
        raise ReplayError(f"expected one {type_} record, found {len(found)}: this run was not checked")
    return found[0]


def _outcome(failures: Sequence[Mapping[str, Any]], name: str) -> list[tuple[int, str, str]]:
    """What counts as the outcome: where, about what, and on what evidence. Not the wording."""
    return sorted((f["step"], f["subject"], canonical(f["evidence"])) for f in failures if f["invariant"] == name)


def replay(ws: Workspace, twin: TwinKernel, run_id: str, invariants: Sequence[Invariant]) -> dict[str, Any]:
    log = RunLog(ws.run_log_path(run_id))
    problems = log.verify()
    if problems:
        raise ReplayError("the run log is not intact, so it can't be replayed: " + "; ".join(problems))
    records = log.records()
    base = _one(records, "base.state")
    active = {a["name"]: a["version"] for a in _one(records, "invariants.active")["invariants"]}
    recorded = [r["data"] for r in records if r["type"] == "invariant.failed"]

    current = {i.name: i for i in invariants}
    use = [current[name] for name in active if name in current]
    with BaseState.open(twin, base["state"], file_sha256=base["file_sha256"]) as state:
        engine = Engine(use, state)
        got: list[dict[str, Any]] = []
        for record in records:
            if record["type"] not in SKIP:
                got += engine.feed(record)

    results, diverged = [], False
    for name, was in active.items():
        if name not in current:
            diverged = True
            results.append({"invariant": name, "recorded_version": was, "current_version": None,
                            "outcome": "missing", "note": f"invariant {name} no longer exists"})  # fmt: skip
            continue
        now = current[name].version
        before, after = _outcome(recorded, name), _outcome(got, name)
        entry: dict[str, Any] = {"invariant": name, "recorded_version": was, "current_version": now}
        if before == after:
            entry["outcome"] = "same"
            if was != now:
                entry["note"] = f"version mismatch: recorded with version {was}, replayed with version {now}"
        else:
            diverged = True
            step = min(set(before) ^ set(after))[0]
            entry |= {"outcome": "different", "first_different_step": step}
            entry["note"] = (
                f"invariant {name} version {was} vs version {now} produced different results at step {step}"
                if was != now
                else f"invariant {name} version {was} produced different results at step {step} on replay"
            )
        results.append(entry)

    record = {
        "format": REPLAY_FORMAT,
        "run_id": run_id,
        "log_head": records[-1]["hash"],
        "at": datetime.now(UTC).isoformat(timespec="milliseconds"),  # when the replay ran (wall clock)
        "verdict": "diverged" if diverged else "reproduced",
        "invariants": results,
        "not_evaluated": sorted(set(current) - set(active)),  # exist now, weren't active in the run
        "failures": got,
        # recorded soft-invariant output, reported as is: replay never calls a judge
        "advisory": [r["data"] for r in records if r["type"] == "advisory.note"],
    }
    out = ws.run_log_path(run_id).parent / "replays"
    out.mkdir(parents=True, exist_ok=True)
    ws.write_json(out / f"replay-{len(list(out.glob('replay-*.json'))) + 1:03d}.json", record)
    return record
