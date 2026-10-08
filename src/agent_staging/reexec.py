# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""`staging replay --execute <run>`: re-execute a run from its log (docs/invariant-engine.md,
"Re-execution replay").

The environment is deterministic from the log; the model is not, so this re-executes the
harness and re-feeds the recorded agent decisions. It opens the same base state with the same
start time and twin seed, answers every fault decision from the log's `fault` records (never
the seed), re-issues the agent's recorded requests where they were made (top level, or inside
the delivery whose handler made them) between the recorded clock advances, checks the hard
invariants live, and compares the new log with the original record by record.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent_staging.faults import Delivery, RequestFault
from agent_staging.harness import Run
from agent_staging.invariants import BaseState, Engine, Intent, Invariant
from agent_staging.kernel import TwinKernel
from agent_staging.runlog import RunLog, canonical
from agent_staging.session import AgentToolError
from agent_staging.workspace import Workspace

EXECUTE_FORMAT = "agent-staging.replay-execute/1"
_ADDED_BY_HARNESS = ("event_id", "type")  # Run adds these to schedule records when it logs them


class Divergence(Exception):
    """The re-execution asked for something the original run never did."""


class RecordedFaults:
    """A fault source that only reads the original run's decisions, by key. Nothing is drawn."""

    def __init__(self, records: Sequence[Mapping[str, Any]]) -> None:
        self._profile = next(r["data"] for r in records if r["type"] == "faults.profile")
        self._by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in records:
            if r["type"] == "fault":
                self._by_key[r["data"]["key"]].append(dict(r["data"]))

    def profile(self) -> dict[str, Any]:
        return dict(self._profile)

    def request(self, n: int, tool: str) -> tuple[RequestFault | None, dict[str, Any] | None]:
        found = [d for d in self._by_key.get(f"request/{n}", []) if d["kind"].startswith("request.")]
        if not found:
            return None, None
        (record,) = found
        if record["tool"] != tool:
            raise Divergence(f"request {n} is {tool}, but the original request {n} was {record['tool']}")
        kind = record["kind"].removeprefix("request.")
        return RequestFault(kind, record.get("retry_after")), record  # type: ignore[arg-type]

    def schedule(self, event_seq: int, emitted_at: datetime) -> tuple[list[Delivery], list[dict[str, Any]]]:
        found = [d for d in self._by_key.get(f"webhook/{event_seq}", [])]
        if not found:
            raise Divergence(f"event {event_seq} was emitted, but the original run never scheduled it")
        records = [{k: v for k, v in d.items() if k not in _ADDED_BY_HARNESS} for d in found]
        deliveries = [Delivery(event_seq, d["copy"], datetime.fromisoformat(d["due"])) for d in records]
        return deliveries, records

    def order(self, batch: Sequence[Delivery], batch_no: int) -> tuple[list[Delivery], dict[str, Any] | None]:
        batch = sorted(batch, key=lambda d: (d.event_seq, d.copy))
        found = self._by_key.get(f"batch/{batch_no}", [])
        if len(batch) < 2:
            if found:
                raise Divergence(f"batch {batch_no} has one delivery, but the original batch had {len(found)} records")
            return batch, None
        if not found:
            raise Divergence(f"batch {batch_no} has {len(batch)} deliveries, but the original run had no such batch")
        (record,) = found
        by_id = {d.id: d for d in batch}
        if sorted(by_id) != sorted(record["delivery_order"]):
            raise Divergence(f"batch {batch_no} holds {sorted(by_id)}, the original held {record['delivery_order']}")
        return [by_id[i] for i in record["delivery_order"]], dict(record)


@dataclass(frozen=True)
class Script:
    """The agent's recorded decisions, in the places they were made."""

    top: list[tuple[str, Mapping[str, Any]]]  # ("call", data) or ("advance", data), in order
    in_delivery: dict[str, list[Mapping[str, Any]]]  # delivery id -> handler calls, in order


def script(records: Sequence[Mapping[str, Any]]) -> Script:
    top: list[tuple[str, Mapping[str, Any]]] = []
    in_delivery: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    delivery: str | None = None  # deliveries only happen inside an advance, which closes with clock.advance
    for r in records:
        if r["type"] == "run.end":
            break
        if r["type"] == "event.delivered":
            delivery = r["data"]["delivery"]
        elif r["type"] == "clock.advance":
            top.append(("advance", r["data"]))
            delivery = None
        elif r["type"] == "call":
            if delivery is None:
                top.append(("call", r["data"]))
            else:
                in_delivery[delivery].append(r["data"])
    return Script(top, dict(in_delivery))


def _issue(run: Run, call: Mapping[str, Any]) -> None:
    try:
        run.session.call(call["tool"], **call["arguments"])
    except AgentToolError:
        pass  # the original agent got this error too; the comparison checks it was the same one


def _body(record: Mapping[str, Any]) -> tuple[str, str]:
    data = dict(record["data"])
    if record["type"] == "invariant.failed":
        data.pop("version")  # compared separately: version mismatch rules
    if record["type"] == "invariants.active":
        data = {"names": [i["name"] for i in data["invariants"]]}  # versions: same rules, via the failures
    return record["type"], canonical(data)


def _versions(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    for r in records:
        if r["type"] == "invariants.active":
            return {i["name"]: i["version"] for i in r["data"]["invariants"]}
    return {}


def _first_field(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    if a["type"] != b["type"]:
        return "type"
    for key in sorted(set(a["data"]) | set(b["data"])):
        if a["data"].get(key) != b["data"].get(key):
            return f"data.{key}"
    return "data"


def execute(ws: Workspace, twin: TwinKernel, run_id: str, invariants: Sequence[Invariant]) -> dict[str, Any]:
    original = RunLog(ws.run_log_path(run_id))
    problems = original.verify()
    if problems:
        raise Divergence("the run log is not intact, so it can't be replayed: " + "; ".join(problems))
    records = original.records()
    end = next((k for k, r in enumerate(records) if r["type"] == "run.end"), None)
    if end is None:
        raise Divergence("the run never ended (no run.end record), so there is nothing to re-execute")
    origin = next(r["data"] for r in records if r["type"] == "base.state")
    intent = Intent.from_record(next(r["data"] for r in records if r["type"] == "intent"))
    active = next((r["data"]["invariants"] for r in records if r["type"] == "invariants.active"), None)
    current = {i.name: i for i in invariants}

    out = ws.run_log_path(run_id).parent / "replays"
    n = len(list(out.glob("execute-*"))) + 1 if out.exists() else 1
    log = RunLog.create(out / f"execute-{n:03d}" / "log.jsonl", {"kind": "replay-execute", "of": run_id})
    start = datetime.fromisoformat(origin["start"])
    error: str | None = None
    with ExitStack() as stack:
        branch = stack.enter_context(twin.open(origin["state"], seed=origin["seed"], start=start))
        if branch.origin.file_sha256 != origin["file_sha256"]:
            raise Divergence(f"state {origin['state']} is not the file the run started from (sha256 differs)")
        engine = None
        if active is not None:
            missing = [a["name"] for a in active if a["name"] not in current]
            if missing:
                raise Divergence(f"invariants active in the run no longer exist: {missing}")
            base = stack.enter_context(BaseState.open(twin, origin["state"], file_sha256=origin["file_sha256"]))
            engine = Engine([current[a["name"]] for a in active], base)
        run = Run.start(branch, log, RecordedFaults(records), intent=intent, engine=engine)
        steps = script(records[: end + 1])
        delivering: list[str] = []
        log.observers.append(
            lambda r: delivering.append(r["data"]["delivery"]) if r["type"] == "event.delivered" else None
        )
        run.session.on_event(lambda _s, _e: [_issue(run, c) for c in steps.in_delivery.get(delivering[-1], [])])
        try:
            for kind, data in steps.top:
                if kind == "call":
                    _issue(run, data)
                else:
                    run.advance(datetime.fromisoformat(data["to"]) - datetime.fromisoformat(data["from"]))
            run.finish()
        except Divergence as e:
            error = str(e)

    new = log.records()
    want, got = records[1 : end + 1], new[1:]  # seq 0 is each log's own header
    verdict, detail, notes = "reproduced", None, []
    for k in range(max(len(want), len(got))):
        a = want[k] if k < len(want) else None
        b = got[k] if k < len(got) else None
        if a is None or b is None or _body(a) != _body(b):
            verdict = "diverged"
            seq = (a or b)["seq"]  # type: ignore[index]
            if a is None or b is None:
                detail = f"diverged at seq {seq}: the {'original' if a is None else 're-execution'} log ends here"
            else:
                detail = f"diverged at seq {seq}: {a['type']} {_first_field(a, b)} differs"
            failed = a if a is not None and a["type"] == "invariant.failed" else b
            if failed is not None and failed["type"] == "invariant.failed":
                name = failed["data"]["invariant"]
                was, now = _versions(want).get(name), _versions(got).get(name)
                if was != now:
                    detail = f"invariant {name} version {was} vs version {now} produced different results at step {failed['data']['step']}"
            break
        if a["type"] == "invariant.failed" and a["data"]["version"] != b["data"]["version"]:
            notes.append(
                f"version mismatch: {a['data']['invariant']} recorded with version {a['data']['version']}, "
                f"re-executed with version {b['data']['version']}"
            )
    if error is not None:
        verdict, detail = "diverged", error
    result = {
        "format": EXECUTE_FORMAT,
        "run_id": run_id,
        "log_head": records[-1]["hash"],
        "replay_log": str(log.path),
        "verdict": verdict,
        "detail": detail,
        "notes": notes,
        "compared": len(want),
        # recorded advisory output, reported as is: replay never calls a judge
        "advisory": [r["data"] for r in records if r["type"] == "advisory.note"],
    }
    ws.write_json(log.path.parent / "result.json", result)
    return result
