# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Where the staging layer keeps its files (default `./.staging`, override with STAGING_HOME).

fixtures/            Seahaven fixtures; production states are fixtures named prod-NNNN
prod/HEAD            id of the fixture that is "production" right now
plans/<id>.json      dry-run plans
approvals/<id>.json  human decisions on plans
runs/<id>/log.jsonl  hash-chained run logs
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Workspace:
    root: Path

    @classmethod
    def from_env(cls) -> Workspace:
        return cls(Path(os.environ.get("STAGING_HOME", ".staging")).resolve())

    @property
    def fixtures(self) -> Path:
        return self.root / "fixtures"

    def run_log_path(self, run_id: str) -> Path:
        return self.root / "runs" / run_id / "log.jsonl"

    def plan_path(self, plan_id: str) -> Path:
        return self.root / "plans" / f"{plan_id}.json"

    def approval_path(self, plan_id: str) -> Path:
        return self.root / "approvals" / f"{plan_id}.json"

    def prod_head(self) -> str | None:
        head = self.root / "prod" / "HEAD"
        return head.read_text().strip() if head.exists() else None

    def set_prod_head(self, fixture_id: str) -> None:
        head = self.root / "prod" / "HEAD"
        head.parent.mkdir(parents=True, exist_ok=True)
        tmp = head.with_suffix(".tmp")
        tmp.write_text(fixture_id + "\n")
        tmp.replace(head)

    def next_prod_id(self) -> str:
        existing = sorted(p.name for p in self.fixtures.glob("prod-*")) if self.fixtures.exists() else []
        n = int(existing[-1].split("-")[1]) + 1 if existing else 1
        return f"prod-{n:04d}"

    @staticmethod
    def new_run_id() -> str:
        return "run-" + uuid.uuid4().hex[:12]

    def write_json(self, path: Path, obj: dict[str, Any], *, exclusive: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x" if exclusive else "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True, default=str)
            f.write("\n")

    @staticmethod
    def read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text())
