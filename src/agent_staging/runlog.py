# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Revtiraman Tripathi
"""Append-only, hash-chained run log (JSONL).

Each record carries the SHA-256 of the previous record, so editing, deleting or
reordering a line breaks the chain and `verify()` reports where.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def canonical(obj: Any) -> str:
    """The one JSON encoding everything is hashed in: sorted keys, no spaces."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


class RunLogError(Exception):
    pass


@dataclass
class RunLog:
    path: Path

    @classmethod
    def create(cls, path: Path, header: dict[str, Any]) -> RunLog:
        if path.exists():
            raise RunLogError(f"run log already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        log = cls(path)
        log.append("run.start", header)
        return log

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            raise RunLogError(f"no run log at {self.path}")
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

    def _tail(self) -> tuple[int, str]:
        if not self.path.exists():
            return 0, GENESIS
        records = self.records()
        if not records:
            return 0, GENESIS
        return records[-1]["seq"] + 1, records[-1]["hash"]

    def append(self, type_: str, data: dict[str, Any]) -> dict[str, Any]:
        seq, prev = self._tail()
        body = {
            "seq": seq,
            "type": type_,
            "at": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "data": data,
            "prev": prev,
        }
        record = {**body, "hash": sha256(body)}
        with self.path.open("a") as f:
            f.write(canonical(record) + "\n")
        return record

    def verify(self) -> list[str]:
        """Return a list of problems; empty means the chain is intact."""
        problems: list[str] = []
        prev = GENESIS
        for n, record in enumerate(self.records()):
            body = {k: record[k] for k in ("seq", "type", "at", "data", "prev")}
            if record["seq"] != n:
                problems.append(f"record {n}: seq is {record['seq']}")
            if record["prev"] != prev:
                problems.append(f"record {n}: prev hash does not match record {n - 1}")
            if sha256(body) != record["hash"]:
                problems.append(f"record {n}: content does not match its hash")
            prev = record["hash"]
        return problems
