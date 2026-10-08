# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""refund_path's hard invariants (docs/invariant-engine.md, "The first two invariants").

Pure functions of (state, changes, faults, intent). No I/O, no clock, no randomness.
Bump `version` whenever the logic changes: replay compares versions.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from agent_staging.invariants import Intent, State, Timeline, Violation, row_evidence
from agent_staging.kernel import money


class RefundedLeRequested:
    """For every charge, the amount refunded during the run must not exceed what the intent
    asked for. A charge nobody asked to refund has a requested amount of 0. A request with no
    amount means the charge's unrefunded remainder in the base state."""

    name = "refunded_le_requested"
    version = 1

    def check(
        self, state: State, changes: Sequence[Mapping[str, Any]], faults: Timeline, intent: Intent
    ) -> list[Violation]:
        refunded: dict[str, int] = defaultdict(int)
        refunds: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        made_by: dict[str, set[int]] = defaultdict(set)
        for c in changes:
            if c["table"] == "refunds" and c["op"] == "insert":
                charge_id = c["after"]["charge_id"]
                refunded[charge_id] += int(c["after"]["amount"])
                refunds[charge_id].append(c["after"])
                if c.get("request") is not None:
                    made_by[charge_id].add(c["request"])

        requested: dict[str, int] = defaultdict(int)
        for r in intent.requests:
            if r.get("action") != "refund":
                continue
            amount = r.get("amount")
            if amount is None:
                base = state.base_row("charges", {"id": r["charge_id"]})
                amount = 0 if base is None else int(base["amount"]) - int(base["amount_refunded"])
            requested[r["charge_id"]] += int(amount)

        violations = []
        for charge_id, total in sorted(refunded.items()):
            asked = requested.get(charge_id, 0)
            if total <= asked:
                continue
            charge = state.row("charges", {"id": charge_id})
            requests = sorted(made_by[charge_id])
            violations.append(
                Violation(
                    subject=f"charge {charge_id}",
                    message=(
                        f"{charge_id}: {money(total)} refunded during the run in {len(refunds[charge_id])} "
                        f"refund(s); {money(asked)} was requested"
                    ),
                    rows=tuple([row_evidence("charges", charge)] if charge else [])
                    + tuple(row_evidence("refunds", r) for r in refunds[charge_id]),
                    faults=tuple(f["key"] for n in requests for f in faults.faults_on_request(n)),
                    requests=tuple(requests),
                )
            )
        return violations


INVARIANTS = (RefundedLeRequested(),)
