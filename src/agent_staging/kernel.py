# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""The twin kernel interface: what the staging layer needs from a twin, and nothing more.

It has exactly two intended implementations: `refund_path` (test scaffolding) and the
Stripe twin (milestone 8). The contract, and why each part is there, is in
docs/kernel-interface.md; this module is its code form.

Three rules the types encode:
  * Time belongs to the harness. A branch's clock moves only when `advance()` is called,
    and world code reads time only through the kernel (`ctx.clock` under Seahaven).
  * Ids a branch assigns are not stable. Columns the system fills in (`assigned_columns`)
    are marked, so nothing above the kernel has to assume an id seen in a dry-run is the
    id production will give.
  * Every change belongs to one call. A change with `i is None` was made outside any
    call, and the plan treats it as unexplained.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

# What fills a column in a row the system creates: an id it mints, or the clock.
type Assigned = Literal["id", "time"]


class TwinToolError(Exception):
    """A tool refused the call, as a vendor API would. `code` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class StateMeta:
    """A saved state of a twin (under Seahaven, a fixture)."""

    id: str
    world: str
    world_version: str
    schema_hash: str
    file_sha256: str
    now: datetime  # the twin's clock when the state was saved


@dataclass(frozen=True)
class Call:
    """One call made on a branch, in order: `i` is its position."""

    i: int
    tool: str
    arguments: dict[str, Any]
    error: dict[str, str] | None  # {"code", "message"} if the tool refused


@dataclass(frozen=True)
class Change:
    """One row a call changed. `i` is the call that changed it, or None if no call did."""

    i: int | None
    table: str
    op: Literal["insert", "update", "delete"]
    key: dict[str, Any]
    before: dict[str, Any] | None
    after: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        """The plan's form of a change (without `i`, which the operation carries)."""
        return {"table": self.table, "op": self.op, "key": self.key, "before": self.before, "after": self.after}


@dataclass(frozen=True)
class Event:
    """A notification the twin sends out (a webhook), and when the harness delivers it."""

    seq: int  # emission order on this branch; ties on `due` deliver in this order
    type: str
    payload: dict[str, Any]
    emitted_at: datetime
    due: datetime


@dataclass(frozen=True)
class Described:
    """A mutating call in words a person approves, and the money it moves."""

    text: str
    amount: int = 0  # minor units (cents)
    currency: str = "usd"


class Branch(Protocol):
    """A running copy of a state. Changes stay here until frozen into a new state."""

    def call(self, tool: str, /, **arguments: Any) -> Any:
        """Run a tool. Raises TwinToolError if the tool refuses."""
        ...

    def calls(self) -> list[Call]: ...

    def changes(self) -> list[Change]: ...

    def row(self, table: str, key: Mapping[str, Any]) -> dict[str, Any] | None:
        """Read one row by primary key, for describing and stale checks. Not a tool."""
        ...

    def now(self) -> datetime:
        """What time the twin thinks it is."""
        ...

    def advance(self, by: timedelta | float) -> list[Event]:
        """Move the clock forward `by` (seconds if a number), delivering every event that
        falls due on the way, each at its own due time, in (due, seq) order. Returns them."""
        ...

    def pending(self) -> list[Event]:
        """Events emitted but not yet due, in delivery order."""
        ...

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        """Receive events as they are delivered (the agent's webhook endpoint)."""
        ...

    def freeze(self, state_id: str, description: str) -> StateMeta: ...


class TwinKernel(Protocol):
    """A twin: its saved states, how to branch one, and what its tools mean."""

    name: str
    version: str

    def state(self, state_id: str) -> StateMeta: ...

    def open(
        self,
        state_id: str | None,
        *,
        start: datetime | None = None,
        seed: int | None = None,
        delivery_delay: timedelta = timedelta(0),
    ) -> AbstractContextManager[Branch]:
        """Branch a saved state (or an empty twin if `state_id` is None, which needs `start`).

        The clock starts at the state's own time, or at `start` if given (never earlier).
        `seed` fixes every seeded value the twin draws. `delivery_delay` is how long after
        emission each event is delivered; milestone 3 replaces it with a seeded schedule.
        """
        ...

    def is_mutating(self, tool: str) -> bool: ...

    def is_irreversible(self, tool: str) -> bool: ...

    def assigned_columns(self, table: str) -> Mapping[str, Assigned]: ...

    def describe(self, branch: Branch, tool: str, arguments: Mapping[str, Any], changes: list[Change]) -> Described:
        """One line for a mutating call, as of `branch`. Must name objects by references that
        existed before the call, never by an id the call itself assigned."""
        ...


def as_timedelta(by: timedelta | float) -> timedelta:
    delta = by if isinstance(by, timedelta) else timedelta(seconds=by)
    if delta < timedelta(0):
        raise ValueError("time only moves forward: advance() needs a duration >= 0")
    return delta


def money(minor: int, currency: str = "usd") -> str:
    return f"${minor / 100:,.2f}" if currency == "usd" else f"{minor / 100:,.2f} {currency.upper()}"
