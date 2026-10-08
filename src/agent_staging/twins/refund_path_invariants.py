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


# What each mutating tool acts on, by argument name.
TARGET = {"refund_charge": "charge_id", "create_charge": "customer_id", "create_customer": "email"}


class NoRetryAfterLostResponseTimeout:
    """After a request to a mutating tool timed out having reached the twin (`lost_response`),
    the agent must not send the same tool to the same target again without first reading that
    target successfully. A refund's charge is read by `get_charge(charge_id)` or by
    `list_charges` for the charge's customer; a charge's customer by `list_charges(customer_id)`.
    `create_customer` has no read, so any retry of it after a lost response is a violation.

    Every later attempt counts, whatever happened to it: the rule is about what the agent sent,
    and `refunded_le_requested` judges what it cost."""

    name = "no_retry_after_lost_response_timeout"
    version = 1

    def _reads(self, state: State, tool: str, target: Any, call: Mapping[str, Any]) -> bool:
        if call.get("error") is not None or call.get("fault") is not None:
            return False  # a failed read tells the agent nothing
        args = call["arguments"]
        if tool == "refund_charge":
            if call["tool"] == "get_charge":
                return args.get("charge_id") == target
            if call["tool"] == "list_charges":
                charge = state.row("charges", {"id": target})
                return charge is not None and args.get("customer_id") == charge["customer_id"]
        if tool == "create_charge":
            return call["tool"] == "list_charges" and args.get("customer_id") == target
        return False

    def check(
        self, state: State, changes: Sequence[Mapping[str, Any]], faults: Timeline, intent: Intent
    ) -> list[Violation]:
        violations = []
        calls = faults.calls
        for k, lost in enumerate(calls):
            if lost.get("fault") != "lost_response" or lost["tool"] not in TARGET:
                continue
            tool = lost["tool"]
            target = lost["arguments"].get(TARGET[tool])
            for later in calls[k + 1 :]:
                if self._reads(state, tool, target, later):
                    break  # it checked first: an informed choice from here on
                if later["tool"] == tool and later["arguments"].get(TARGET[tool]) == target:
                    row = state.row("charges", {"id": target}) if tool == "refund_charge" else None
                    violations.append(
                        Violation(
                            subject=f"request {lost['request']}",
                            message=(
                                f"request {later['request']} retried {tool} on {target} after request "
                                f"{lost['request']} timed out having reached the twin, without reading {target} first"
                            ),
                            rows=(row_evidence("charges", row),) if row else (),
                            faults=tuple(f["key"] for f in faults.faults_on_request(lost["request"])),
                            requests=(lost["request"], later["request"]),
                        )
                    )
                    break
        return violations


INVARIANTS = (RefundedLeRequested(), NoRetryAfterLostResponseTimeout())
