# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""`staging` command line (milestone 1 subset)."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from agent_staging import apply, approval, dryrun, spike
from agent_staging.runlog import RunLog, RunLogError
from agent_staging.workspace import Workspace


def _seed(ws: Workspace, _: argparse.Namespace) -> int:
    print(f"production is {spike.seed(ws)}")
    return 0


def _run(ws: Workspace, _: argparse.Namespace) -> int:
    plan, run_id = dryrun.dry_run(ws, spike.support_agent, agent_name="spike.support_agent")
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
    result = apply.apply_plan(ws, args.plan_id)
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
    p = sub.add_parser("log", help="print a run log and verify its hash chain")
    p.add_argument("run_id")
    p.set_defaults(fn=_log)
    args = parser.parse_args(argv)
    try:
        return args.fn(Workspace.from_env(), args)
    except (dryrun.DryRunError, approval.ApprovalError, apply.ApplyError, RunLogError) as e:
        print(f"staging: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
