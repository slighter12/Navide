"""Supplemental real native-protocol proofs; production WS is tested separately."""
import base64
import json
import gzip
import hashlib
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

import pytest
import psutil

from tests.cli_regression.support.cli_shim import base_python_executable
from tests.cli_regression.support.isolation import isolated_environment


class NativePeer:
    def __init__(self, root):
        self.root = root
        self.binary = Path(__file__).resolve().parents[2] / "native/navide-cli-runtime/target/release/navide-cli-runtime"
        if not hasattr(os, "tcgetpgrp") or not self.binary.is_file():
            pytest.skip("requires an explicitly built native POSIX runtime")
        self.generation = uuid.uuid4().hex
        self.number = 0
        self.events = []
        self.responses = {}
        self.output = {}
        self.queue = queue.Queue()
        self.read_enabled = threading.Event()
        self.read_enabled.set()
        self.process = subprocess.Popen([str(self.binary), "--protocol", "1", "--runtime-generation", self.generation,
                                         "--owner-pid", str(os.getpid())], env=isolated_environment(root),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        ready = self.queue.get(timeout=5)
        assert ready["event"] == "ready" and ready["pid"] == self.process.pid
        process = psutil.Process(self.process.pid)
        assert Path(process.exe()).resolve() == self.binary.resolve()
        self.identity = {**ready, "created": process.create_time(), "executable": process.exe(),
                         "executableSha256": hashlib.sha256(self.binary.read_bytes()).hexdigest()}
        (self.root / "native-identity.json").write_text(json.dumps(self.identity, indent=2))

    def _read(self):
        for line in self.process.stdout:
            self.read_enabled.wait()
            self.queue.put(json.loads(line))

    def send(self, op, args=None, sid=None, generation=None):
        self.number += 1
        request = {"id": f"r{self.number}", "runtime_generation": self.generation, "op": op, "args": args or {}}
        if sid:
            request.update(session_id=sid, session_generation=generation)
        self.process.stdin.write(json.dumps(request).encode() + b"\n")
        self.process.stdin.flush()
        return request["id"]

    def receive(self, timeout=15):
        record = self.queue.get(timeout=timeout)
        if "id" in record:
            self.responses[record["id"]] = record
        else:
            self.events.append(record)
            if record["event"] == "output":
                sid = record["session_id"]
                self.output[sid] = self.output.get(sid, b"") + base64.b64decode(record["data_b64"])
        return record

    def result(self, request):
        while request not in self.responses:
            self.receive()
        response = self.responses.pop(request)
        assert response["ok"], response
        return response["result"]

    def create(self, argv):
        sid, generation = str(uuid.uuid4()), uuid.uuid4().hex
        args = {"session_id": sid, "session_generation": generation, "pane_id": sid,
                "workspace_path": str(self.root), "argv": argv, "cwd": str(self.root),
                "env": isolated_environment(self.root), "cols": 100, "rows": 30}
        self.result(self.send("create", args))
        self.result(self.send("activate", sid=sid, generation=generation))
        return sid, generation

    def close(self):
        for sid, data in self.output.items():
            (self.root / f"native-output-{sid}.bin").write_bytes(data)
        event_bytes = json.dumps(self.events).encode()
        (self.root / "native-events.json.gz").write_bytes(gzip.compress(event_bytes, mtime=0))
        (self.root / "native-identity.json").write_text(json.dumps(self.identity, indent=2))
        self.read_enabled.set()
        try:
            self.result(self.send("shutdown"))
            assert self.process.wait(timeout=2) == 0
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
            self.reader.join(timeout=2)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires a paced POSIX filesystem fixture")
def test_native_paced_historical_observation_does_not_block_shell_admission(tmp_path):
    """Correctness proof with a held external history stream, not a timing benchmark."""
    native = NativePeer(tmp_path)
    released = threading.Event()
    opened = threading.Event()
    writer = None
    try:
        script = tmp_path / "native_shell_peer.py"
        shutil.copyfile(Path(__file__).parent / "cli_regression/support/native_shell_peer.py", script)
        sid, generation = native.create([base_python_executable(), "-u", str(script)])
        while b"NATIVE_READY " not in native.output.get(sid, b""):
            native.receive()
        history = tmp_path / "history.jsonl"
        os.mkfifo(history)

        def paced_history():
            if released.wait(5):
                with history.open("wb"):
                    opened.set()  # Release the real native File::open rendezvous.

        writer = threading.Thread(target=paced_history, daemon=True)
        writer.start()
        observation = native.send("observe", {"vendor": "claude", "path": str(history), "mode": "activity", "seen": []})
        controls = [native.send("write", {"data_b64": base64.b64encode(b"responsive\r").decode()}, sid, generation),
                    native.send("interrupt", {"data_b64": "Aw=="}, sid, generation),
                    native.send("kill", {"force": True}, sid, generation)]
        deadline = time.monotonic() + 1
        try:
            for request in controls:
                while request not in native.responses:
                    native.receive(timeout=max(0.001, deadline - time.monotonic()))
                assert native.responses.pop(request)["ok"]
        except queue.Empty:
            pytest.fail("unrelated shell control admission waited for historical I/O release")
        assert observation not in native.responses and not released.is_set() and not opened.is_set()
        released.set()
        writer.join(timeout=1)
        assert not writer.is_alive() and opened.is_set()
        while observation not in native.responses:
            native.receive()
        failed = native.responses.pop(observation)
        # The paced open completes, then a FIFO is (correctly) not seekable.
        assert not failed["ok"] and failed["error"]["code"] == "IO_FAILED"
        assert "seek" in failed["error"]["message"].lower()
        (tmp_path / "held-history.json").write_text(json.dumps({"request_id": observation, "path": str(history),
            "process_bound": False, "controls_admitted_before_release": controls, "response": failed}, indent=2))
        history.unlink()
        history.write_text("")
        observation = native.send("observe", {"vendor": "claude", "path": str(history), "mode": "activity", "seen": []})
        result = native.result(observation)
        parts = [row for row in native.events if row.get("observation_id") == observation]
        assert result["observation_parts"] == len(parts)
        observed = json.loads(b"".join(base64.b64decode(row["data_b64"]) for row in parts))
        assert observed["parser"] == "rust-claude-jsonl-v1" and observed["path"] == str(history)
        assert all(observed["ownership"][key] is None for key in (
            "session_id", "session_generation", "pane_id", "workspace_path"))
        assert all(row["runtime_generation"] == native.generation for row in parts)
        while not any(row.get("event") == "cleaned" and row["session_id"] == sid for row in native.events):
            native.receive()
        cleaned = next(row for row in native.events if row.get("event") == "cleaned" and row["session_id"] == sid)
        assert cleaned["cleanup_complete"] and cleaned["root_reaped"]
        assert cleaned["survivors"] == cleaned["unverifiable"] == []
    finally:
        released.set()
        if writer:
            writer.join(timeout=1)
        native.close()


def test_native_held_control_fence_rejects_overlap_without_fifo_deadlock(tmp_path):
    peer = NativePeer(tmp_path)
    try:
        sid, generation = peer.create([base_python_executable(), "-c", "import time; time.sleep(60)"])
        first = peer.result(peer.send("resize", {"cols": 110, "rows": 40}, sid, generation))
        second = peer.send("resize", {"cols": 120, "rows": 40}, sid, generation)
        while second not in peer.responses:
            peer.receive()
        rejected = peer.responses.pop(second)
        assert rejected["ok"] is False, rejected
        assert "CONTROL_FENCE_HELD" in rejected["error"]["message"]
        # A release behind rejected B remains serviceable on the same FIFO.
        peer.result(peer.send("release", {"barrier_id": first["barrier_id"]}, sid, generation))
        third = peer.result(peer.send("resize", {"cols": 130, "rows": 40}, sid, generation))
        peer.result(peer.send("release", {"barrier_id": "not-owner"}, sid, generation))
        fourth = peer.send("resize", {"cols": 140, "rows": 40}, sid, generation)
        while fourth not in peer.responses:
            peer.receive()
        assert peer.responses.pop(fourth)["ok"] is False
        peer.result(peer.send("release", {"barrier_id": third["barrier_id"]}, sid, generation))
    finally:
        peer.close()


def test_native_admitted_writes_and_controls_remain_fifo(tmp_path):
    native = NativePeer(tmp_path)
    try:
        script = tmp_path / "native_shell_peer.py"
        shutil.copyfile(Path(__file__).parent / "cli_regression/support/native_shell_peer.py", script)
        sid, generation = native.create([base_python_executable(), "-u", str(script)])
        while b"NATIVE_READY " not in native.output.get(sid, b""):
            native.receive()
        requests = []
        for index in range(100):
            requests.append(native.send("write", {"data_b64": base64.b64encode(f"{index}\r".encode()).decode()}, sid, generation))
            if index == 49:
                resize = native.send("resize", {"cols": 120, "rows": 40, "redraw": False}, sid, generation)
                requests.append(resize)
                requests.append(native.send("release", {"barrier_id": resize}, sid, generation))
        for request in requests:
            native.result(request)
        while b'"text": "99"' not in native.output[sid]:
            native.receive()
        observed = re.findall(rb'ACK \{"pid": \d+, "text": "(\d+)"\}', native.output[sid])
        assert observed == [str(index).encode() for index in range(100)]
        native.result(native.send("kill", {"force": True}, sid, generation))
    finally:
        native.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS native WINCH/PTY fixture")
def test_native_producer_completes_stdout_after_winch(tmp_path):
    native = NativePeer(tmp_path)
    expected = b"WINCH_FRAME " + b"x" * 65536 + b"\r\nWINCH_DONE\r\n"
    observed = b""
    try:
        script = tmp_path / "native_shell_peer.py"
        shutil.copyfile(Path(__file__).parent / "cli_regression/support/native_shell_peer.py", script)
        sid, generation = native.create(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)(deny network*)",
                                         base_python_executable(), "-u", str(script)])
        while not native.output.get(sid, b"").endswith(b"\r\n"):
            native.receive()
        start = len(native.output[sid])
        native.result(native.send("flow", {"paused": True}, sid, generation))
        native.result(native.send("write", {"data_b64": base64.b64encode(b"winch-short-write\r").decode()}, sid, generation))
        sent = tmp_path / "winch-sent.json"
        deadline = time.monotonic() + 0.5
        while not sent.exists():
            assert time.monotonic() < deadline, "producer did not signal its held stdout write"
            time.sleep(0.001)
        signal_receipt = json.loads(sent.read_text())
        owned = next(row for row in native.events if row.get("event") == "owned" and row["session_id"] == sid)
        assert signal_receipt == {"pid": owned["root"]["pid"], "signal": "SIGWINCH"}
        native.result(native.send("flow", {"paused": False}, sid, generation))
        while not native.output[sid].endswith(b"WINCH_DONE\r\n"):
            native.receive()
        observed = native.output[sid][start:]
        offset = 0
        for sequence, row in enumerate((e for e in native.events if e.get("event") == "output"), start=1):
            assert row["seq"] == sequence and row["offset"] == offset and row["dropped_before"] == 0
            assert row["runtime_generation"] == native.generation and row["session_generation"] == generation
            offset += len(base64.b64decode(row["data_b64"]))
        assert observed == expected
    finally:
        native.close()
        owned = [row for row in native.events if row.get("event") == "owned"]
        cleaned = [row for row in native.events if row.get("event") == "cleaned"]
        (tmp_path / "winch-proof.json").write_text(json.dumps({"identity": native.identity, "owned": owned, "cleaned": cleaned,
            "expected_bytes": len(expected), "observed_bytes": len(observed),
            "expected_sha256": hashlib.sha256(expected).hexdigest(), "observed_sha256": hashlib.sha256(observed).hexdigest(),
            "sidecar_exit": native.process.returncode, "signal_origin": "external producer self-WINCH",
            "rescue": native.process.returncode != 0}, indent=2))
        assert {row["session_id"] for row in owned} == {row["session_id"] for row in cleaned}
        assert all(row["cleanup_complete"] and row["root_reaped"] and not row["survivors"] and not row["unverifiable"] for row in cleaned)
        assert native.process.returncode == 0


def test_native_bookkeeping_is_bounded_after_settled_requests_and_sessions(tmp_path):
    native = NativePeer(tmp_path)
    try:
        for _ in range(1000):
            result = native.result(native.send("ping"))
        assert result["bookkeeping"]["last_request"] == 1000
        for _ in range(260):
            sid, _generation = native.create(["/usr/bin/true"])
            while not any(event["event"] == "cleaned" and event["session_id"] == sid for event in native.events):
                native.receive()
        result = native.result(native.send("ping"))["bookkeeping"]
        assert result["live_sessions"] == 0
        assert result["settled_sessions"] <= 256
        assert result["session_workers"] == 0
    finally:
        native.close()


def test_native_partial_input_reports_pending_and_unblocks_without_replay(tmp_path):
    native = NativePeer(tmp_path)
    try:
        script = tmp_path / "native_shell_peer.py"
        shutil.copyfile(Path(__file__).parent / "cli_regression/support/native_shell_peer.py", script)
        sid, generation = native.create([base_python_executable(), "-u", str(script)])
        while b"NATIVE_READY " not in native.output.get(sid, b""):
            native.receive()
        native.result(native.send("write", {"data_b64": base64.b64encode(b"block-input\r").decode()}, sid, generation))
        while b"INPUT_STOPPED" not in native.output[sid]:
            native.receive()
        payload = b"x" * 16384
        result = native.result(native.send("write", {"data_b64": base64.b64encode(payload).decode()}, sid, generation))
        assert 0 < result["pending"] <= len(payload)
        assert native.result(native.send("ping"))["pong"]
        expected = b"INPUT_DRAINED " + hashlib.sha256(payload).hexdigest().encode()
        while expected not in native.output[sid] or not any(event["event"] == "input_unblocked" for event in native.events):
            native.receive()
        assert any(event["event"] == "input_blocked" and event["since_ms"] >= 500 for event in native.events)
        unblocked = next(event for event in native.events if event["event"] == "input_unblocked")
        assert 0 < unblocked["drained"] <= len(payload)
        native.result(native.send("kill", {"force": True}, sid, generation))
    finally:
        native.close()


@pytest.mark.parametrize("pause_before_admission", [True, False])
def test_native_saturated_publication_is_bounded_and_exit_accounts_drops(tmp_path, pause_before_admission):
    native = NativePeer(tmp_path)
    primary_failure = None
    child = None
    try:
        script = tmp_path / "native_shell_peer.py"
        shutil.copyfile(Path(__file__).parent / "cli_regression/support/native_shell_peer.py", script)
        sid, generation = native.create([base_python_executable(), "-u", str(script)])
        while b"NATIVE_READY " not in native.output.get(sid, b""):
            native.receive()
        if pause_before_admission:
            native.read_enabled.clear()
        write = native.send("write", {"data_b64": base64.b64encode(b"flood-exit\r").decode()}, sid, generation)
        if not pause_before_admission:
            native.result(write)
            native.read_enabled.clear()
        observed = json.loads(native.output[sid].split(b"NATIVE_READY ")[1].splitlines()[0])
        child = psutil.Process(observed["pid"])
        _gone, alive = psutil.wait_procs([child], timeout=15)
        assert not alive, "native flood/reap deadline"
        ping = native.send("ping")
        native.read_enabled.set()
        if pause_before_admission:
            native.result(write)
        bounds = native.result(ping)["bookkeeping"]
        assert bounds["max_session_output_bytes"] <= 5 * 1024 * 1024
        assert bounds["queued_output_bytes"] <= 5 * 1024 * 1024
        while not any(event["event"] == "exit" for event in native.events):
            native.receive()
        exit_record = next(event for event in native.events if event["event"] == "exit")
        while max(event.get("seq", 0) for event in native.events) < exit_record["last_output_seq"]:
            native.receive()
        assert sum(event.get("dropped_before", 0) for event in native.events) > 0
        assert exit_record["exit_code"] == 7
    except BaseException as error:
        primary_failure = error
        # Failure-only observations do not perturb the trigger or resume the reader.
        proof = {"primary": repr(error), "native": native.identity}
        if child is not None:
            try:
                proof["child"] = {"pid": child.pid, "created": child.create_time(), "status": child.status()}
            except psutil.NoSuchProcess:
                proof["child"] = {"pid": child.pid, "gone": True}
        (tmp_path / "primary-failure.json").write_text(json.dumps(proof, indent=2))
        raise
    finally:
        cleanup_failure = None
        try:
            native.close()
        except BaseException as error:
            cleanup_failure = error
            if primary_failure is None:
                raise
            primary_failure.add_note(f"Native cleanup also failed: {error!r}")
        finally:
            if primary_failure is not None:
                (tmp_path / "failure-after-cleanup.json").write_text(json.dumps({
                    "primary": repr(primary_failure), "cleanup": repr(cleanup_failure),
                    "responses": native.responses,
                    "control_events": [event for event in native.events if event["event"] != "output"],
                }, indent=2))


def test_native_shutdown_reaps_writer_blocked_by_held_publication(tmp_path):
    """Actual native cleanup must reap a writer even while its PTY write is blocked."""
    native = NativePeer(tmp_path)
    try:
        source = (Path(__file__).parent / "cli_regression/support/native_shell_peer.py").read_text()
        script = tmp_path / "native_shell_peer.py"
        script.write_text(source.replace('elif line == "flood-exit":',
                                         'elif line == "flood-exit":\n            Path("flood-started").touch()'))
        sid, generation = native.create([base_python_executable(), "-u", str(script)])
        while b"NATIVE_READY " not in native.output.get(sid, b""):
            native.receive()
        native.result(native.send("resize", {"cols": 100, "rows": 30}, sid, generation))
        native.result(native.send("write", {"data_b64": base64.b64encode(b"flood-exit\r").decode()}, sid, generation))
        deadline = time.monotonic() + 5
        while not (tmp_path / "flood-started").exists():
            assert time.monotonic() < deadline, "owned flood command was not received"
            time.sleep(0.005)
        native.close()
        cleaned = next(event for event in native.events if event["event"] == "cleaned")
        assert cleaned["cleanup_complete"] and cleaned["root_reaped"]
        assert not cleaned["survivors"] and not cleaned["unverifiable"]
    finally:
        if native.process.poll() is None:
            native.close()
        (tmp_path / "cleanup-control-events.json").write_text(json.dumps(
            [event for event in native.events if event["event"] != "output"], indent=2))


@pytest.mark.parametrize("natural", [False, True])
def test_native_reaps_owned_and_detached_tree_without_touching_unrelated_peer(tmp_path, natural):
    native = NativePeer(tmp_path)
    unrelated = None
    children = []
    try:
        unrelated = subprocess.Popen([base_python_executable(), "-c", "import time; time.sleep(120)"],
                                     env=isolated_environment(tmp_path))
        script = tmp_path / "native_shell_peer.py"
        shutil.copyfile(Path(__file__).parent / "cli_regression/support/native_shell_peer.py", script)
        sid, generation = native.create([base_python_executable(), "-u", str(script)])
        while b"NATIVE_READY " not in native.output.get(sid, b""):
            native.receive()
        native.result(native.send("write", {"data_b64": base64.b64encode(b"spawn-tree\r").decode()}, sid, generation))
        while b"TREE {" not in native.output[sid]:
            native.receive()
        tree = json.loads(native.output[sid].split(b"TREE ")[1].splitlines()[0])
        children = [psutil.Process(pid) for pid in tree["pids"]]
        proof = [{"pid": child.pid, "created": child.create_time(), "parent": child.ppid()} for child in children]
        (tmp_path / "tree-identities.json").write_text(json.dumps(proof, indent=2))
        if natural:
            while not any(event["event"] == "ownership" and len(event.get("descendants", [])) >= 2 for event in native.events):
                native.receive()
            native.result(native.send("write", {"data_b64": base64.b64encode(b"exit\r").decode()}, sid, generation))
        else:
            native.result(native.send("kill", {"force": True}, sid, generation))
        while not any(event["event"] == "cleaned" for event in native.events):
            native.receive()
        cleanup = next(event for event in native.events if event["event"] == "cleaned")
        assert cleanup["cleanup_complete"] and cleanup["root_reaped"]
        assert not cleanup["survivors"] and not cleanup["unverifiable"]
        assert all(not child.is_running() or child.status() == psutil.STATUS_ZOMBIE for child in children)
        assert unrelated.poll() is None
    finally:
        try:
            native.close()
        finally:
            for child in children:
                if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                    child.kill()
            if unrelated is not None:
                unrelated.kill()
                unrelated.wait(timeout=5)
