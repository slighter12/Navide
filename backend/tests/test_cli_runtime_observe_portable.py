"""The sidecar's native observation on every operating system, through the production client (`RustTerminalService`).

The observation core runs on Windows as on POSIX. These cases drive the real sidecar binary below the Python readers with
the production client (the same class and the same `observe` path the backend uses, so they run unchanged on POSIX and Windows)
and pin what the two platforms must agree on:

- the roster: a vendor with a declared observer is answered, one without (mcode) is refused as not integrated;
- the parity of every vendor's own synthetic catalog fixture: the core's answer over the fixture's files carries the vendor's parser
  and the modes its declaration lists, and the fixture's tokens are the sum of the usage events it delivers;
- the lifecycle of a log: an append is read once, a saved checkpoint is recognised by a *different* sidecar process (its anchor and
  the file identity are the file system's, so the next generation resumes and replays nothing), a rewritten or shorter log is a
  rotation that restarts at 0;
- the bounds of the source open: a directory, and a link, are never read; a missing log is refused as missing (the reader decides
  whether a missing file is empty, before the core is asked);
- the paths real Windows users have: spaces, non-ASCII and non-BMP characters in the directory, for a log and for a SQLite store;
- a log a writer keeps appending to while it is observed (the CLI's own state of affairs) is read without hurting either side.

The Windows arm of the source open (reparse points, device names, sharing violations) is pinned by the Rust tests of
`observe/os.rs`, which `cargo test` runs on Windows; the cases here prove the same guarantees over the wire.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from agent_team_backend.cli_runtime import RustTerminalService
from agent_team_backend.cli_runtime_observation import request_of
from agent_team_backend.cli_vendors import _vendor_facts as facts
from agent_team_backend.cli_vendors.registry import VENDORS
from tests.cli_regression.support.vendor_data import VendorData

pytestmark = [pytest.mark.asyncio, pytest.mark.cli_regression]

OBSERVED = sorted(key for key, spec in VENDORS.items() if spec.make_log_reader)
PRESERVED = sorted(key for key, spec in VENDORS.items() if spec.make_log_reader is None)
SESSION = "12345678-1234-4234-8234-123456789abc"
#: A directory name with a space, accents, CJK and a character outside the BMP (a surrogate pair in UTF-16): what a Windows user's
#: profile can hold, and what a URI, a UTF-8 to UTF-16 conversion and a percent encoder must all agree on.
AWKWARD = "ünï cödé 日本語 𝔘nicode & #hash %41"


class Sidecar:
    """One sidecar generation behind the production client; `restart` is a new process with nothing in common but the files."""

    def __init__(self, root: Path, monkeypatch):
        from tests.cli_regression.support.isolation import isolated_environment
        env = isolated_environment(root)
        monkeypatch.setenv("AGENT_TEAM_DATA_DIR", env["AGENT_TEAM_DATA_DIR"])
        self._service: RustTerminalService | None = None

    @property
    def service(self) -> RustTerminalService:
        # Built on first use, inside the running test loop: the service binds the loop it is created in.
        if self._service is None:
            async def sink(_frame):
                return None
            self._service = RustTerminalService(sink)
        return self._service

    async def stop(self) -> None:
        service, self._service = self._service, None
        runtime = None if service is None else service._runtime  # noqa: SLF001
        if runtime is not None and runtime.returncode is None:
            runtime.kill()
            await runtime.wait()

    async def observe(self, vendor: str, path: Path, mode: str, *, checkpoint=None, seen=None, state=None, **extra) -> dict:
        args = request_of(path, mode, checkpoint=checkpoint, seen=seen, state=state, **extra)
        return await self.service.observe(vendor, facts.FACTS[vendor].observer.parser_id, args, match=lambda _sessions: None)

    async def restart(self) -> None:
        await self.stop()


@contextlib.asynccontextmanager
async def sidecar(root: Path, monkeypatch):
    side = Sidecar(root, monkeypatch)
    try:
        yield side
    finally:
        await side.stop()


class Chain:
    """What the Python side keeps between the passes of one read: the checkpoint, the dedup keys and the typed state."""

    def __init__(self, side: Sidecar, vendor: str, path: Path, mode: str = "usage", **extra):
        self.side, self.vendor, self.path, self.mode, self.extra = side, vendor, path, mode, extra
        self.checkpoint: dict = {}
        self.seen: set[str] = set()
        self.state: dict = {}
        self.last: dict = {}

    async def pass_(self) -> list[dict]:
        result = await self.side.observe(self.vendor, self.path, self.mode, checkpoint=self.checkpoint, seen=self.seen,
                                         state=self.state, **self.extra)
        self.last = result
        self.checkpoint = result["checkpoint"]
        self.seen |= set(result["seen"])
        self.state = result.get("state") or {}
        return result["events"]


def _tokens(events: list[dict]) -> list[int]:
    return [sum(event["input_tokens"] for event in events), sum(event["output_tokens"] for event in events)]


def _assistant(number: int, cwd: str = "/w") -> str:
    """One Claude assistant record that carries usage 12 in, 3 out (the catalog fixture's own shape), unique by its ids."""
    return json.dumps({
        "type": "assistant", "uuid": f"a{number}", "sessionId": SESSION, "cwd": cwd, "requestId": f"r{number}",
        "timestamp": "2026-09-29T10:00:00Z",
        "message": {"id": f"msg{number}", "role": "assistant", "model": "fixture", "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": f"reply {number}"}],
                    "usage": {"input_tokens": 10, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 1, "output_tokens": 3}},
    }, ensure_ascii=False) + "\n"


def _log(directory: Path, *numbers: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{SESSION}.jsonl"
    path.write_text("".join(_assistant(number) for number in numbers), encoding="utf-8", newline="\n")
    return path


def _vendor_data(vendor: str, tmp_path: Path, monkeypatch, set_home) -> VendorData:
    root = tmp_path / "data"
    root.mkdir()
    return VendorData(vendor, root, monkeypatch, set_home)


def _mode_for(vendor: str) -> str:
    """A vendor on the core answers the modes its declaration lists; Droid's usage is the replay of its totals (`session_usage`)."""
    modes = {str(mode.value) for mode in facts.FACTS[vendor].observer.core.modes}
    return "usage" if "usage" in modes else "session_usage"


# ------------------------------------------------------------------------------------------------------ roster

@pytest.mark.timeout(120)
async def test_the_sidecar_answers_exactly_the_vendors_with_a_declared_observer(tmp_path, monkeypatch):
    """The roster of the sidecar on this host (its own answer) is the registry's readers: no vendor is left to a per-platform gate."""
    path = tmp_path / "session.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    answered = set()
    async with sidecar(tmp_path, monkeypatch) as side:
        for key in VENDORS:
            if facts.FACTS[key].observer is None:
                with pytest.raises(RuntimeError, match="observer not integrated"):
                    await side.service.observe(key, "none", request_of(path, "usage"), match=lambda _sessions: None)
                continue
            try:
                await side.observe(key, path, _mode_for(key))
                answered.add(key)
            except RuntimeError as error:
                assert "observer not integrated" not in str(error), f"{key} is declared and the sidecar refuses it: {error}"
                answered.add(key)  # refused for the file's content, not for the roster
    assert answered == set(OBSERVED), sorted(answered ^ set(OBSERVED))
    assert PRESERVED == ["mcode"], "the preserved (never observed) set changed: name the new member here and in its declaration"


@pytest.mark.timeout(120)
async def test_mcode_is_refused_as_not_integrated_on_every_platform(tmp_path, monkeypatch):
    path = tmp_path / "session.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    async with sidecar(tmp_path, monkeypatch) as side:
        with pytest.raises(RuntimeError, match="observer not integrated"):
            await side.service.observe("mcode", "none", request_of(path, "usage"), match=lambda _sessions: None)


# -------------------------------------------------------------------------------- parity over each vendor's fixture

@pytest.mark.timeout(180)
@pytest.mark.parametrize("vendor", OBSERVED)
async def test_the_core_reads_each_vendors_catalog_fixture_to_the_fixtures_own_totals(vendor, tmp_path, monkeypatch, set_home):
    """The fixture's files (written the way the CLI writes them) answer through the declared parser and mode, and the tokens the usage
    events deliver are the fixture's expected tokens: the same oracle the Python readers' runs established."""
    data = _vendor_data(vendor, tmp_path, monkeypatch, set_home)
    try:
        for phase in data.fixture["phases"]:
            data.apply(phase)
        expected = data.fixture["expected"]["tokens"]
        path = data.path
        mode = _mode_for(vendor)
    finally:
        data.close()
    declared = facts.FACTS[vendor].observer
    async with sidecar(tmp_path, monkeypatch) as side:
        chain = Chain(side, vendor, path, mode)
        events = await chain.pass_()
        assert chain.last["parser"] == declared.parser_id and chain.last["mode"] == mode, chain.last
        assert not chain.last.get("error"), chain.last["error"]
        if any(expected):
            assert _tokens(events) == expected, [(event.get("input_tokens"), event.get("output_tokens")) for event in events]
        # A second pass from what the first answered finds nothing new: the dedup keys and the checkpoint are carried whole.
        assert await chain.pass_() == [], "a second pass over the unchanged files delivered events again"


# ------------------------------------------------------------------------------------------------------ lifecycle

@pytest.mark.timeout(180)
async def test_a_log_is_read_once_by_one_sidecar_and_a_later_one_resumes_without_a_replay(tmp_path, monkeypatch):
    log = _log(tmp_path / "logs", 1, 2)
    async with sidecar(tmp_path, monkeypatch) as side:
        chain = Chain(side, "claude", log)
        assert _tokens(await chain.pass_()) == [24, 6]
        assert await chain.pass_() == [], "an unchanged log delivered events again"
        with log.open("ab") as writer:
            writer.write(_assistant(3).encode("utf-8"))
        assert _tokens(await chain.pass_()) == [12, 3], "an append is read once, and only the append"
        saved = chain.checkpoint
        assert saved["offset"] == log.stat().st_size and saved["identity"] and saved["anchor"], saved
        await side.restart()
        # A different process: nothing is shared but the file and what the checkpoint says about it.
        resumed = Chain(side, "claude", log)
        resumed.checkpoint, resumed.seen, resumed.state = saved, set(chain.seen), chain.state
        assert await resumed.pass_() == [], "the next sidecar generation replayed what the last one delivered"
        with log.open("ab") as writer:
            writer.write(_assistant(4).encode("utf-8"))
        assert _tokens(await resumed.pass_()) == [12, 3], "the next generation reads the append that followed"
        assert resumed.checkpoint["identity"] == saved["identity"], "an append is not a new file"


@pytest.mark.timeout(180)
async def test_a_shorter_or_rewritten_log_is_a_rotation_that_restarts_at_zero(tmp_path, monkeypatch):
    log = _log(tmp_path / "logs", 1, 2, 3)
    async with sidecar(tmp_path, monkeypatch) as side:
        chain = Chain(side, "claude", log)
        assert _tokens(await chain.pass_()) == [36, 9]
        # Shorter than the offset the checkpoint holds: a rotation by size.
        log.write_text(_assistant(7), encoding="utf-8", newline="\n")
        events = await chain.pass_()
        assert [event["dedup_key"] for event in events], "a rotated log was not read from the start"
        assert _tokens(events) == [12, 3]
        assert chain.checkpoint["offset"] == log.stat().st_size
        # As long as before but not the same bytes: the anchor says it is not the file the checkpoint saw.
        before = log.read_text(encoding="utf-8")
        log.write_text(before.replace("msg7", "msg8").replace("r7", "r8"), encoding="utf-8", newline="\n")
        assert log.stat().st_size == len(before.encode("utf-8"))
        assert _tokens(await chain.pass_()) == [12, 3], "a log rewritten in place at the same length was taken for the old one"


@pytest.mark.timeout(180)
async def test_a_log_a_writer_keeps_appending_to_is_observed_without_losing_or_repeating_a_record(tmp_path, monkeypatch):
    """The CLI holds its log open and appends while the observer reads it: the open must neither fail nor stop the writer, and across
    the passes every record is delivered exactly once."""
    log = _log(tmp_path / "logs", 1)
    total = 40
    failures: list[BaseException] = []
    started, finished = threading.Event(), threading.Event()

    def writer() -> None:
        try:
            with log.open("ab") as handle:
                started.set()
                for number in range(2, total + 1):
                    handle.write(_assistant(number).encode("utf-8"))
                    handle.flush()
                    time.sleep(0.01)
        except BaseException as error:  # noqa: BLE001
            failures.append(error)
        finally:
            started.set()
            finished.set()

    thread = threading.Thread(target=writer, daemon=True)
    async with sidecar(tmp_path, monkeypatch) as side:
        chain = Chain(side, "claude", log)
        delivered: list[dict] = []
        thread.start()
        assert await asyncio.to_thread(started.wait, 20)
        while not finished.is_set():
            delivered += await chain.pass_()
        thread.join(timeout=20)
        delivered += await chain.pass_()
    assert not failures, f"the writer failed while the observer held the file: {failures[0]!r}"
    keys = [event["dedup_key"] for event in delivered]
    assert len(keys) == len(set(keys)) == total, f"{len(keys)} deliveries, {len(set(keys))} distinct, {total} written"
    assert _tokens(delivered) == [12 * total, 3 * total]


# --------------------------------------------------------------------------------------------------- source bounds

@pytest.mark.timeout(120)
async def test_a_directory_is_never_a_log_and_a_missing_log_is_refused_as_missing(tmp_path, monkeypatch):
    """Both are refused (the Python reader decides whether a log exists before it asks); the refusals differ, so one cannot pass for the other."""
    folder = tmp_path / "logs" / f"{SESSION}.jsonl"
    folder.mkdir(parents=True)
    async with sidecar(tmp_path, monkeypatch) as side:
        with pytest.raises(RuntimeError, match="not a regular file"):
            await side.observe("claude", folder, "usage")
        with pytest.raises(RuntimeError, match=r"IO_FAILED.*\(os error 2\)"):
            await side.observe("claude", tmp_path / "logs" / "absent.jsonl", "usage")


@pytest.mark.timeout(120)
async def test_a_link_to_a_log_is_not_followed(tmp_path, monkeypatch):
    real = _log(tmp_path / "real", 1)
    link = tmp_path / "logs" / "link.jsonl"
    link.parent.mkdir()
    try:
        os.symlink(real, link)
    except OSError as error:
        pytest.fail(f"this runner cannot create a file symlink ({error}); the no-follow rule cannot be exercised here")
    async with sidecar(tmp_path, monkeypatch) as side:
        with pytest.raises(RuntimeError, match="not a regular file|Too many levels|symbolic|link"):
            await side.observe("claude", link, "usage")
        assert _tokens((await side.observe("claude", real, "usage"))["events"]) == [12, 3]


# ----------------------------------------------------------------------------------------------- awkward real paths

@pytest.mark.timeout(180)
async def test_a_log_under_a_directory_with_spaces_and_non_ascii_is_read(tmp_path, monkeypatch):
    log = _log(tmp_path / AWKWARD / "with space", 1, 2)
    async with sidecar(tmp_path, monkeypatch) as side:
        chain = Chain(side, "claude", log)
        assert _tokens(await chain.pass_()) == [24, 6]
        assert chain.last["path"] == str(log), "the answer names the path as it was asked, byte for byte"
        assert chain.checkpoint["identity"], "the identity of a file under such a path is still the file system's"


@pytest.mark.timeout(180)
async def test_a_sqlite_store_under_a_directory_with_spaces_and_non_ascii_is_read(tmp_path, monkeypatch, set_home):
    """The SQLite open takes a URI built from the path (percent-encoded; on Windows a drive path, through the OS's own `winsqlite3`): the
    awkward directory proves the encoding and the UTF-8 to UTF-16 conversion agree with the file system."""
    data = _vendor_data("opencode", tmp_path, monkeypatch, set_home)
    try:
        for phase in data.fixture["phases"]:
            data.apply(phase)
        expected = data.fixture["expected"]["tokens"]
        source = data.path
    finally:
        data.close()
    folder = tmp_path / AWKWARD / "with space"
    folder.mkdir(parents=True)
    store = folder / source.name
    shutil.copy2(source, store)
    for suffix in ("-wal", "-shm"):
        sidecar_file = Path(f"{source}{suffix}")
        if sidecar_file.exists():
            shutil.copy2(sidecar_file, Path(f"{store}{suffix}"))
    async with sidecar(tmp_path, monkeypatch) as side:
        chain = Chain(side, "opencode", store, "usage")
        events = await chain.pass_()
        assert _tokens(events) == expected, [(event.get("input_tokens"), event.get("output_tokens")) for event in events]
        assert chain.last["identity"], "a store's identity is named whatever its path spells"


@pytest.mark.timeout(180)
async def test_a_sqlite_store_that_another_connection_holds_open_is_read(tmp_path, monkeypatch, set_home):
    """The CLI's own connection stays open while the observer reads (rollback journal and WAL alike): the read-only open shares."""
    data = _vendor_data("opencode", tmp_path, monkeypatch, set_home)
    try:
        for phase in data.fixture["phases"]:
            data.apply(phase)
        expected = data.fixture["expected"]["tokens"]
        path = data.path
        async with sidecar(tmp_path, monkeypatch) as side:
            # `data` still holds its writer connection here, as a running CLI does.
            chain = Chain(side, "opencode", path, "usage")
            assert _tokens(await chain.pass_()) == expected
    finally:
        data.close()


async def test_the_vendors_the_cases_above_stand_on_are_observed():
    """A rename of a registry member must not turn the parametrised cases above into a smaller test."""
    assert {"claude", "opencode"} <= set(OBSERVED)
    assert len({facts.FACTS[key].observer.parser_id for key in OBSERVED}) == len(OBSERVED), "two vendors share one parser id"
