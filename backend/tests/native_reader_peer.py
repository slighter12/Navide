"""A native log reader whose every observation is the real sidecar's answer: shared test support for the tests that prove a
declaration's rule across the Python/sidecar boundary (the request the reader sends and the document it gets back).

`native_reader(...)` wires the reader's `observe_core` straight to a `FramePeer` over the built sidecar, so no backend process is
involved. `RequestPeer.ask` sends every request field exactly as the reader named it: `FramePeer.observe` spends the name `session`
on a live-session binding, which is also a request field of the Copilot log layout. A missing sidecar binary fails the tests that
use this module, it never skips them.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_team_backend import cli_runtime_native
from agent_team_backend.cli_runtime_frames import FrameType, code
from agent_team_backend.cli_runtime_observation import admit, request_of
from tests.cli_regression.support.frame_peer import PRODUCT, FramePeer


class RequestPeer(FramePeer):
    def ask(self, vendor, path, mode, request: dict):
        number = self.send(code.OBSERVE, {"vendor": vendor, "path": str(path), "mode": mode, **request})
        final = self.result(number, allow_error=True)
        if final.frame_type is FrameType.ERROR and not final.meta.get("observation_parts"):
            return None, final
        parts = self.parts.pop(number, [])
        self._sizes.pop(number, None)
        assert len(parts) == final.meta["observation_parts"], (len(parts), final.meta)
        return json.loads(b"".join(parts)), final


class Sidecar:
    """One sidecar process and the requests it was asked, so a test can name the exact wire key a reader sent."""

    def __init__(self, root: Path):
        assert PRODUCT.is_file(), f"the native sidecar is not built: {PRODUCT}"
        self.peer = RequestPeer(root)
        self.asked: list[dict] = []

    def observe_core(self, vendor, path, mode, *, checkpoint=None, seen=None, state=None, validate=None, match=None, **extra):
        request = {"checkpoint": dict(checkpoint or {}), "seen": sorted(seen or ()), "state": dict(state or {}), **extra}
        # The product's `observe_core` guards the request it sends (`cli_runtime_observation.admit`); so does this stand-in for it.
        admit(vendor, request_of(path, mode, checkpoint=checkpoint, seen=seen, state=state, **extra))
        self.asked.append({"vendor": vendor, "path": str(path), "mode": mode, **request})
        document, final = self.peer.ask(vendor, path, mode, request)
        assert document is not None, final.meta
        if validate:
            validate(document, None)
        return document

    def close(self) -> None:
        self.peer.close()


def wire(monkeypatch, root: Path) -> Sidecar:
    """Point `cli_runtime_native.observe_core` at a fresh sidecar; the caller closes it."""
    sidecar = Sidecar(root)
    monkeypatch.setattr(cli_runtime_native, "observe_core", sidecar.observe_core)
    return sidecar
