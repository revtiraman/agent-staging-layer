# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""The hard-invariant engine (docs/invariant-engine.md).

An invariant is a pure function of four inputs, all rebuilt from the run log:
  state    rows as of this step: the hash-pinned base state plus every logged change
  changes  the logged row changes, each with its call `i`, request `n` and twin time
  faults   the harness timeline: fault decisions, requests, webhook deliveries
  intent   what the run was asked to do

The engine is fed run-log records one at a time, in their canonical JSON form, and
evaluates every invariant after each step. A live run feeds it records as they are
appended; replay feeds it the same records read back from the file. One code path, so
live and replay results cannot differ because they were computed differently.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_staging.kernel import StateMeta, TwinKernel

INTENT_FORMAT = "agent-staging.intent/1"
STEP_TYPES = frozenset({"call", "event.delivered", "steps.end"})


# --- inputs ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Intent:
    """What the run was asked to do. `requests` today; `allowed_actions`, `forbidden_actions`
    and `consents` are planned beside it (their schema is deliberately not designed yet)."""

    requests: tuple[Mapping[str, Any], ...] = ()

    def record(self) -> dict[str, Any]:
        return {"format": INTENT_FORMAT, "requests": [dict(r) for r in self.requests]}

    @classmethod
    def from_record(cls, data: Mapping[str, Any]) -> Intent:
        if data.get("format") != INTENT_FORMAT:
            raise ValueError(f"unknown intent format {data.get('format')!r}")
        return cls(requests=tuple(data["requests"]))


class BaseRows(Protocol):
    meta: StateMeta

    def rows(self, table: str) -> list[dict[str, Any]]: ...


class BaseState:
    """The saved state a run started from, read-only. Open with `BaseState.open(twin, id)`."""

    def __init__(self, meta: StateMeta, branch: Any) -> None:
        self.meta = meta
        self._branch = branch
        self._cache: dict[str, list[dict[str, Any]]] = {}

    @classmethod
    @contextmanager
    def open(cls, twin: TwinKernel, state_id: str, *, file_sha256: str | None = None) -> Iterator[BaseState]:
        meta = twin.state(state_id)
        if file_sha256 is not None and meta.file_sha256 != file_sha256:
            raise ValueError(f"state {state_id} is not the file this run started from (sha256 differs)")
        with twin.open(state_id) as branch:  # never called: only read
            yield cls(meta, branch)

    def rows(self, table: str) -> list[dict[str, Any]]:
        if table not in self._cache:
            self._cache[table] = self._branch.rows(table)
        return [dict(r) for r in self._cache[table]]


def _key(key: Mapping[str, Any], cols: Sequence[str]) -> tuple[Any, ...]:
    return tuple(key[c] for c in cols)


class State:
    """Rows as of a step: base rows with the logged changes applied in order."""

    def __init__(self, base: BaseRows, changes: Sequence[Mapping[str, Any]]) -> None:
        self._base = base
        self._changes = changes

    def rows(self, table: str) -> list[dict[str, Any]]:
        mine = [c for c in self._changes if c["table"] == table]
        if not mine:
            return self._base.rows(table)
        cols = list(mine[0]["key"])
        by_key = {_key(r, cols): r for r in self._base.rows(table)}
        for c in mine:
            k = _key(c["key"], cols)
            if c["op"] == "delete":
                by_key.pop(k, None)
            elif c["op"] == "update":  # Seahaven logs only the changed columns of an update
                by_key[k] = {**by_key.get(k, {}), **c["after"]}
            else:
                by_key[k] = dict(c["after"])
        return list(by_key.values())

    def row(self, table: str, key: Mapping[str, Any]) -> dict[str, Any] | None:
        for r in self.rows(table):
            if all(r.get(k) == v for k, v in key.items()):
                return r
        return None

    def base_row(self, table: str, key: Mapping[str, Any]) -> dict[str, Any] | None:
        """A row as it was in the base state, before the run changed anything."""
        for r in self._base.rows(table):
            if all(r.get(k) == v for k, v in key.items()):
                return r
        return None


@dataclass
class Timeline:
    """The harness side of the run, in log order. Each item carries its log `seq`."""

    seed: int | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    faults: list[dict[str, Any]] = field(default_factory=list)
    deliveries: list[dict[str, Any]] = field(default_factory=list)

    def faults_on_request(self, n: int) -> list[dict[str, Any]]:
        return [f for f in self.faults if f.get("request") == n]


# --- invariants ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    subject: str  # what is wrong, e.g. "charge charge-0001"; reported once per (invariant, subject)
    message: str
    rows: tuple[Mapping[str, Any], ...] = ()  # {"table", "key", "values"}
    faults: tuple[str, ...] = ()  # fault keys, e.g. "request/1"
    requests: tuple[int, ...] = ()


class Invariant(Protocol):
    name: str
    version: int

    def check(
        self, state: State, changes: Sequence[Mapping[str, Any]], faults: Timeline, intent: Intent
    ) -> list[Violation]: ...


def row_evidence(table: str, row: Mapping[str, Any], key_cols: Sequence[str] = ("id",)) -> dict[str, Any]:
    return {"table": table, "key": {c: row[c] for c in key_cols}, "values": dict(row)}


# --- the engine ------------------------------------------------------------------------------


class Engine:
    def __init__(self, invariants: Sequence[Invariant], base: BaseRows) -> None:
        names = [i.name for i in invariants]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate invariant names: {names}")
        self.invariants = list(invariants)
        self.base = base
        self.changes: list[dict[str, Any]] = []
        self.timeline = Timeline()
        self.intent = Intent()
        self._reported: set[tuple[str, str]] = set()

    def active(self) -> dict[str, Any]:
        return {"invariants": [{"name": i.name, "version": i.version} for i in self.invariants]}

    def feed(self, record: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Take one log record. After a step, return the new failures as `invariant.failed` data."""
        type_, data, seq = record["type"], record["data"], record["seq"]
        if type_ == "change":
            self.changes.append({**data, "seq": seq})
        elif type_ == "call":
            self.timeline.calls.append({**data, "seq": seq})
        elif type_ == "fault":
            self.timeline.faults.append({**data, "seq": seq})
        elif type_ == "event.delivered":
            self.timeline.deliveries.append({**data, "seq": seq})
        elif type_ == "faults.profile":
            self.timeline.seed = data["seed"]
        elif type_ == "intent":
            self.intent = Intent.from_record(data)
        if type_ not in STEP_TYPES:
            return []
        state = State(self.base, self.changes)
        failures = []
        for inv in self.invariants:
            found = inv.check(state, tuple(self.changes), self.timeline, self.intent)
            if not all(isinstance(v, Violation) for v in found):  # a guard on the input, not a dispatch
                raise TypeError(f"invariant {inv.name} returned something other than Violation")
            for v in sorted(found, key=lambda v: v.subject):
                if (inv.name, v.subject) in self._reported:
                    continue
                self._reported.add((inv.name, v.subject))
                failures.append(
                    {
                        "invariant": inv.name,
                        "version": inv.version,
                        "subject": v.subject,
                        "step": seq,
                        "message": v.message,
                        "evidence": {
                            "rows": sorted(
                                (dict(r) for r in v.rows), key=lambda r: (r["table"], sorted(r["key"].items()))
                            ),
                            "faults": sorted(v.faults),
                            "requests": sorted(v.requests),
                        },
                        "seed": self.timeline.seed,
                    }
                )
        return failures
