"""Test-only descriptor probe for L1e/L1f (E3 S13 (e); E2 `close_once_ok`): records the number a handle frees, so a
test can later check that an unrelated resource that reuses the number is not closed by a late closer.

It lives in the harness only. The product's receipts never carry descriptor numbers (E1 5.2), and this probe
writes nothing to them: numbers stay in this object, in the test's memory.
"""
from __future__ import annotations

import os


class DescriptorProbe:
    def __init__(self, monkeypatch) -> None:
        from agent_team_backend.cli_runtime_handoff import MasterHandle
        self.freed: list[tuple[int, int]] = []  # (handle id, number)
        original = MasterHandle.close
        probe = self

        def close(handle) -> None:
            number = handle.fd
            original(handle)
            if number is not None and handle.fd is None:
                probe.freed.append((id(handle), number))

        monkeypatch.setattr(MasterHandle, "close", close)

    def numbers(self) -> list[int]:
        return [number for _, number in self.freed]

    @staticmethod
    def is_open(number: int) -> bool:
        try:
            os.fstat(number)
            return True
        except OSError:
            return False
