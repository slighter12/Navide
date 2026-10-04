"""Claude reference integration through authenticated WS and native runtime."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shlex
import sys

import pytest

from tests.cli_regression.support.backend_process import BackendProcess
from tests.cli_regression.support.cli_shim import base_python_executable, install_cli_shim

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression,
              pytest.mark.skipif(sys.platform != "darwin", reason="native macOS reference evidence")]


def reference_backend(root: Path) -> tuple[BackendProcess, list[str]]:
    backend = BackendProcess(root)
    backend.env["NAVIDE_ACR_EVAL_RUNTIME"] = "rust-shell"
    script = Path(__file__).parent / "cli_regression/support/claude_runtime_peer.py"
    executable = install_cli_shim(root, "claude", [base_python_executable(), "-u", str(script), str(root)])
    # Own shell startup file, never the real user's startup configuration.
    (root / "home/.bash_profile").write_text(
        f"export PATH={shlex.quote(str(executable.parent))}:$PATH\nexport REFERENCE_SHELL_RC=login\n")
    (root / "home/.claude/projects").mkdir(parents=True, exist_ok=True)
    return backend, ["/bin/bash", "-ilc", "claude"]


async def live_totals(backend, ws, workspace, sid, expected):
    async with asyncio.timeout(15):
        while True:
            snap = (await backend.request(ws, "tokens.snapshot", {"workspace_path": str(workspace)}))["payload"]
            live = snap["workspace"]["live_by_session"].get(sid, {})
            if [live.get("input"), live.get("output")] == expected:
                return snap
            await asyncio.sleep(0.05)


async def test_native_claude_observes_usage_and_completion_without_python_parser(tmp_path):
    backend, command = reference_backend(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fixture = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/cli-regression/claude.json").read_text())
    sid = fixture["sessionId"]
    records = fixture["phases"]["initial"][0]["records"] + fixture["phases"]["response"][0]["records"]
    (tmp_path / "stage-1.bin").write_bytes("".join(json.dumps(r) + "\n" for r in records).encode())
    command[-1] += " --session-id " + sid
    async with backend, backend.connect() as ws:
        created = await backend.create(ws, pane_id="native-observation", agent_key="claude", command=command,
                                       cwd=str(workspace), metadata={"explicit_session_id": sid})
        tid = created["terminal_session_id"]
        await backend.expect_output(ws, tid, "CLAUDE_REFERENCE_READY")
        await backend.request(ws, "terminal.input", {"terminal_session_id": tid, "data": "stage 1\r"})
        await backend.expect_output(ws, tid, "CLAUDE_STAGE 1")
        await live_totals(backend, ws, workspace, sid, [12, 3])
        async with asyncio.timeout(15):
            while not any(e.get("type") == "agent.activity" and e["payload"].get("event_type") == "turn_complete"
                          for e in backend.events):
                await backend.receive(ws)
        completions = [e["payload"] for e in backend.events if e.get("type") == "agent.activity"
                       and e["payload"].get("event_type") == "turn_complete"]
        assert completions[0]["text"] == fixture["expected"]["reply"]
        assert completions[0]["pane_id"] == "native-observation"
        assert completions[0]["workspace_path"] == str(workspace)
        observed = [r for r in receipts(tmp_path) if r.get("event") == "observed"]
        assert observed and all(r["parser"] == "rust-claude-jsonl-v1" for r in observed)
        assert any(r["session_id"] == tid and r["pane_id"] == "native-observation" and r["native_session_id"] == sid
                   and r["session_generation"] for r in observed)
        await backend.request(ws, "terminal.kill", {"terminal_session_id": tid, "force": True})


async def test_native_checkpoint_restart_preserves_question_background_partial_and_dedup(tmp_path):
    from agent_team_backend.db import Database
    from agent_team_backend.tokens_store import TokensStore
    from agent_team_backend.log_readers.base import encode_claude_cwd

    workspace = tmp_path / "workspace checkpoint café"
    workspace.mkdir()
    sid = "22222222-2222-4222-8222-222222222222"
    prompt = {"type": "user", "promptId": "prompt-1", "message": {"content": "Implement the fixture"}}
    question = {"type": "assistant", "requestId": "request-1", "message": {
        "id": "msg-1", "model": "fixture-model", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "name": "AskUserQuestion", "input": {}}],
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 2, "cache_creation_input_tokens": 3, "output_tokens": 4}}}
    background = {"type": "user", "message": {"content": [{"type": "tool_result"}]},
                  "toolUseResult": {"backgroundTaskId": "bg-1"}}
    completion = {"type": "assistant", "requestId": "request-2", "version": "99.0.0-regression", "message": {
        "id": "msg-2", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Reference completed café 世界"}],
        "usage": {"input_tokens": 7, "output_tokens": 5}}}
    full = json.dumps(completion).encode()
    split = len(full) // 2
    stage1 = b"".join((json.dumps(r) + "\n").encode() for r in [prompt, question, question, background]) + b"corrupt complete record\n"
    (tmp_path / "stage-1.bin").write_bytes(stage1 + full[:split])
    notification = {"type": "queue-operation", "operation": "enqueue", "content":
                    "<task-notification><task-id>bg-1</task-id><task-id>bg-2</task-id><status>completed</status></task-notification>"}
    monitor = {**notification, "content": "<task-notification><task-id>bg-1</task-id>monitor</task-notification>"}
    stop = {"type": "assistant", "message": {"stop_reason": "tool_use", "content":
            [{"type": "tool_use", "name": "TaskStop", "input": {"task_id": "bg-3"}}]}}
    duplicate = {**completion, "message": {**completion["message"], "stop_reason": "tool_use"}}
    (tmp_path / "stage-2.bin").write_bytes(full[split:] + b"\n" + b"".join(
        (json.dumps(r) + "\n").encode() for r in [monitor, notification, stop, duplicate]))
    path = tmp_path / "home/.claude/projects" / encode_claude_cwd(str(workspace)) / f"{sid}.jsonl"

    async def run(stage):
        backend, command = reference_backend(tmp_path)
        command[-1] += (" --session-id " if stage == 1 else " --resume ") + sid
        async with backend, backend.connect() as ws:
            if stage == 2:
                # Unsupported session preflight is deliberately permissive in the
                # current backend; it must not be redefined by this migration.
                for agent, expected in [("claude", True), ("mcode", True)]:
                    answer = await backend.request(ws, "agent.session_exists", {
                        "agent": agent, "workspace_path": str(workspace), "session_id": sid})
                    assert answer["payload"]["exists"] is expected
                before = (await backend.request(ws, "tokens.snapshot", {"workspace_path": str(workspace)}))["payload"]
                assert [before["global"]["all_time"][k] for k in ("input", "output")] == [15, 4]
            created = await backend.create(ws, pane_id="checkpoint-reference", agent_key="claude", command=command,
                                           cwd=str(workspace), metadata={"explicit_session_id": sid})
            tid = created["terminal_session_id"]
            await backend.expect_output(ws, tid, "CLAUDE_REFERENCE_READY")
            if stage == 2:
                args = json.loads(sorted(tmp_path.glob("claude-child-*.json"), key=lambda p: p.stat().st_mtime)[-1].read_text())["argv"]
                assert args[args.index("--resume") + 1] == sid
                await live_totals(backend, ws, workspace, sid, [15, 4])
                assert not [e for e in backend.events if e.get("type") == "agent.activity"], "restart replayed activity"
            await backend.request(ws, "terminal.input", {"terminal_session_id": tid, "data": f"stage {stage}\r"})
            await backend.expect_output(ws, tid, f"CLAUDE_STAGE {stage}")
            await live_totals(backend, ws, workspace, sid, [15, 4] if stage == 1 else [22, 9])
            target = "assistant:question" if stage == 1 else "background:end"
            async with asyncio.timeout(15):
                while not any(e.get("type") == "agent.activity" and e["payload"].get("detail") == target for e in backend.events):
                    await backend.receive(ws)
            activity = [e["payload"] for e in backend.events if e.get("type") == "agent.activity"]
            if stage == 1:
                assert not [e for e in activity if e["event_type"] == "turn_complete"]
                assert [(e["detail"], e["text"]) for e in activity if e["detail"].startswith("background:")] == [("background:start", "bg-1")]
            else:
                assert [e["text"] for e in activity if e["event_type"] == "turn_complete"] == ["Reference completed café 世界"]
                assert not [e for e in activity if e["detail"] == "assistant:question"]
                assert [(e["detail"], e["text"]) for e in activity if e["detail"].startswith("background:")] == [
                    ("background:end", "bg-1\nbg-2"), ("background:end", "bg-3")]
                cut = (await backend.request(ws, "tokens.turns", {"pane_id": "checkpoint-reference", "include_calls": True}))["payload"]
                assert cut["totals"] == {"input": 17, "cache_read": 2, "cache_creation": 3, "output": 9, "total": 31, "calls": 2}
            assert all(e["pane_id"] == "checkpoint-reference" and e["workspace_path"] == str(workspace) for e in activity)
            await backend.request(ws, "terminal.kill", {"terminal_session_id": tid, "force": True})
        db = Database(tmp_path / "data/navide.db")
        store = TokensStore(db=db)
        cursor = store.get_ingestion_checkpoint(str(path), None)
        activity_cursor = store.get_ingestion_checkpoint(str(path), "@activity")
        assert cursor["kind"] == "jsonl"
        assert cursor["offset"] == (len(stage1) if stage == 1 else path.stat().st_size)
        assert len(cursor["recent_keys"]) == stage
        assert activity_cursor["keys"] == [f"act_hw::{5 if stage == 1 else 10}"]
        totals = store.snapshot(str(workspace))["global"]["all_time"]
        assert [totals[k] for k in ("input", "output")] == ([15, 4] if stage == 1 else [22, 9])
        db.close()
        return tid

    first = await run(1)
    second = await run(2)
    assert first != second
    observed = [r for r in receipts(tmp_path) if r.get("event") == "observed"]
    assert {r["session_id"] for r in observed if r["session_id"]} == {first, second}
    assert {r["mode"] for r in observed} >= {"usage", "activity", "turns"}
    assert len({r["runtime_generation"] for r in observed}) == 2


async def test_native_claude_isolated_login_switch_and_refusal_preserve_python_credentials(tmp_path):
    backend, command = reference_backend(tmp_path)
    backend.env["NAVIDE_REGRESSION_KEYCHAIN_FIXTURE"] = "1"
    home = tmp_path / "home"
    original = json.dumps({"claudeAiOauth": {"accessToken": "dummy-original-A", "refreshToken": "dummy-refresh-A", "expiresAt": 9999999999999}})
    (home / ".claude/.credentials.json").write_text(original)
    (home / ".claude.json").write_text(json.dumps({"fixture_preserved_setting": "café"}))
    async with backend, backend.connect() as ws:
        profile = (await backend.request(ws, "cli_profiles.create", {"agent_key": "claude", "name": "Fixture B"}))["payload"]["profile"]
        slot = profile["id"]
        command[-1] += " --model must-not-reach-login --settings '{}'"
        created = await backend.create(ws, pane_id="credential-login", agent_key="claude", command=command,
                                       is_login=True, login_profile_id=slot)
        tid = created["terminal_session_id"]
        await backend.expect_output(ws, tid, "CLAUDE_REFERENCE_READY")
        child = json.loads(next(tmp_path.glob("claude-child-*.json")).read_text())
        assert child["argv"] == ["auth", "login"]
        assert Path(child["config_home"]) == home / ".navide/cli-profiles/claude" / slot / "login-home"
        assert (home / ".claude/.credentials.json").read_text() == original
        push = await backend.request(ws, "agent_msg.push", {"pane_id": "credential-login", "text": "must not send"})
        assert push["payload"]["reason"] == "no-channel"
        refused_live_login = await backend.request(ws, "cli_profiles.set_default", {
            "agent_key": "claude", "profile_id": slot}, ok=False)
        assert refused_live_login["error"]["code"] == "LOGIN_IN_PROGRESS"
        assert len(list(tmp_path.glob("claude-child-*.json"))) == 1
        await backend.request(ws, "terminal.input", {"terminal_session_id": tid, "data": "login complete\r"})
        await backend.expect_output(ws, tid, "CLAUDE_LOGIN_WRITTEN")
        await backend.request(ws, "terminal.kill", {"terminal_session_id": tid, "force": True})
        # set_default uses the actual switch lock, harvest, vault, profile DB
        # and preservation transaction; only the OS Keychain is a fixture.
        switched = await backend.request(ws, "cli_profiles.set_default", {"agent_key": "claude", "profile_id": slot})
        assert switched["ok"]
        store = json.loads((tmp_path / "fixture-keychain.json").read_text())
        assert json.loads(store["Claude Code-credentials"])["claudeAiOauth"]["accessToken"] == "dummy-login-B"
        assert any(json.loads(value).get("claudeAiOauth", {}).get("accessToken") == "dummy-original-A"
                   for name, value in store.items() if name.startswith("Navide CLI account"))
        before = (tmp_path / "fixture-keychain.json").read_bytes()
        refused = await backend.request(ws, "cli_profiles.set_default", {"agent_key": "claude", "profile_id": "missing-profile"}, ok=False)
        assert refused["error"]["code"] == "BAD_REQUEST"
        assert (tmp_path / "fixture-keychain.json").read_bytes() == before
        await backend.request(ws, "cli_profiles.set_default", {"agent_key": "claude", "profile_id": None})
        restored = json.loads((tmp_path / "fixture-keychain.json").read_text())
        assert json.loads(restored["Claude Code-credentials"])["claudeAiOauth"]["accessToken"] == "dummy-original-A"
        assert json.loads((home / ".claude.json").read_text())["fixture_preserved_setting"] == "café"
        assert any(r.get("event") == "cleaned" and r["session_id"] == tid and r["cleanup_complete"] for r in receipts(tmp_path))


async def test_native_claude_interrupt_rewake_and_session_preflight(tmp_path):
    import httpx
    from agent_team_backend.log_readers.base import encode_claude_cwd
    backend, command = reference_backend(tmp_path)
    sid = "33333333-3333-4333-8333-333333333333"
    command[-1] += " --session-id " + sid
    unrelated = tmp_path / "home/.claude/projects/another-project" / f"{sid}.jsonl"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("{}\n")
    async with backend, backend.connect() as ws:
        for requested, expected in [(sid, True), ("missing-session", False)]:
            checked = await backend.request(ws, "agent.session_exists", {
                "agent": "claude", "workspace_path": str(tmp_path), "session_id": requested})
            assert checked["payload"]["exists"] is expected
        missing_current = tmp_path / "home/.claude/projects" / encode_claude_cwd(str(tmp_path)) / f"{sid}.jsonl"
        assert not missing_current.exists(), "cross-project check accidentally stayed within one project"
        refused = await backend.request(ws, "terminal.create", {"pane_id": "not-migrated", "agent_key": "mcode",
            "command": command, "cwd": str(tmp_path)}, ok=False)
        assert "not yet integrated" in refused["error"]["message"]
        assert not list(tmp_path.glob("claude-child-*.json")), "unsupported runtime silently spawned a fallback"
        created = await backend.create(ws, pane_id="claude-rewake", agent_key="claude", command=command,
                                       metadata={"explicit_session_id": sid})
        tid = created["terminal_session_id"]
        await backend.expect_output(ws, tid, "CLAUDE_REFERENCE_READY")
        await backend.request(ws, "terminal.interrupt", {"terminal_session_id": tid})
        await backend.expect_output(ws, tid, "CLAUDE_INTERRUPTED")
        await backend.request(ws, "terminal.input", {"terminal_session_id": tid, "data": "message café 世界\r"})
        await backend.expect_output(ws, tid, "CLAUDE_INPUT")
        base = backend.url.replace("ws://", "http://").split("/ws?", 1)[0]
        header, token = (tmp_path / "data/hook-auth").read_text().strip().split(": ", 1)
        async with httpx.AsyncClient(base_url=base, timeout=15, trust_env=False) as client:
            denied = await client.post("/hooks/claude/rewake", json={"session_id": sid})
            assert denied.status_code == 403
            waiting = asyncio.create_task(client.post("/hooks/claude/rewake", headers={header: token}, json={"session_id": sid}))
            try:
                async with asyncio.timeout(10):
                    while True:
                        event = await backend.receive(ws)
                        if event and event.get("type") == "agent_msg.push_state" and event["payload"].get("ready"):
                            assert event["payload"]["pane_id"] == "claude-rewake"
                            break
                text = "[Navide MSG] native fixture café 世界"
                pushed = await backend.request(ws, "agent_msg.push", {"pane_id": "claude-rewake", "text": text})
                assert pushed["payload"]["ok"]
                answer = await waiting
                assert answer.status_code == 200 and answer.text.endswith("\n" + text)
                assert (await backend.request(ws, "agent_msg.push", {"pane_id": "claude-rewake", "text": "second"}))["payload"]["reason"] == "not-armed"
            finally:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
        await backend.request(ws, "terminal.kill", {"terminal_session_id": tid, "force": True})


async def test_native_activity_error_advances_before_next_poll_completion(tmp_path):
    """Python-WS supplement proves the composed reader applies native error progress."""
    backend, command = reference_backend(tmp_path)
    sid = "44444444-4444-4444-8444-444444444444"
    command[-1] += " --session-id " + sid
    active = {"type": "assistant", "message": {"stop_reason": "tool_use", "content": []}}
    complete = {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [
        {"type": "text", "text": "After exceptional record"}]}}
    (tmp_path / "stage-1.bin").write_text(json.dumps(active) + "\nnull\n" + json.dumps(complete) + "\n")
    async with backend, backend.connect() as ws:
        created = await backend.create(ws, pane_id="exception-progress", agent_key="claude", command=command,
                                       metadata={"explicit_session_id": sid})
        tid = created["terminal_session_id"]
        await backend.expect_output(ws, tid, "CLAUDE_REFERENCE_READY")
        await backend.request(ws, "terminal.input", {"terminal_session_id": tid, "data": "stage 1\r"})
        async with asyncio.timeout(6):
            while not any(event.get("type") == "agent.activity" and event["payload"].get("event_type") == "turn_complete"
                          for event in backend.events):
                await backend.receive(ws)
        activity = [event["payload"] for event in backend.events if event.get("type") == "agent.activity"]
        assert [event["event_type"] for event in activity] == ["agent_active", "turn_complete"]
        assert [event["text"] for event in activity if event["event_type"] == "turn_complete"] == ["After exceptional record"]
        errors = [row for row in receipts(tmp_path) if row.get("event") == "observed" and row.get("error")]
        assert len(errors) == 1 and errors[0]["seen"] == ["act_hw::2"]
        assert errors[0]["event_count"] == 0 and errors[0]["session_id"] == tid
        await backend.request(ws, "terminal.kill", {"terminal_session_id": tid, "force": True})


def receipts(root: Path) -> list[dict]:
    return [json.loads(line) for line in (root / "runtime-receipts.jsonl").read_text().splitlines()]


async def test_claude_child_receives_wrapped_launch_and_graceful_force_kill(tmp_path):
    backend, command = reference_backend(tmp_path)
    workspace = tmp_path / "workspace café 世界 ' quote"
    workspace.mkdir()
    settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo '世界' \\\" ; | &"}]}]}}
    sid = "11111111-1111-4111-8111-111111111111"
    command[-1] += " --model fixture-model --effort high --session-id " + sid
    command[-1] += " --settings " + shlex.quote(json.dumps(settings))
    async with backend, backend.connect() as ws:
        created = await backend.create(ws, pane_id="reference-launch", agent_key="claude",
                                       command=command, cwd=str(workspace),
                                       env={"REFERENCE_MARKER": "fixture env café ; | &"},
                                       metadata={"explicit_session_id": sid})
        tid = created["terminal_session_id"]
        await backend.expect_output(ws, tid, "CLAUDE_REFERENCE_READY")
        child = json.loads(next(tmp_path.glob("claude-child-*.json")).read_text())
        args = child["argv"]
        assert args[args.index("--model") + 1] == "fixture-model"
        assert args[args.index("--effort") + 1] == "high"
        assert args[args.index("--session-id") + 1] == sid
        assert json.loads(args[args.index("--settings") + 1]) == settings
        assert child["cwd"] == str(workspace)
        assert child["home"] == str(tmp_path / "home")
        assert child["shell_rc"] == "login"
        assert child["marker"] == "fixture env café ; | &"
        assert child["isatty"] and child["ctty"]
        await backend.request(ws, "terminal.interrupt", {"terminal_session_id": tid})
        await backend.expect_output(ws, tid, "CLAUDE_INTERRUPTED")
        await backend.request(ws, "terminal.input", {"terminal_session_id": tid, "data": "multiline café\r"})
        await backend.expect_output(ws, tid, "CLAUDE_INPUT")
        await backend.request(ws, "terminal.kill", {"terminal_session_id": tid, "force": True})
        assert json.loads((tmp_path / f"claude-stopped-{child['pid']}.json").read_text()) == {
            "signal": "SIGTERM", "stdout_open": True,
        }
        native = receipts(tmp_path)
        ready = [r for r in native if r.get("event") == "ready"]
        assert len(ready) == 1 and ready[0]["executableSha256"]
        owned = [r for r in native if r.get("event") == "owned"]
        clean = [r for r in native if r.get("event") == "cleaned"]
        assert len(owned) == len(clean) == 1
        assert clean[0]["cleanup_complete"] and clean[0]["root_reaped"]
        assert clean[0]["survivors"] == clean[0]["unverifiable"] == []
        assert owned[0]["runtime_generation"] == ready[0]["runtime_generation"]
        assert owned[0]["pane_id"] == "reference-launch"
        assert owned[0]["workspace_path"] == str(workspace)
