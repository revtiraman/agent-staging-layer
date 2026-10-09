# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""`staging` command line (spike subset; the twin is refund_path until milestone 7)."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from agent_staging import apply, approval, dryrun, reexec, replay, spike
from agent_staging.kernel import TwinToolError
from agent_staging.runlog import RunLog, RunLogError
from agent_staging.twins.seahaven_twin import KernelError
from agent_staging.workspace import Workspace


def _seed(ws: Workspace, _: argparse.Namespace) -> int:
    print(f"production is {spike.seed(ws)}")
    return 0


def _run(ws: Workspace, _: argparse.Namespace) -> int:
    plan, run_id = dryrun.dry_run(ws, spike.twin(ws), spike.support_agent, agent_name="spike.support_agent")
    print(dryrun.render(plan))
    print(f"\nProduction was not changed. Run log: {ws.run_log_path(run_id)}")
    print(f"Next: staging approve {plan.id}")
    return 0


def _approve(ws: Workspace, args: argparse.Namespace) -> int:
    record = approval.decide(ws, args.plan_id)
    if record["decision"] == "approved":
        print(f"Next: staging apply {args.plan_id}")
        return 0
    return 1


def _apply(ws: Workspace, args: argparse.Namespace) -> int:
    result = apply.apply_plan(ws, spike.twin(ws), args.plan_id)
    print(
        f"Applied {result['applied']} operations from {result['plan_id']}: production {result['previous']} -> {result['head']}"
    )
    return 0


def _log(ws: Workspace, args: argparse.Namespace) -> int:
    log = RunLog(ws.run_log_path(args.run_id))
    for r in log.records():
        print(json.dumps({"seq": r["seq"], "type": r["type"], "data": r["data"]}, default=str))
    problems = log.verify()
    print(
        ("chain OK: " if not problems else "CHAIN BROKEN: ") + (", ".join(problems) or f"{len(log.records())} records"),
        file=sys.stderr,
    )
    return 1 if problems else 0


def _replay(ws: Workspace, args: argparse.Namespace) -> int:
    from agent_staging.twins.refund_path_invariants import INVARIANTS

    if args.execute:
        executed = reexec.execute(ws, spike.twin(ws), args.run_id, INVARIANTS)
        print(f"re-executed {executed['compared']} records -> {executed['replay_log']}")
        for line in executed["notes"]:
            print(f"  note: {line}")
        for note in executed["advisory"]:
            print(f"ADVISORY (recorded, not re-judged) {note['advisor']}: {note['message']}")
        print(executed["verdict"].upper() + (f": {executed['detail']}" if executed["detail"] else ""))
        return 0 if executed["verdict"] == "reproduced" else 1
    result = replay.replay(ws, spike.twin(ws), args.run_id, INVARIANTS)
    for f in result["failures"]:
        print(f"FAILED {f['invariant']} v{f['version']} at step {f['step']}: {f['message']}")
    for entry in result["invariants"]:
        print(f"  {entry['invariant']}: {entry['outcome']}" + (f" ({entry['note']})" if "note" in entry else ""))
    for note in result["advisory"]:
        print(f"ADVISORY (recorded, not re-judged) {note['advisor']}: {note['message']}")
    print(result["verdict"].upper())
    return 0 if result["verdict"] == "reproduced" else 1


def main(argv: list[str] | None = None) -> int:
    logging.getLogger("seahaven").setLevel(logging.CRITICAL)  # ToolErrors are reported by us
    parser = argparse.ArgumentParser(prog="staging", description="Staging layer for AI agents (spike).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("spike", help="milestone-1 spike commands").add_subparsers(dest="spike_cmd", required=True)
    sp.add_parser("seed", help="create the production state").set_defaults(fn=_seed)
    sp.add_parser("run", help="dry-run the support agent and print the plan").set_defaults(fn=_run)
    p = sub.add_parser("approve", help="approve or reject a plan at the terminal")
    p.add_argument("plan_id")
    p.set_defaults(fn=_approve)
    p = sub.add_parser("apply", help="apply an approved plan to production")
    p.add_argument("plan_id")
    p.set_defaults(fn=_apply)
    p = sub.add_parser("replay", help="re-evaluate a run's invariants from its log alone")
    p.add_argument("run_id")
    p.add_argument(
        "--execute",
        action="store_true",
        help="re-execute the run: rebuild the environment, re-feed the recorded agent requests, compare logs",
    )
    p.set_defaults(fn=_replay)
    p = sub.add_parser("log", help="print a run log and verify its hash chain")
    p.add_argument("run_id")
    p.set_defaults(fn=_log)
    args = parser.parse_args(argv)
    try:
        return args.fn(Workspace.from_env(), args)
    except (
        dryrun.DryRunError,
        approval.ApprovalError,
        apply.ApplyError,
        replay.ReplayError,
        reexec.Divergence,
        RunLogError,
        KernelError,
        TwinToolError,
    ) as e:
        print(f"staging: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
