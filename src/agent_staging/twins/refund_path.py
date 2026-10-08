# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""refund_path: a deliberately tiny test world for building the staging layer.

TEST SCAFFOLDING ONLY. This is not a model of any vendor's API. It has just enough
state (customers, charges, refunds) for refund rules to matter, and one outbound event
(`refund.created`) so the harness-owned clock and webhook delivery can be tested. It is
one of the two implementations of the kernel interface (docs/kernel-interface.md); the
Stripe twin is the other.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import seahaven

from agent_staging.kernel import Assigned, Branch, Change, Described, money
from agent_staging.twins.seahaven_twin import SeahavenTwin

SCHEMA = """
CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL
) STRICT;

CREATE TABLE charges (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    amount INTEGER NOT NULL CHECK (amount > 0),
    amount_refunded INTEGER NOT NULL DEFAULT 0 CHECK (amount_refunded >= 0),
    CHECK (amount_refunded <= amount)
) STRICT;

CREATE TABLE refunds (
    id TEXT PRIMARY KEY,
    charge_id TEXT NOT NULL REFERENCES charges(id),
    amount INTEGER NOT NULL CHECK (amount > 0),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;

-- The outbox: every row is one event the twin sends. Written in the same call as the
-- change it reports, so a refused call emits nothing.
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
"""


def _next_id(ctx: seahaven.Ctx, table: str, prefix: str) -> str:
    row = ctx.db.one(f"SELECT COUNT(*) AS n FROM {table}")
    n = (row["n"] if row else 0) + 1
    return f"{prefix}-{n:04d}"


def _emit(ctx: seahaven.Ctx, type_: str, payload: dict[str, Any]) -> None:
    ctx.db.execute(
        "INSERT INTO events (id, type, payload, created_at) VALUES (?, ?, ?, ?)",
        _next_id(ctx, "events", "evt"),
        type_,
        json.dumps(payload, sort_keys=True),
        ctx.clock.iso(),
    )


def make_world(fixtures_dir: Path | str) -> seahaven.World:
    """Build the world with its fixtures stored under `fixtures_dir`."""
    world = seahaven.World(
        name="refund_path",
        version="0.2.0",
        schema=SCHEMA,
        state_format="seahaven.state+calls/1",
        default_clock_mode="fixed",
        fixtures_dir=fixtures_dir,
        description="Test scaffolding: customers, charges, refunds and refund events.",
    )

    @world.tool
    def create_customer(ctx: seahaven.Ctx, email: str) -> dict[str, object]:
        """Create a customer."""
        cid = _next_id(ctx, "customers", "cust")
        ctx.db.execute("INSERT INTO customers (id, email) VALUES (?, ?)", cid, email)
        return {"id": cid, "email": email}

    @world.tool
    def create_charge(ctx: seahaven.Ctx, customer_id: str, amount: int) -> dict[str, object]:
        """Charge a customer `amount` cents."""
        if ctx.db.one("SELECT id FROM customers WHERE id = ?", customer_id) is None:
            raise seahaven.ToolError("not_found", f"no such customer: {customer_id}")
        if amount <= 0:
            raise seahaven.ToolError("invalid_amount", "amount must be a positive number of cents")
        chid = _next_id(ctx, "charges", "charge")
        ctx.db.execute("INSERT INTO charges (id, customer_id, amount) VALUES (?, ?, ?)", chid, customer_id, amount)
        return get_charge(ctx, chid)

    @world.tool
    def get_charge(ctx: seahaven.Ctx, charge_id: str) -> dict[str, object]:
        """Read one charge, including how much of it has been refunded."""
        row = ctx.db.one("SELECT * FROM charges WHERE id = ?", charge_id)
        if row is None:
            raise seahaven.ToolError("not_found", f"no such charge: {charge_id}")
        return row

    @world.tool
    def list_charges(ctx: seahaven.Ctx, customer_id: str) -> list[dict[str, object]]:
        """List a customer's charges."""
        return ctx.db.rows("SELECT * FROM charges WHERE customer_id = ? ORDER BY id", customer_id)

    @world.tool
    def refund_charge(
        ctx: seahaven.Ctx, charge_id: str, amount: int | None = None, reason: str = "requested_by_customer"
    ) -> dict[str, object]:
        """Refund `amount` cents of a charge (the whole remainder if omitted)."""
        charge = ctx.db.one("SELECT * FROM charges WHERE id = ?", charge_id)
        if charge is None:
            raise seahaven.ToolError("not_found", f"no such charge: {charge_id}")
        remaining = charge["amount"] - charge["amount_refunded"]
        if remaining == 0:
            raise seahaven.ToolError("already_refunded", f"charge {charge_id} is already fully refunded")
        amount = remaining if amount is None else amount
        if amount <= 0:
            raise seahaven.ToolError("invalid_amount", "refund amount must be a positive number of cents")
        if amount > remaining:
            raise seahaven.ToolError(
                "amount_too_large",
                f"refund amount ({amount}) is greater than the unrefunded amount on {charge_id} ({remaining})",
            )
        rid = _next_id(ctx, "refunds", "refund")
        ctx.db.execute(
            "INSERT INTO refunds (id, charge_id, amount, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            rid,
            charge_id,
            amount,
            reason,
            ctx.clock.iso(),
        )
        ctx.db.execute("UPDATE charges SET amount_refunded = amount_refunded + ? WHERE id = ?", amount, charge_id)
        _emit(ctx, "refund.created", {"refund_id": rid, "charge_id": charge_id, "amount": amount})
        return {"id": rid, "charge_id": charge_id, "amount": amount, "reason": reason}

    return world


class RefundPath(SeahavenTwin):
    """refund_path behind the kernel interface."""

    name = "refund_path"
    version = "0.2.0"
    outbox_table = "events"
    mutating_tools = frozenset({"create_customer", "create_charge", "refund_charge"})
    irreversible_tools = frozenset()  # none here; the plan still shows the flag
    assigned: ClassVar[Mapping[str, Mapping[str, Assigned]]] = {
        "customers": {"id": "id"},
        "charges": {"id": "id"},
        "refunds": {"id": "id", "created_at": "time"},
        "events": {"id": "id", "created_at": "time"},
    }

    def make_world(self, fixtures_dir: Path) -> seahaven.World:
        return make_world(fixtures_dir)

    def describe(self, branch: Branch, tool: str, arguments: Mapping[str, Any], changes: list[Change]) -> Described:
        """The amount comes from the rows the call wrote, not the arguments, so "refund the
        remainder" shows the real figure. Objects are named by the charge and customer,
        which existed before the call, never by the refund id the call assigned."""
        if tool == "refund_charge":
            charge_id = str(arguments["charge_id"])
            charge = branch.row("charges", {"id": charge_id})
            customer = branch.row("customers", {"id": charge["customer_id"]}) if charge else None
            written = [c.after for c in changes if c.table == "refunds" and c.op == "insert" and c.after]
            if written:
                cents = sum(int(r["amount"]) for r in written)
            else:
                requested = arguments.get("amount")
                cents = requested if isinstance(requested, int) else 0
            who = f"{customer['id']} <{customer['email']}>" if customer else "unknown customer"
            amount = money(cents) if cents else "the remainder"
            return Described(f"Refund {amount} on {charge_id} to {who}", cents)
        if tool == "create_charge":
            cents = int(arguments["amount"])
            return Described(f"Charge {money(cents)} to {arguments['customer_id']}", cents)
        if tool == "create_customer":
            return Described(f"Create customer {arguments['email']}")
        return Described(f"{tool}({dict(arguments)})")
