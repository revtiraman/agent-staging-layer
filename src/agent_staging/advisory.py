# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Soft invariants: advisory, like a linter (docs/invariant-engine.md, "Soft invariants").

Two guarantees, enforced by the code's shape rather than by convention:

* **Outcome is hard-only.** An advisor returns `list[AdvisoryNote]`, never a `Violation`, and
  `review()` refuses to run before the run's `run.end` record exists. The outcome and exit
  code are already written when the first advisor runs, so nothing here can change them.
* **Nothing that gates reads this module.** The harness, the hard engine, dry-run, approval,
  apply and both replays do not import it (tests/test_advisory.py checks the import graph).

An advisor may ask a judge (an LLM, a person, a heuristic) for an opinion. The judge's question,
context and full answer are written into the `advisory.note` record, so replay can report them
without ever asking the judge again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from agent_staging.invariants import BaseRows, Engine, Intent, State, Timeline
from agent_staging.runlog import RunLog

type Verdict = Literal["fits", "does_not_fit", "unsure"]


class AdvisoryError(Exception):
    pass


@dataclass(frozen=True)
class Judgement:
    verdict: Verdict
    rationale: str
    model: str  # what produced it, e.g. a model id, "heuristic/1", "person"


class Judge(Protocol):
    name: str

    def judge(self, question: str, context: Mapping[str, Any]) -> Judgement: ...


@dataclass(frozen=True)
class AdvisoryNote:
    """An opinion about a run. Not a failure: it has no way to fail anything."""

    subject: str
    verdict: Verdict
    message: str
    question: str
    context: Mapping[str, Any]
    judgement: Judgement


class Advisor(Protocol):
    name: str
    version: int

    def review(
        self, state: State, changes: Sequence[Mapping[str, Any]], faults: Timeline, intent: Intent, judge: Judge
    ) -> list[AdvisoryNote]: ...


def review(log: RunLog, base: BaseRows, advisors: Sequence[Advisor], judge: Judge | None) -> list[dict[str, Any]]:
    """Run the advisors over a finished run and append their notes after `run.end`."""
    records = log.records()
    if not any(r["type"] == "run.end" for r in records):
        raise AdvisoryError("advisors run only after run.end is written: the outcome must not wait for them")
    if judge is None:
        return [log.append("advisory.skipped", {"advisor": a.name, "reason": "no judge configured"}) for a in advisors]
    inputs = Engine([], base)  # the hard engine's input builder, with no invariants: same four inputs
    for record in records:
        inputs.feed(record)
    state = State(base, inputs.changes)
    written = []
    for advisor in advisors:
        for note in advisor.review(state, tuple(inputs.changes), inputs.timeline, inputs.intent, judge):
            written.append(
                log.append(
                    "advisory.note",
                    {
                        "advisor": advisor.name,
                        "version": advisor.version,
                        "subject": note.subject,
                        "verdict": note.verdict,
                        "message": note.message,
                        "judge": {
                            "name": judge.name,
                            "model": note.judgement.model,
                            "question": note.question,
                            "context": dict(note.context),
                            "verdict": note.judgement.verdict,
                            "rationale": note.judgement.rationale,
                        },
                    },
                )
            )
    return written


# --- the one advisor (refund_path) -----------------------------------------------------------


class RefundReasonFitsRequest:
    """Does the reason the agent gave a refund fit what the customer said? A judgement call, so
    advisory: a wrong reason misfiles a refund, it doesn't move money."""

    name = "refund_reason_fits_request"
    version = 1

    def review(
        self, state: State, changes: Sequence[Mapping[str, Any]], faults: Timeline, intent: Intent, judge: Judge
    ) -> list[AdvisoryNote]:
        said = {r["charge_id"]: r["note"] for r in intent.requests if r.get("action") == "refund" and r.get("note")}
        notes = []
        for c in changes:
            if c["table"] != "refunds" or c["op"] != "insert":
                continue
            refund = c["after"]
            if refund["charge_id"] not in said:
                continue
            question = "Does the refund reason fit what the customer said?"
            context = {"customer_said": said[refund["charge_id"]], "refund_reason": refund["reason"]}
            judgement = judge.judge(question, context)
            notes.append(
                AdvisoryNote(
                    subject=f"refund {refund['id']}",
                    verdict=judgement.verdict,
                    message=f"refund {refund['id']} reason {refund['reason']!r}: {judgement.verdict} ({judgement.rationale})",
                    question=question,
                    context=context,
                    judgement=judgement,
                )
            )
        return notes


class KeywordJudge:
    """A deterministic stand-in judge, for tests and for runs without a model configured.
    Not an LLM: it matches a few phrases. A real model judge plugs in behind `Judge`."""

    name = "keyword"
    _FITS: Mapping[str, tuple[str, ...]] = {
        "duplicate": ("duplicate", "twice", "double"),
        "fraudulent": ("fraud", "didn't make", "not me", "stolen"),
        "requested_by_customer": ("never arrived", "wrong size", "changed my mind", "return", "broken"),
    }

    def judge(self, question: str, context: Mapping[str, Any]) -> Judgement:
        said = str(context["customer_said"]).lower()
        reason = str(context["refund_reason"])
        if reason not in self._FITS:
            return Judgement("unsure", f"no phrases known for reason {reason!r}", "heuristic/1")
        for other, phrases in self._FITS.items():
            if any(p in said for p in phrases):
                if other == reason:
                    return Judgement("fits", f"the customer's words match {reason!r}", "heuristic/1")
                return Judgement("does_not_fit", f"the customer's words match {other!r}, not {reason!r}", "heuristic/1")
        return Judgement("unsure", "no known phrase in what the customer said", "heuristic/1")
