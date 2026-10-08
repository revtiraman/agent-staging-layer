# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""refund_path: a deliberately tiny test world for building the staging layer.

TEST SCAFFOLDING ONLY. This is not a model of any vendor's API. It has just enough
state (customers, charges, refunds) for refund rules to matter, so the layer above it
(dry-run, diff, approval, apply, run log) can be built and tested before a real twin
is chosen (docs/architecture-decision-record.md, A2).
"""

from __future__ import annotations

from pathlib import Path

import seahaven

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
"""

# Tools that change state. Anything that writes and is not listed here is treated as
# an unmapped write by the dry-run and blocks the plan (agent_staging.dryrun).
MUTATING_TOOLS = frozenset({"create_customer", "create_charge", "refund_charge"})


def _next_id(ctx: seahaven.Ctx, table: str, prefix: str) -> str:
    row = ctx.db.one(f"SELECT COUNT(*) AS n FROM {table}")
    n = (row["n"] if row else 0) + 1
    return f"{prefix}-{n:04d}"


def make_world(fixtures_dir: Path | str) -> seahaven.World:
    """Build the world with its fixtures stored under `fixtures_dir`."""
    world = seahaven.World(
        name="refund_path",
        version="0.1.0",
        schema=SCHEMA,
        state_format="seahaven.state+calls/1",
        default_clock_mode="tick",
        fixtures_dir=fixtures_dir,
        description="Test scaffolding: customers, charges and refunds.",
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
        return {"id": rid, "charge_id": charge_id, "amount": amount, "reason": reason}

    return world


# --- What the staging layer needs to know about this world ------------------------------

# Columns whose value is set from the clock when the write happens. Applying a plan later
# legitimately gives them a different value, so they are excluded when an apply is checked
# against the plan (docs/spike-notes.md, decision 4).
VOLATILE_COLUMNS: dict[str, frozenset[str]] = {"refunds": frozenset({"created_at"})}

# Tools that are irreversible in the real world (an email that is sent stays sent).
# None in this world; the field exists so the plan can show it.
IRREVERSIBLE_TOOLS: frozenset[str] = frozenset()


def describe(
    tool: str, arguments: dict[str, object], changes: list[dict[str, object]], db: seahaven.Db
) -> tuple[str, int]:
    """One human-readable line for a mutating call, and the money it moves (cents).

    The amount comes from the rows the call wrote (`changes`), not from the arguments, so
    "refund the remainder" shows the real figure. `db` is read only for names.
    """
    if tool == "refund_charge":
        row = db.one(
            "SELECT cu.id AS customer_id, cu.email FROM charges c "
            "JOIN customers cu ON cu.id = c.customer_id WHERE c.id = ?",
            str(arguments["charge_id"]),
        )
        written = [c["after"] for c in changes if c["table"] == "refunds" and c["op"] == "insert"]
        if written:
            cents = sum(int(r["amount"]) for r in written)  # type: ignore[index]
        else:
            requested = arguments.get("amount")
            cents = int(requested) if isinstance(requested, int) else 0
        who = f"{row['customer_id']} <{row['email']}>" if row else "unknown customer"
        amount = _money(cents) if cents else "the remainder"
        return f"Refund {amount} on {arguments['charge_id']} to {who}", cents
    if tool == "create_charge":
        return f"Charge {_money(int(arguments['amount']))} to {arguments['customer_id']}", int(arguments["amount"])  # type: ignore[arg-type]
    if tool == "create_customer":
        return f"Create customer {arguments['email']}", 0
    return f"{tool}({arguments})", 0


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"
