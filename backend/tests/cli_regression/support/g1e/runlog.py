"""Run bookkeeping for the Python-side runners (E2 v13 V7 items 3 and 6); the TypeScript matrix runner has the same thing in `tests/cli/g1eRun.ts`.

BLINDNESS: until a matrix or reader-measure run is complete, a runner prints and persists outside the sealed receipts ONLY the per-attempt validity (`clean` / `invalid`) and the stop reason:
`lines()` and `to_record()` are the only things such a runner may show; neither can hold a measured value, a paired statistic or a failure text (which can quote a number). The detail of a
failed attempt lives in the sealed receipt. `to_record()` is `run.json` (`acr-g1e-run/1`): the first invalid attempt stops the run and is its last attempt.
"""
from __future__ import annotations

from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise

BLIND_KINDS = ("matrix", "reader-measure")


class RunLog:
    def __init__(self, kind: str) -> None:
        if kind not in ref.RUN_KINDS:
            raise ValueError(f"unknown run kind {kind!r}")
        self.kind = kind
        self.attempts: list[dict] = []
        self.stopped: str | None = None

    @property
    def blind(self) -> bool:
        return self.kind in BLIND_KINDS

    def record(self, name: str, validity: str, stop_reason: str | None = None) -> None:
        if self.stopped:
            raise RuntimeError(f"run already stopped ({self.stopped}); no attempt may follow")
        if validity not in ("clean", "invalid") or (validity == "clean") != (stop_reason is None) or (stop_reason is not None and stop_reason not in summarise.STOP_REASONS):
            raise ValueError(f"validity {validity!r} with stop reason {stop_reason!r} is not a legal attempt record")
        self.attempts.append({"name": name, "validity": validity, "stopReason": stop_reason})
        if validity == "invalid":
            self.stopped = stop_reason

    def lines(self) -> list[str]:
        return [f"{self.kind} {a['name']}: {a['validity']}" + (f" stop={a['stopReason']}" if a["stopReason"] else "") for a in self.attempts]

    def to_record(self, expected_attempts: int) -> dict:
        complete = self.stopped is None and len(self.attempts) == expected_attempts
        record = {"schema": summarise.RUN_SCHEMA, "kind": self.kind, "attempts": [dict(a) for a in self.attempts], "stopReason": self.stopped or (None if complete else "operator-stop"), "complete": complete}
        if problems := summarise.run_problems(record):
            raise RuntimeError("; ".join(problems))
        return record
