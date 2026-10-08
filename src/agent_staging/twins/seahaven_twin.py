# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""The twin kernel interface on Seahaven 0.5.0 (pinned). Shared by refund_path and Stripe.

Time: Seahaven's clock modes are fixed, tick, running and wall, and none can be moved on
demand. Every branch here runs a `fixed` clock, and `_set_clock` moves its instant. That
is the one place this package touches a private Seahaven member, the same one
`seahaven.http` itself touches (`Clock._call_started`). It works because Seahaven
computes every reading on demand from one shared Clock object, so the tools (`ctx.clock`),
SQL (`CURRENT_TIMESTAMP`, `datetime('now')`) and `freeze()` all see the moved instant.
tests/test_kernel.py checks all three, so a Seahaven upgrade that breaks this fails loudly.

Events: a world declares an outbox table. Each row a call inserts there is one event. The
branch only records them; scheduling and delivery are the harness's (agent_staging.harness).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import seahaven

from agent_staging.kernel import (
    Assigned,
    Call,
    Change,
    Event,
    Origin,
    StateMeta,
    TwinToolError,
    as_timedelta,
)

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class KernelError(Exception):
    pass


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _ms(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        raise KernelError("times must be timezone-aware")
    utc = instant.astimezone(UTC)
    return utc.replace(microsecond=utc.microsecond // 1000 * 1000)


def _set_clock(clock: seahaven.Clock, instant: datetime) -> None:
    """Move a fixed Seahaven clock to `instant` (see the module docstring)."""
    if clock.mode != "fixed" or not hasattr(clock, "_start"):
        raise KernelError(f"cannot move this clock ({clock!r}); was Seahaven upgraded from 0.5.0?")
    clock._start = _ms(instant)


class SeahavenBranch:
    def __init__(self, twin: SeahavenTwin, inst: seahaven.Instance, origin: Origin) -> None:
        self._twin = twin
        self._inst = inst
        self.origin = origin
        self._events: list[Event] = []
        self._codes: dict[int, str] = {}  # Seahaven's call log keeps the message, not the code

    # --- calls and state ---

    def call(self, tool: str, /, **arguments: Any) -> Any:
        i = self._inst.call_count
        try:
            result = self._inst.call(tool, **arguments)
        except seahaven.ToolError as e:
            self._codes[i] = e.code
            raise TwinToolError(e.code, str(e)) from None
        self._collect(i)
        return result

    def calls(self) -> list[Call]:
        return [
            Call(
                i=i,
                tool=c.tool,
                arguments=dict(c.arguments),
                error=None if c.error is None else {"code": self._code(i, c.tool_error), "message": c.error},
            )
            for i, c in enumerate(self._inst.call_log())
        ]

    def _code(self, i: int, tool_error: bool) -> str:
        return self._codes.get(i, "unknown") if tool_error else "internal_error"

    def changes(self) -> list[Change]:
        return [
            Change(
                i=r.i,
                table=r.table,
                op=r.op,
                key=dict(r.key),
                before=None if r.before is None else dict(r.before),
                after=None if r.after is None else dict(r.after),
            )
            for r in self._inst.change_log()
        ]

    def row(self, table: str, key: Mapping[str, Any]) -> dict[str, Any] | None:
        if not _IDENT.match(table) or not key or not all(_IDENT.match(k) for k in key):
            raise KernelError(f"not a table or column name: {table} {list(key)}")
        where = " AND ".join(f'"{k}" = ?' for k in key)
        row = self._inst.db.one(f'SELECT * FROM "{table}" WHERE {where}', *key.values())
        return None if row is None else dict(row)

    def rows(self, table: str) -> list[dict[str, Any]]:
        if not _IDENT.match(table):
            raise KernelError(f"not a table name: {table}")
        return [dict(r) for r in self._inst.db.rows(f'SELECT * FROM "{table}"')]

    def freeze(self, state_id: str, description: str) -> StateMeta:
        self._inst.freeze(state_id, description)
        return self._twin.state(state_id)

    # --- time and events ---

    def now(self) -> datetime:
        return self._inst.clock.now()

    def advance(self, by: timedelta | float) -> None:
        _set_clock(self._inst.clock, self.now() + as_timedelta(by))

    def events(self) -> list[Event]:
        return list(self._events)

    def _collect(self, i: int) -> None:
        outbox = self._twin.outbox_table
        if outbox is None:
            return
        for change in self._inst.change_log():
            if change.i == i and change.table == outbox and change.op == "insert" and change.after is not None:
                self._events.append(
                    Event(
                        seq=len(self._events) + 1,
                        id=str(change.after["id"]),
                        type=str(change.after["type"]),
                        payload=json.loads(str(change.after["payload"])),
                        emitted_at=self.now(),
                        i=i,
                    )
                )


class SeahavenTwin:
    """Base for twins built as Seahaven worlds. A subclass supplies the world and its meaning."""

    name: str
    version: str
    outbox_table: str | None = None  # table whose inserted rows are events
    mutating_tools: frozenset[str] = frozenset()
    irreversible_tools: frozenset[str] = frozenset()
    assigned: ClassVar[Mapping[str, Mapping[str, Assigned]]] = {}

    def __init__(self, fixtures_dir: Path | str) -> None:
        self.world = self.make_world(Path(fixtures_dir))

    def make_world(self, fixtures_dir: Path) -> seahaven.World:
        raise NotImplementedError

    def state(self, state_id: str) -> StateMeta:
        for fixture in self.world.fixtures():
            if fixture.meta.id == state_id:
                m = fixture.meta
                return StateMeta(
                    id=m.id,
                    world=m.world,
                    world_version=m.world_version,
                    schema_hash=m.schema_hash,
                    file_sha256=m.file_sha256,
                    now=_parse(m.now),
                )
        raise KernelError(f"no saved state {state_id}")

    @contextmanager
    def open(
        self,
        state_id: str | None,
        *,
        start: datetime | None = None,
        seed: int | None = None,
    ) -> Iterator[SeahavenBranch]:
        if state_id is None and start is None:
            raise KernelError("an empty twin needs a start time (the kernel never reads the wall clock)")
        now = None if start is None else _ms(start)
        with self.world.instance(state_id, now=now, seed=seed, clock_mode="fixed") as inst:
            sha = None if state_id is None else self.state(state_id).file_sha256
            yield SeahavenBranch(self, inst, Origin(self.name, state_id, sha, seed, inst.clock.now()))

    def is_mutating(self, tool: str) -> bool:
        return tool in self.mutating_tools

    def is_irreversible(self, tool: str) -> bool:
        return tool in self.irreversible_tools

    def assigned_columns(self, table: str) -> Mapping[str, Assigned]:
        return self.assigned.get(table, {})
