"""Static and binary guards for the Rust runtime path (E3 S12; E1 13.1, 13.3, 15.1 F4; E2 section 9 scope).

What they refuse, in the runtime's own production sources (test modules and the harness-only `mod fault` are
cut out first):

* a sleep used as a wait (no polling interval, E1 13.1);
* a bare numeric deadline (every bound has a name with a source);
* a vendor key compared in shared code (vendor behaviour comes from declarations, E1 13.3 sites 6-7);
* a text-encoded byte payload (`base64`, `_b64`, F4);
* a read, poll or `FIONREAD`/`tcgetpgrp` on a PTY master after the handoff (E2 section 3 item 5);
* a fault hook in the product binary (checked on the binary itself, by hash);
* a Rust `cleanup_ms` that differs from the baseline's `_reap_wait_timeout_s()`.

Every allowlist entry carries its reason and the test fails if an entry is no longer needed, so the list cannot
rot into a bypass.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUST_SRC = ROOT / "native/navide-cli-runtime/src"
PY_SRC = ROOT / "backend/agent_team_backend"
PRODUCT = ROOT / "native/navide-cli-runtime/target/release/navide-cli-runtime"
HARNESS = ROOT / "native/navide-cli-runtime/target-harness/release/navide-cli-runtime"

VENDOR_MODULES = {"claude.rs", "claude_facts.rs", "codex.rs", "opencode.rs", "adapters.rs"}
PY_RUNTIME = ["cli_runtime.py", "cli_runtime_frames.py", "cli_runtime_handoff.py", "cli_runtime_observation.py"]
PY_VENDOR = ["cli_runtime_claude.py", "cli_runtime_adapters.py"]

#: Symbols that exist only for the isolated harness build (cargo feature `fault-hooks`).
FAULT_SYMBOLS = [b"fault: ", b"fault-survivor", b"ready-wire", b"requested isolated startup fault"]
def strip_balanced(text: str, opener: re.Pattern) -> str:
    """Remove every brace-balanced block that starts at a match of `opener` (the match includes the `{`)."""
    out, position = [], 0
    for match in opener.finditer(text):
        if match.start() < position:
            continue
        depth, index = 1, match.end()
        while depth and index < len(text):
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            index += 1
        out.append(text[position:match.start()])
        position = index
    out.append(text[position:])
    return "".join(out)


def production(path: Path) -> str:
    text = path.read_text()
    text = strip_balanced(text, re.compile(r"#\[cfg\(test\)\]\s*mod\s+\w+\s*\{"))
    text = strip_balanced(text, re.compile(r"#\[cfg\(feature = \"fault-hooks\"\)\]\s*mod\s+fault\s*\{"))
    # comments are not code
    return re.sub(r"(?m)//.*$", "", text)


def rust_files() -> list[Path]:
    return [p for p in sorted(RUST_SRC.glob("*.rs")) if not p.name.endswith("_test.rs")]


# ------------------------------------------------------------------------------------ waits and bounds

def test_no_sleep_is_used_as_a_wait_in_the_rust_runtime():
    offenders = [f"{p.name}: {m.group(0)}" for p in rust_files() for m in re.finditer(r"(?:thread::)?sleep\s*\(", production(p))]
    assert not offenders, offenders


def test_every_rust_duration_comes_from_a_named_bound():
    literal = re.compile(r"Duration::from_(?:secs|millis|micros|nanos)\(\s*[0-9]|(?:recv_timeout|wait_timeout)\(\s*[^,)]*Duration::from_\w+\(\s*[0-9]")
    offenders = []
    for path in rust_files():
        for number, line in enumerate(production(path).splitlines(), 1):
            if literal.search(line) and not re.match(r"\s*(pub\s+)?const\s", line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, offenders


#: (file, regex of the line) -> why the number is not a deadline in disguise.
PY_LITERAL_ALLOWLIST: dict[tuple[str, str], str] = {}


def test_python_runtime_has_no_bare_numeric_deadline():
    pattern = re.compile(r"(?:wait_for\(.*,\s*[0-9.]+\)\s*$|asyncio\.timeout\(\s*[0-9.]+|timeout\s*=\s*[0-9.]+|"
                         r"asyncio\.sleep\(|time\.sleep\(|\.result\(\s*[0-9.]+)")
    offenders, used = [], set()
    for name in PY_RUNTIME + PY_VENDOR:
        for number, line in enumerate((PY_SRC / name).read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if not pattern.search(code):
                continue
            key = next(((f, r) for (f, r) in PY_LITERAL_ALLOWLIST if f == name and re.search(r, line)), None)
            if key:
                used.add(key)
            else:
                offenders.append(f"{name}:{number}: {line.strip()}")
    assert not offenders, offenders
    assert used == set(PY_LITERAL_ALLOWLIST), "stale allowlist entries"


def test_the_observation_waits_are_named_bounds_on_blocked_executor_calls():
    """The only waits the scan executors make are upper bounds on one blocked `future.result`: named, documented."""
    for name in ("cli_runtime_observation.py", "cli_runtime_claude.py"):
        text = (PY_SRC / name).read_text()
        assert re.search(r"^OBSERVE_RESULT_S = \d+", text, re.M), name
        assert "future.result(timeout=OBSERVE_RESULT_S)" in text, name


# -------------------------------------------------------------------------------------- vendor locality

def test_shared_rust_code_never_compares_a_vendor_key():
    pattern = re.compile(r"agent_key\s*==|claude_facts::KEY|\"claude\"|\"codex\"|\"opencode\"")
    offenders = []
    for path in rust_files():
        if path.name in VENDOR_MODULES:
            continue
        for number, line in enumerate(production(path).splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, offenders


def test_shared_python_runtime_never_names_a_vendor():
    pattern = re.compile(r"[\"'](?:claude|codex|opencode)[\"']|vendor\(\s*[\"']")
    offenders = []
    for name in PY_RUNTIME:
        for number, line in enumerate((PY_SRC / name).read_text().splitlines(), 1):
            if pattern.search(line.split("#", 1)[0]):
                offenders.append(f"{name}:{number}: {line.strip()}")
    assert not offenders, offenders


# --------------------------------------------------------------------------------------- F4, master reads

def test_no_text_encoded_bytes_anywhere_in_the_runtime_sources():
    sources = [*(PY_SRC / n for n in PY_RUNTIME + PY_VENDOR), *rust_files(),
               ROOT / "native/navide-cli-runtime/Cargo.toml", ROOT / "native/navide-cli-runtime/Cargo.lock"]
    offenders = [p.name for p in sources if re.search(r"base64|_b64|b64decode|b64encode", p.read_text())]
    assert not offenders, offenders


#: libc::read call sites that are not a PTY master: function name -> what it reads.
RAW_READ_ALLOWLIST = {
    "read_full": "the control socket (the sidecar's reader)",
    "gate_owner": "the child's pid report on the gate's report pipe",
    "spawn_gated": "the child's gate pipe inside `pre_exec`",
}


def enclosing_fn(text: str, offset: int) -> str:
    names = list(re.finditer(r"(?m)^\s*(?:pub(?:\([a-z]+\))?\s+)?(?:unsafe\s+)?fn\s+(\w+)", text[:offset]))
    return names[-1].group(1) if names else "?"


def test_the_sidecar_never_reads_polls_or_queries_a_pty_master():
    offenders, used = [], set()
    forbidden = re.compile(r"FIONREAD|tcgetpgrp|libc::poll\(|libc::select\(|libc::epoll|EVFILT_READ")
    for path in rust_files():
        text = production(path)
        for match in forbidden.finditer(text):
            offenders.append(f"{path.name}: {match.group(0)}")
        for match in re.finditer(r"libc::(?:read|readv|recv|recvmsg)\(", text):
            owner = enclosing_fn(text, match.start())
            if owner in RAW_READ_ALLOWLIST:
                used.add(owner)
            else:
                offenders.append(f"{path.name}: {match.group(0)} in fn {owner}")
    assert not offenders, offenders
    assert used == set(RAW_READ_ALLOWLIST), f"stale read allowlist: {set(RAW_READ_ALLOWLIST) - used}"
    pty = production(RUST_SRC / "session_pty.rs")
    assert not re.search(r"fn\s+(read|write|poll|pending|foreground)\w*\s*\(", pty), "SessionPty exposes no read/write/poll/query"
    assert "pub fn lend_master" in pty and "BorrowedFd" in pty


# ------------------------------------------------------------------------------------ the product binary

@pytest.mark.skipif(not PRODUCT.is_file() or not HARNESS.is_file(), reason="requires the built product and harness binaries")
def test_the_product_binary_carries_no_fault_hook_and_the_harness_one_does():
    """A strings scan of the hashed binary: it sees message and flag literals, not short selector literals the
    compiler turns into immediates, so it is the receipt, while the guarantee itself is the cargo feature
    `fault-hooks` (every hook is `#[cfg(feature = "fault-hooks")]`; the product build never enables it)."""
    strings = lambda path: subprocess.run(["strings", "-a", str(path)], capture_output=True, check=True).stdout  # noqa: E731
    product, harness = strings(PRODUCT), strings(HARNESS)
    leaked = [s.decode() for s in FAULT_SYMBOLS if s in product]
    assert not leaked, f"fault-hook symbols in the product binary {hashlib.sha256(PRODUCT.read_bytes()).hexdigest()}: {leaked}"
    present = [s.decode() for s in FAULT_SYMBOLS if s in harness]
    assert len(present) == len(FAULT_SYMBOLS), f"the guard's symbol list no longer matches the harness build ({present})"


@pytest.mark.skipif(sys.platform != "darwin" or not PRODUCT.is_file(), reason="requires the built macOS product binary")
def test_the_advertised_cleanup_ms_equals_the_baselines_reap_ceiling():
    """E1 13.3 row 10 / plan G10: Rust derives `cleanup_ms` from its declarations; it must equal the ceiling the
    baseline's `_reap_wait_timeout_s()` derives from the vendor roster (8 s with Claude's 3 s grace)."""
    import tempfile

    from agent_team_backend import terminals
    from tests.cli_regression.support.frame_peer import FramePeer
    with tempfile.TemporaryDirectory() as directory:
        peer = FramePeer(Path(directory))
        try:
            assert peer.ready["limits"]["cleanup_ms"] == round(terminals._reap_wait_timeout_s() * 1000)
        finally:
            peer.close()


# --------------------------------------------------------------------- review 2, S-3: the named call sites

def test_s3_the_call_sites_review_2_named_use_named_bounds():
    """`cli_runtime.py` `wait_for(..., 2)`, `wait_for(..., 5)` and the create-owned `wait_for(..., 15)` (a site E1 13.3 row 10
    did not list) and `darwin.rs` `Duration::from_secs(5)`: each is now a named constant."""
    runtime = (PY_SRC / "cli_runtime.py").read_text()
    for name, value in (("HANDSHAKE_TEARDOWN_S", "2.0"), ("READY_EXIT_S", "5.0"), ("OWNED_RECEIPT_S", "15.0")):
        assert re.search(rf"^{name} = {re.escape(value)}", runtime, re.M), name
    assert "self._runtime.wait(), HANDSHAKE_TEARDOWN_S" in runtime and "self._runtime.wait(), READY_EXIT_S" in runtime
    assert "asyncio.shield(self._owned[sid]), OWNED_RECEIPT_S" in runtime
    darwin = (RUST_SRC / "darwin.rs").read_text()
    assert "Duration::from_millis(super::SNAPSHOT_MS)" in darwin and "Duration::from_secs(5)" not in production(RUST_SRC / "darwin.rs")
    assert re.search(r'"cleanup_ms": 8_000', runtime), "the cleanup_ms ceiling is the derived 8 s (S-4)"
