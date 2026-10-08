# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Milestone 1 spike: seed data and a scripted support agent for refund_path.

The agent is deliberately plain: it reads each charge, then asks for a refund, and
carries on when a tool refuses, like a real agent reading an API error.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_staging.session import AgentToolError, Session
from agent_staging.twins.refund_path import RefundPath
from agent_staging.workspace import Workspace

START = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

# Three customers asked for refunds. Support already refunded the third by hand, so the
# agent's third refund must be refused, and the plan must show it as skipped.
REQUESTS = [
    {"charge_id": "charge-0001", "note": "item never arrived"},
    {"charge_id": "charge-0002", "note": "duplicate order"},
    {"charge_id": "charge-0003", "note": "wrong size"},
]


def twin(ws: Workspace) -> RefundPath:
    return RefundPath(ws.fixtures)


def seed(ws: Workspace) -> str:
    if ws.prod_head() is not None:
        return ws.prod_head()  # type: ignore[return-value]
    with twin(ws).open(None, start=START) as inst:
        for email, cents in [("ana@example.com", 12000), ("ben@example.com", 8000), ("cy@example.com", 9500)]:
            customer = inst.call("create_customer", email=email)
            inst.call("create_charge", customer_id=customer["id"], amount=cents)
        inst.call("refund_charge", charge_id="charge-0003", reason="refunded_by_support")
        fixture_id = ws.next_prod_id()
        inst.freeze(fixture_id, "seed: 3 customers, 3 charges, charge-0003 refunded by support")
    ws.set_prod_head(fixture_id)
    return fixture_id


def support_agent(session: Session) -> None:
    for request in REQUESTS:
        try:
            charge = session.call("get_charge", charge_id=request["charge_id"])
            session.call("refund_charge", charge_id=charge["id"], reason="requested_by_customer")
        except AgentToolError:
            continue
