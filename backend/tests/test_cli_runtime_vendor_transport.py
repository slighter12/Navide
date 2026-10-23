"""G2 ticket 05: re-wake, message delivery and vendor interrupt reach only the intended session on the
Rust runtime, and a stale or wrong target is refused rather than made to pass.

The scenarios are those of `cli_regression/test_vendor_transport.py` (same external peers, same
production spawn wiring), run with the Rust runtime selected and extended with a second pane and a
stale target. The vendors are launched through the structured composition of ticket 05.
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx
import pytest

from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.test_vendor_transport import _install, _records, _spawn

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression, pytest.mark.cli_vendor,
              pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="POSIX composition only")]


def _rust_backend(root) -> BackendProcess:
    backend = BackendProcess(root)
    if os.environ.get("NAVIDE_G2_RUNTIME") == "python":  # checks the expectations against the pre-migration runtime
        return backend
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    if binary := os.environ.get("NAVIDE_CLI_RUNTIME_BIN"):
        backend.env["NAVIDE_CLI_RUNTIME_BIN"] = binary
    return backend


def _http(backend: BackendProcess, vendor: str) -> list[dict]:
    return [record for record in _records(backend, vendor) if record["kind"] == "http"]


@pytest.mark.parametrize("vendor,other", [("opencode", "kilo"), ("kilo", "opencode")])
async def test_http_delivery_reaches_only_the_intended_pane(tmp_path, vendor, other):
    backend = _rust_backend(tmp_path)
    command, other_command = _install(backend, vendor), _install(backend, other)
    async with backend, backend.connect() as ws:
        await _spawn(backend, ws, vendor, command)
        await _spawn(backend, ws, other, other_command)
        response = await backend.request(ws, "agent_msg.push", {"pane_id": vendor, "text": "first\nmessage"})
        assert response["payload"] == {"ok": True, "kind": "tui-http", "reason": "", "unclear": False}
        assert [record["path"] for record in _http(backend, vendor)] == ["/tui/append-prompt", "/tui/submit-prompt"]
        assert _http(backend, other) == [], "a message for one pane reached another vendor's server"
        wrong = await backend.request(ws, "agent_msg.push", {"pane_id": f"{vendor}-stale", "text": "nobody"})
        assert wrong["payload"]["ok"] is False
        assert len(_http(backend, vendor)) == 2 and _http(backend, other) == []


async def test_a_pane_that_was_killed_is_a_stale_target(tmp_path):
    backend = _rust_backend(tmp_path)
    command = _install(backend, "opencode")
    async with backend, backend.connect() as ws:
        created = await _spawn(backend, ws, "opencode", command)
        await backend.request(ws, "terminal.kill", {"terminal_session_id": created["terminal_session_id"]})
        stale = await backend.request(ws, "agent_msg.push", {"pane_id": "opencode", "text": "late"})
        assert stale["payload"]["ok"] is False
        assert _http(backend, "opencode") == []


async def test_qwen_file_delivery_goes_to_its_own_input_file(tmp_path):
    backend = _rust_backend(tmp_path)
    command, other = _install(backend, "qwen"), _install(backend, "opencode")
    async with backend, backend.connect() as ws:
        await _spawn(backend, ws, "qwen", command)
        await _spawn(backend, ws, "opencode", other)
        envelope = "[Navide MSG] from: fixture\nUnicode: 測試\nthird line"
        response = await backend.request(ws, "agent_msg.push", {"pane_id": "qwen", "text": envelope})
        assert response["payload"] == {"ok": True, "kind": "input-file", "reason": "", "unclear": False}
        async with asyncio.timeout(10):
            while not any(record["kind"] == "file" for record in _records(backend, "qwen")):
                await asyncio.sleep(0.01)
        assert [record["value"] for record in _records(backend, "qwen") if record["kind"] == "file"] == [
            {"type": "submit", "text": envelope}]
        assert _http(backend, "opencode") == []


@pytest.mark.parametrize("vendor,expected", [("codex", 27), ("mcode", 3)])
async def test_interrupt_reaches_only_the_target_vendor_process(tmp_path, vendor, expected):
    other = "mcode" if vendor == "codex" else "codex"
    backend = _rust_backend(tmp_path)
    command, other_command = _install(backend, vendor), _install(backend, other)
    async with backend, backend.connect() as ws:
        created = await _spawn(backend, ws, vendor, command)
        await _spawn(backend, ws, other, other_command)
        await backend.request(ws, "terminal.interrupt", {"terminal_session_id": created["terminal_session_id"]})
        await backend.expect_output(ws, created["terminal_session_id"], f"INTERRUPT_{expected}")
        assert {"kind": "interrupt", "value": expected} in _records(backend, vendor)
        assert not [record for record in _records(backend, other) if record["kind"] == "interrupt"]


@pytest.mark.parametrize("vendor,expected", [("claude", ["auth", "login"]), ("codex", ["login"]), ("muse", ["login"])])
async def test_login_subcommand_excludes_repl_wiring(tmp_path, vendor, expected):
    backend = _rust_backend(tmp_path)
    command = _install(backend, vendor)
    command[-1] += " --fixture-repl-only"
    async with backend, backend.connect() as ws:
        await _spawn(backend, ws, vendor, command, is_login=True)
        assert _records(backend, vendor)[0]["argv"] == expected
        response = await backend.request(ws, "agent_msg.push", {"pane_id": vendor, "text": "must not deliver"})
        assert response["payload"]["ok"] is False
        assert response["payload"]["reason"] == "no-channel"


@pytest.mark.parametrize("vendor", ["opencode", "kilo"])
async def test_managed_login_rejects_unverifiable_command(tmp_path, vendor):
    backend = _rust_backend(tmp_path)
    command = _install(backend, vendor)
    async with backend, backend.connect() as ws:
        response = await backend.request(ws, "terminal.create", {
            "pane_id": vendor, "agent_key": vendor, "command": command,
            "cwd": str(tmp_path), "is_login": True}, ok=False)
        assert response["ok"] is False
        assert response["error"]["code"] == "CREDENTIAL_STORE_UNVERIFIED"
        assert _records(backend, vendor) == []


async def test_claude_rewake_is_authenticated_and_bound_to_its_own_session(tmp_path):
    backend = _rust_backend(tmp_path)
    command, other = _install(backend, "claude"), _install(backend, "codex")
    async with backend, backend.connect() as ws:
        await _spawn(backend, ws, "claude", command, metadata={"explicit_session_id": "claude-regression-session"})
        await _spawn(backend, ws, "codex", other)
        base_url = backend.url.replace("ws://", "http://").split("/ws?", 1)[0]
        header, value = (tmp_path / "data" / "hook-auth").read_text().strip().split(": ", 1)
        async with httpx.AsyncClient(base_url=base_url, timeout=15, trust_env=False) as client:
            denied = await client.post("/hooks/claude/rewake", json={"session_id": "claude-regression-session"})
            assert denied.status_code == 403
            waiting = asyncio.create_task(client.post("/hooks/claude/rewake", headers={header: value},
                                                      json={"session_id": "claude-regression-session"}))
            try:
                async with asyncio.timeout(10):
                    while True:
                        event = await backend.receive(ws)
                        if event and event.get("type") == "agent_msg.push_state" and event["payload"].get("ready"):
                            assert event["payload"]["pane_id"] == "claude"
                            break
                envelope = "[Navide MSG] wake the attributed pane"
                misdirected = await backend.request(ws, "agent_msg.push", {"pane_id": "codex", "text": envelope})
                assert misdirected["payload"]["ok"] is False, "a message for another pane woke the armed Claude pane"
                response = await backend.request(ws, "agent_msg.push", {"pane_id": "claude", "text": envelope})
                assert response["payload"]["ok"] is True
                answer = await waiting
                assert answer.status_code == 200 and answer.text.endswith("\n" + envelope)
                again = await backend.request(ws, "agent_msg.push", {"pane_id": "claude", "text": "second"})
                assert again["payload"]["ok"] is False and again["payload"]["reason"] == "not-armed"
            finally:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
