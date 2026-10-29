"""The hosted correctness precondition, on every platform, on the F0 sidecar (rf4c D8, rf4d E1, rf4e G1-G3, rf4f H1-H4).

rf4f: the reader cases run on fixtures the RUNNER builds (`stage_reader_descriptor`: the descriptor of the `reader-fixtures` pre step, verified against the registered digests, written over the packed one) instead of
being deselected; only a case whose fixture the platform does not register (Linux: the two opencode cases) stays out, by a rootdir-relative `--deselect` id (`tests/<file>::<case>`) that a real
`pytest --collect-only` proves removes something (H1, H2). The gate expects every variant of a parametrized case that real collection produced, and every iteration of a literal vitest loop (H3); a
required case that did not pass, whatever its name, fails its file and its rows, and `partly-runnable` never holds one (H4).

rh22 (the macOS diagnostic run): a platform may declare FINDINGS (`KNOWN_FINDINGS`, Darwin only). A declared case still runs. When it fails exactly as declared, the gate records the finding by id in
the gate record and the receipt and gives two verdicts on the same results: `frozenRule` (the case failed; the registered rule) and `withDeclaredFindings` (the case and its rows are `known-finding`
and nothing fails on it). `FINDINGS_RULE` names the one that decides. Any other failure of the case fails both.

rh23 (the user's decision of 2026-11-13, "declare and disclose", addendum section 18): this pack carries `withDeclaredFindings`. The one constant changed; the gate's code is rh22's.

rh24 (reviews of rh23: B High, A F-1, A F-4): the frozen r2 message quotes the first difference only, so D4M-1 is now accepted only from the WHOLE stream. The Darwin pytest call loads
`hosted_findings_plugin` (`PLUGIN`), which writes `correctness-findings-evidence.json` (`EVIDENCE_NAME`) beside the JUnit file: for a failed declared case the received stream of r2 and the `code`
and `effect` of b10, read from the failing frame. The gate rebuilds the expected stream itself and accepts D4M-1 only when the received stream is the expected one with one `\\r` inserted
immediately before some of its `\\r\\n` and nothing else (no byte lost, reordered or other), and when the record's message and first difference are the JUnit message's. A declared case with no
record, an unreadable record or a disagreeing one is an ordinary failure. The total-surplus bound is gone. D4M-2's recogniser is rh23's; its finding also records the `effect`. Off Darwin nothing
of this runs: no plugin, no record, no key.

rh25 (review A of rh24: F-1, F-3): the Darwin gate record's `findingEvidence` holds `declaredFailures`, one entry per declared case that failed or errored: whether the plugin wrote a record, the
plugin's own error, whether the gate accepted it and, when not, the recogniser's fixed code and short text (`not-pure-doubling` with the first offending byte's offset); never a byte of the stream.
The step log prints one line per refusal. A b10 `effect` that is not a string is refused (`effect-not-str`). Off Darwin nothing changes.

The frozen rule (`g3-prereg-proposal.md:134`) is: the 21 G1-E correctness rows plus the G2 required cases pass at the same H in the same session. `g1e/gates.py` `REGISTRY` is the executable form of the
21 rows (row id -> the pytest files, the vitest files and the cargo run that prove it). This module runs that registry on the runner:

* a **row table** (`correctness-gate.json`, key `rows`) names, for each row on this platform, its commands, and for each command the cases it requires. The registry maps a row to FILES, not to cases, so a
  row requires every case of each of its files. A case the source says cannot run on the platform (a `skipif`, a `runIf`, a decidable `pytest.skip`, a fixture the checkout does not hold) is listed in
  the table with that reason; it is never proved by another case. A row is `passed` only when every command ran and every one of its cases was COLLECTED and PASSED by name; `partly-runnable` when the
  cases that can run all passed and at least one command or case is listed as not runnable; `failed` when a required case is absent, skipped, failed or errored (rf4e G1, reviewer B's B1); and
  `not-runnable` when nothing of it can run here;
* the Darwin plan adds the frozen macOS G2 part, the 17 files of `driver.CORRECTNESS_FILES`, as the row `G2_ROW`, through the same per-case gate (rf4e G3);
* the run writes `correctness-receipt.json` with the sidecar path and its SHA-256 (hashed here from the file), which `receipt_problems` compares with F0's sidecar before the cost run starts;
* Linux and Windows also run the G2 required cases from the workflow (`tests/cli/g2Native.mjs guard`, by case id); `bind_g2` re-checks those ids by name in the outcomes log and binds the receipts
  output to the same sidecar (`g2-receipt-binding.json`).

The static platform scan (`scan_pytest`, `scan_vitest`) reads the test sources; it never imports or runs them. A condition it cannot evaluate counts as false (the product and harness binaries are
assumed built), so a skip it did not predict is a gate problem, never a silent pass. A skip condition that reads a file (`X.is_file()`) is decided against the runner's tree (`RunnerTree`: the freeze
head's tracked files plus the pack), never against the local disk (rf4e G2).

    hosted_correctness.py run REPO PRE_OUT SYSTEM     the registry on the F0 sidecar, then the gate, the row table and the receipt; exit 0 only when every run and the gate pass
    hosted_correctness.py rows REPO SYSTEM            print the row table of the plan (no run)
    hosted_correctness.py bind REPO PRE_OUT SYSTEM RECEIPTS_OUT OUTCOMES JUNIT REQUIRED      bind the G2 receipts and required cases to the F0 sidecar

Standard library, `g1e.gates` and `g1e.reference` only (`local_runner_tree` also uses `hosted_pack`); nothing is measured.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Callable

from tests.cli_regression.support.g3 import hosted_posix_stubs

hosted_posix_stubs.install()      # rh20 (diag3 D3-3): before any g1e import. This entry process is not under the hosted startup (the correctness plan step carries no hosted flag), and `plan` -> `hosted_reader` -> `g1e.assemble` -> `g1e.summarise` -> `g1e.quiet_gate` reads `os.getloadavg` at import (Windows lacks it). The tests this module starts are other processes and never see the stub
from tests.cli_regression.support.g1e import gates, reference  # noqa: E402

SCHEMA = "acr-g3h-correctness-gate/3"
RECEIPT_SCHEMA = "acr-g3h-correctness-receipt/1"
G2_SCHEMA = "acr-g3h-g2-binding/1"
GATE_NAME = "correctness-gate.json"
JUNIT_NAME = "correctness-junit.xml"
VITEST_NAME = "correctness-vitest.json"
RECEIPT_NAME = "correctness-receipt.json"
G2_BINDING_NAME = "g2-receipt-binding.json"
PLATFORMS = {"Linux": "linux", "Windows": "win32", "Darwin": "darwin"}
POSIX_ONLY_MODULES = ("fcntl", "pty", "termios", "resource", "tty", "grp", "pwd")      # not importable on Windows: a top-level import is a collection error there
POSIX_OS_ATTRIBUTES = ("tcgetpgrp", "fork", "getpgid", "getpgrp", "setsid", "killpg")      # absent from `os` on Windows: `hasattr(os, name)` is false there
POSIX_OS_CALLS = (*POSIX_OS_ATTRIBUTES, "openpty")      # rh21 (diag4): absent from `os` on Windows, so an unguarded CALL is an AttributeError there: the case fails, it does not skip (`_posix_fixture`)
POSIX_BRIDGE_CHANNEL = ("_sock", "_runtime")      # rh21 (diag4): the bridge's control channel is a socket on POSIX (`_sock`) and the sidecar's stdin and stdout on Windows (`_runtime`, `cli_runtime._send_windows`)
ROWS_WITHOUT_READER = ("Windows",)      # no reader rows on Windows (`hosted_reader.has_readers`), so `gates.NOT_REQUIRED_WITHOUT_READER` is not required there
G2_PLATFORMS = ("Linux", "Windows")      # the systems whose G2 required list runs in the workflow
G2_ROW = "G2-macos-frozen-17-files"      # Darwin only: the frozen macOS G2 part (rf4e G3), gated like a registry row
# The 17 files of `driver.CORRECTNESS_FILES` (the reconstruction of the mac-final-eddddb7-r3 correctness run, manager answer 3); `test_g3h_correctness.py` pins this tuple to the driver's list.
MACOS_G2_FILES = ("test_cli_runtime_composition", "test_cli_runtime_composition_vendors", "test_cli_runtime_observe_vendors", "test_cli_runtime_observe_cost", "test_cli_runtime_recovery",
                  "test_cli_runtime_lifecycle", "test_cli_runtime_handoff", "test_cli_runtime_sidecar", "test_cli_runtime_managed_login", "test_cli_runtime_vendor_transport",
                  "test_cli_runtime_control_failures", "test_cli_runtime_observe", "test_cli_runtime_claude", "test_cli_runtime_claude_parity", "test_cli_runtime_observe_codex",
                  "test_cli_create_meta_size", "test_cli_runtime_credential_env_remove")
STAGED_DESCRIPTOR = ".scratch/agent-cli-runtime-evaluation/evidence/e3/reader-fixtures.json"      # the evidence descriptor of the reader cases; the pack carries a placeholder copy (no machine path, rf4g J1), the runner stages its own over it (rf4f H1)
READER_NAMES = ("claude", "opencode")
ROOTDIR_TESTS = "tests"      # pytest's rootdir is `backend/` (`backend/pyproject.toml` holds `[tool.pytest.ini_options]`), so a node id is `tests/<file>::<case>`; `--deselect` matches `nodeid.startswith(prefix)` and normalises nothing (rf4f H2)
CLIENT = ("bunx", "--bun")
IDS = tuple(reference.CORRECTNESS_IDS)
PRINCIPAL_REASON = "requires the ticket-04 principal controller (NAVIDE_T04_WRITER_BINDING_DIR), which no hosted runner has; the test file skips without it (g1e gates.NOTES)"
ABSOLUTE_PATH = re.compile(r"(/|[A-Za-z]:[\\/])\S")
FLOOD_LINE_BYTES = 215      # one r2 flood line as the tty writes it: `FLOOD nnnnnn `, 200 `x`, `\r\n` (`lifecycle_peer.py:48`)
FLOOD_COUNTS = {"False": 2500, "True": 5000}      # the r2 variant (`slow`) -> its line count (`test_cli_runtime_lifecycle.py:649`)
ROOT_REAP_S = 1.0      # `control.rs` ROOT_REAP: how long the sidecar waits for a killed root's exit
FLOOD_MESSAGE = re.compile(r"AssertionError: first difference at byte (\d+) \(got (\d+) bytes, want (\d+)\): got (b'(?:[^'\\]|\\.)*'), want (b'(?:[^'\\]|\\.)*'); delivered=(\d+) dropped=0 produced=(\d+)(?:\n|$)")
EVIDENCE_NAME = "correctness-findings-evidence.json"      # rh24: written by `PLUGIN` beside the JUnit file, read by the gate on Darwin only
EVIDENCE_SCHEMA = "acr-g3h-findings-evidence/1"
PLUGIN = "backend.tests.cli_regression.support.g3.hosted_findings_plugin"      # rh24: `-p` of the Darwin pytest call (the call runs from the checkout's root)


def flood_expected(count: int) -> bytes:
    """The r2 stream as the frozen test expects it when nothing was dropped (`test_cli_runtime_lifecycle.py:664-666`): the echoed command, `count` flood lines and the end marker."""
    return f"flood {count}\r\n".encode() + b"".join(f"FLOOD {n:06d} ".encode() + b"x" * 200 + b"\r\n" for n in range(count)) + b"FLOOD_DONE\r\n"


def flood_difference(got: bytes, want: bytes) -> str | None:
    """The frozen test's own comparison text (`test_cli_runtime_lifecycle.py:613-620`), rebuilt here from the bytes so the message can be checked against them."""
    if got == want:
        return None
    at = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
    low = max(0, at - 8)
    return f"first difference at byte {at} (got {len(got)} bytes, want {len(want)}): got {got[low:at + 16]!r}, want {want[low:at + 16]!r}"


def carriage_return_reading(got: bytes, want: bytes) -> tuple[list[int] | None, tuple[int, int] | None]:
    """`(inserts, None)` when `got` is `want` with one `\\r` more before some of its `\\r\\n` and nothing else (each line byte equal and in order, each line end `\\r\\n` or `\\r\\r\\n`, nothing after
    the last): `inserts` are the offsets in `want` of those `\\r\\n`, empty when the two are equal. Otherwise `(None, (offset in got, offset in want))`: where the first byte of `got` that cannot be read
    so is, and how far into `want` the reading had come (rh25, review A F-1 of rh24). A line of `want` that holds a `\\r` of its own is refused, so the reading is never ambiguous."""
    lines = want.split(b"\r\n")
    if lines[-1] != b"":
        return None, (0, 0)
    pos, at, inserts = 0, 0, []
    for line in lines[:-1]:
        if b"\r" in line:
            return None, (pos, at)
        part = got[pos:pos + len(line)]
        if part != line:
            skip = next((index for index, (a, b) in enumerate(zip(part, line)) if a != b), len(part))
            return None, (pos + skip, at + skip)
        pos, at = pos + len(line), at + len(line)
        if got[pos:pos + 2] == b"\r\n":
            pos += 2
        elif got[pos:pos + 3] == b"\r\r\n":
            inserts.append(at)
            pos += 3
        else:
            skip = 2 if got[pos:pos + 2] == b"\r\r" else 1 if got[pos:pos + 1] == b"\r" else 0      # `\r\r` then another byte; `\r` then neither `\r` nor `\n`; no `\r` at all
            return None, (pos + skip, at + min(skip, 1))
        at += 2
    return (inserts, None) if pos == len(got) else (None, (pos, at))


def _refused(code: str, text: str, **more) -> tuple[None, dict]:
    """rh25 (review A F-1 of rh24): why a recogniser did not accept a declared failure, as a fixed code and a short text. Never a byte of the stream: the gate record stays small."""
    return None, {"code": code, "text": text, **more}


def _record_refusal(record, case: str, message: str) -> tuple[None, dict] | None:
    """The plugin's record checks both recognisers share: there is one, of this case, without an error of the plugin's own, and its message is the JUnit failure's."""
    if not isinstance(record, dict):
        return _refused("no-record", "the plugin wrote no record of this node id")
    if record.get("case") != case:
        return _refused("wrong-case", f"the record is of case {str(record.get('case'))[:20]!r}, not {case!r}")
    if record.get("error"):
        return _refused("plugin-error", "the plugin could not read the failing frame (its error is `pluginError`)")
    if record.get("message") != message:
        return _refused("message-mismatch", "the record's message is not the JUnit failure's message")
    return None


def _doubled_carriage_return(message: str, body: str, seconds: float, record: dict | None, node: str) -> tuple[dict | None, dict | None]:
    """D4M-1: the r2 comparison (`test_cli_runtime_lifecycle.py:668`) failed, and the WHOLE received stream (the plugin's record) is the expected stream, rebuilt here, with one `\\r` inserted
    immediately before one or more of its `\\r\\n` (ONLCR doubling) and nothing else. The record must be the JUnit failure's: the same message, and the first difference that message quotes is the
    one the bytes give. Returns `(what the finding records: the stream's digests and the insert offsets, None)`, or `(None, why not)` (rh25)."""
    match = FLOOD_MESSAGE.match(message)
    if not match:
        return _refused("message-shape", "the JUnit message is not the frozen r2 comparison message (line 668) with no drop")
    refusal = _record_refusal(record, "r2", message)
    if refusal:
        return refusal
    count = FLOOD_COUNTS.get(node.rpartition("[")[2].rstrip("]"))
    if count is None or record.get("count") != count or record.get("slow") is not (count == FLOOD_COUNTS["True"]):
        return _refused("variant-mismatch", "the record's `count` or `slow` is not the node's variant's")
    if record.get("dropped") != 0:
        return _refused("dropped", f"the record's drop total is {record.get('dropped') if isinstance(record.get('dropped'), int) else type(record.get('dropped')).__name__}, not 0")
    received = record.get("received")
    if not isinstance(received, str):
        return _refused("too-large", "the plugin kept no stream: its encoding is over the plugin's bound") if record.get("receivedTooLarge") is True else _refused("no-stream", "the record holds no received stream")
    try:
        got = received.encode("latin-1")
    except UnicodeEncodeError:
        return _refused("not-latin-1", "the record's stream is not one character per byte")
    want = flood_expected(count)
    got_bytes, want_bytes, delivered, produced = (int(match[index]) for index in (2, 3, 6, 7))
    if len(got) != record.get("receivedBytes") or hashlib.sha256(got).hexdigest() != record.get("receivedSha256"):
        return _refused("digest-mismatch", "the record's received length or SHA-256 is not its stream's")
    if len(want) != record.get("expectedBytes") or hashlib.sha256(want).hexdigest() != record.get("expectedSha256"):
        return _refused("digest-mismatch", "the record's expected length or SHA-256 is not the stream the gate builds for the variant")
    if produced != count * FLOOD_LINE_BYTES + len(b"FLOOD_DONE\r\n") + len(f"flood {count}\r\n") or not (got_bytes == delivered == len(got) and want_bytes == produced == len(want)):
        return _refused("accounting-mismatch", "the message's lengths, `delivered` or `produced` are not the record's streams'")
    if not message.startswith(f"AssertionError: {flood_difference(got, want)}; delivered="):
        return _refused("first-difference-mismatch", "the first difference the message quotes is not the one the record's bytes give")
    inserts, stop = carriage_return_reading(got, want)
    if inserts is None:
        return _refused("not-pure-doubling", f"received byte {stop[0]} (expected byte {stop[1]}) is not the expected stream with one `\\r` more before a `\\r\\n`", offset=stop[0], expectedOffset=stop[1])
    if not inserts or len(got) - len(want) != len(inserts):
        return _refused("no-insert", "the received stream has no inserted `\\r`")
    return {"stream": {"count": count, "expectedBytes": len(want), "expectedSha256": record["expectedSha256"], "receivedBytes": len(got), "receivedSha256": record["receivedSha256"],
                       "carriageReturnInsertedBefore": inserts}}, None


def _exit_reported_before_the_reap(message: str, body: str, seconds: float, record: dict | None, node: str) -> tuple[dict | None, dict | None]:
    """D4M-2: the b10 case's first assertion (`test_cli_runtime_control_failures.py:555`) saw CLEANUP_FAILED where it wants SPAWN_FAILED, and the whole case took less than the sidecar's reap bound, so the
    root was not still running at the bound: the sidecar read `waitpid(WNOHANG)` once, right after the exit notification. rh24: the plugin's record must be the JUnit failure's (the same message, and
    the code CLEANUP_FAILED); the finding records the `effect` too, which the message does not show (pytest stops at the first half of line 555). rh25: an `effect` that is not a string is refused
    (review A F-3 of rh24), and a refusal says why."""
    if not message.startswith("AssertionError: assert ('CLEANUP_FAILED' == 'SPAWN_FAILED'"):
        return _refused("message-shape", "the JUnit message is not CLEANUP_FAILED where SPAWN_FAILED is wanted")
    if re.search(r"test_cli_runtime_control_failures\.py:555: AssertionError", body) is None:
        return _refused("not-line-555", "the failure is not the assertion of line 555")
    if not seconds < ROOT_REAP_S:
        return _refused("too-slow", f"the case took {seconds:.3f} s, not under the {ROOT_REAP_S} s reap bound" if seconds != float("inf") else "the case has no duration")
    refusal = _record_refusal(record, "b10", message)
    if refusal:
        return refusal
    if record.get("code") != "CLEANUP_FAILED":
        return _refused("code-mismatch", "the record's code is not CLEANUP_FAILED")
    if not isinstance(record.get("effect"), str):
        return _refused("effect-not-str", f"the refusal's effect is {str(record.get('effectType') or type(record.get('effect')).__name__)[:40]}, not a string")
    return {"code": record["code"], "effect": record["effect"]}, None


#: rh22 (the macOS diagnostic run, `OUT/diag4/results.md` part 2): the failures of a required case that the gate RECOGNISES, per platform. The case runs; it is never deselected. When it fails, and
#: `accept` recognises the failure from the JUnit record (its message, its text and its duration), the finding is written into the gate record and the receipt by id, and the gate gives TWO verdicts
#: (`verdicts`): `frozenRule`, in which the case failed and fails its file, its rows and the gate as it always did, and `withDeclaredFindings`, in which the case's status is `known-finding` and
#: nothing fails on it. `FINDINGS_RULE` names the verdict that decides `passed`, the receipt and the step's exit code. Any other failure of the same case fails both. `kind` says what the finding is:
#: `os-terminal` is the operating system's terminal layer giving bytes the frozen test does not expect (no product code writes them); `product-defect` is a defect of the frozen product, recorded as
#: one, which the pack cannot fix. No platform but Darwin has an entry, and off Darwin nothing here runs and no record gains a key.
#: `frozenRule` is the rule as registered (`g3-prereg-proposal.md:134`: the rows pass). Deciding by `withDeclaredFindings` changes that rule for the declared cases on Darwin and for nothing else.
#: rh23: the user took that decision on 2026-11-13 ("declare and disclose", addendum section 18), so `withDeclaredFindings` decides. `frozenRule` is still evaluated and recorded beside it in every
#: run, and D4M-2 stays what it is: a defect of the frozen product, disclosed, not fixed and not hidden.
#: rh24: `accept` also reads the plugin's record of the case (`EVIDENCE_NAME`, by node id) and returns what the finding records, or None; with no record, or one that is not the JUnit failure's,
#: nothing is accepted. D4M-1 is accepted from the whole stream (`carriage_return_reading`), never from the quoted window alone.
#: rh25: `accept` returns `(what the finding records, None)` or `(None, {code, text})`, and the gate record names the refusal (`findingEvidence.declaredFailures`).
FINDINGS_RULE = "withDeclaredFindings"
FINDINGS_RULES = ("frozenRule", "withDeclaredFindings")
KNOWN_FINDINGS = {"Darwin": (
    {"id": "D4M-1", "kind": "os-terminal", "file": "test_cli_runtime_lifecycle.py", "case": "test_r2_flood_during_resize_preserves_order_accounts_drops_and_emits_late_bytes_after_the_ack",
     "accept": _doubled_carriage_return,
     "why": "macOS writes `\\r\\r\\n` for one `\\n` when a pty's output queue is full between the two bytes of the line end (shown without product code: OUT/rh22/pty-onlcr.json); the case floods a pty "
            "while its reader is held back and compares the stream with one `\\r\\n` per line"},
    {"id": "D4M-2", "kind": "product-defect", "file": "test_cli_runtime_control_failures.py", "case": "test_b10_an_exit_watcher_failure_after_gate_release_kills_and_reaps_before_answering",
     "accept": _exit_reported_before_the_reap,
     "why": "posix.rs:522-541 (macOS): after the kill the sidecar waits for the root's exit notification and then reads `waitpid(WNOHANG)` once; macOS reports the exit before the root can be reaped "
            "(OUT/rh22/note-exit-reap-sweep.json), so the read finds nothing and the sidecar answers CLEANUP_FAILED for a root that is dying. A defect of the frozen product, on a path only a failed "
            "thread start reaches; the answer errs on the careful side"},
)}


def known_finding(system: str | None, name: str, base: str, case: ElementTree.Element, evidence: dict | None = None, declared_failures: list | None = None) -> dict | None:
    """The declared finding a FAILED JUnit case is, or None: its platform, file and case name are declared, and the declaration's `accept` recognises this failure from the JUnit record and the
    plugin's record of the same node id (`evidence`, rh24). rh25 (review A F-1 of rh24): for each declared case that failed or errored, `declared_failures` gets one entry: the node id, the declared
    finding, whether the plugin wrote a record, the plugin's own error, whether it was accepted and, when not, the recogniser's code and text."""
    for entry in KNOWN_FINDINGS.get(system or "", ()):
        if entry["file"] != name or entry["case"] != base:
            continue
        failure = next((child for child in case if child.tag == "failure"), None)
        try:
            seconds = float(case.get("time") or "inf")
        except ValueError:
            seconds = float("inf")
        node = f"{ROOTDIR_TESTS}/{name}::{case.get('name') or ''}"
        record = (evidence or {}).get(node)
        if failure is not None:
            found, why = entry["accept"](failure.get("message") or "", failure.text or "", seconds, record, node)
        else:
            found, why = _refused("not-a-failure", "the case errored; only a failed assertion can be a finding")
        if declared_failures is not None:
            error = record.get("error") if isinstance(record, dict) else None
            declared_failures.append({"node": node, "finding": entry["id"], "recordPresent": isinstance(record, dict), "pluginError": str(error)[:200] if error else None,
                                      "accepted": found is not None, "rejection": why})
        if found is not None:
            return {"id": entry["id"], "kind": entry["kind"], "node": node, "message": (failure.get("message") or "")[:400], "seconds": seconds, "why": entry["why"], **found}
    return None


def row_ids(system: str) -> tuple[str, ...]:
    """The rows the receipt must cover on `system`: the 21 registry rows, and on Darwin the frozen G2 part."""
    return IDS + ((G2_ROW,) if system == "Darwin" else ())


def sidecar_rel(system: str) -> str:
    return "native/navide-cli-runtime/target/release/navide-cli-runtime" + (".exe" if system == "Windows" else "")


def python_rel(system: str) -> str:
    return "backend/.venv/Scripts/python.exe" if system == "Windows" else "backend/.venv/bin/python"


def harness_path(pre_out: str, system: str) -> str:
    return f"{pre_out}/harness-target/release/navide-cli-runtime" + (".exe" if system == "Windows" else "")


def basetemp(system: str) -> str:
    """Short on purpose (a long basetemp overflowed a path-derived name in an earlier run); pytest removes an existing directory itself."""
    return "/private/tmp/g3r6-ct" if system == "Darwin" else str(Path(tempfile.gettempdir()) / "g3r6-ct")


# ---------------------------------------------------------------------------------------------------------------------------- the runner's tree
class RunnerTree:
    """The files a skip condition may find on a runner. `paths=None` is the checkout on disk (what the runner itself sees, so the plan the runner computes is the truth); a set of repo-relative paths
    is the runner's tree computed away from the runner (`local_runner_tree`), so the local disk never decides a skip (rf4e G2)."""

    def __init__(self, root: str | Path, paths=None):
        self.root = Path(root)
        self.paths = None if paths is None else frozenset(paths)

    def has(self, rel: str) -> bool:
        return (self.root / rel).is_file() if self.paths is None else rel in self.paths

    def read(self, rel: str) -> bytes | None:
        try:
            return (self.root / rel).read_bytes()
        except OSError:
            return None

    def holds_absolute(self, value: str) -> bool:
        """Does an absolute path named inside a descriptor file exist in this tree? On disk: ask the disk (a path of another machine is absent). Computed: it must lie under the root and be listed."""
        if self.paths is None:
            return Path(value).is_file()
        try:
            return Path(value).relative_to(self.root).as_posix() in self.paths
        except ValueError:
            return False


def local_runner_tree(root: str | Path, ref: str = "HEAD") -> RunnerTree:
    """The tree of a runner checkout of `ref` plus the pack: `git ls-tree` of the freeze head and the pack's file list. Untracked and ignored files that are not in the pack are not in it."""
    from tests.cli_regression.support.g3 import hosted_pack
    listed = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", "-z", ref], capture_output=True, check=True).stdout.decode("utf-8").split("\0")
    return RunnerTree(root, {path for path in listed if path} | set(hosted_pack.checked_files(root)))


# ---------------------------------------------------------------------------------------------------------------------------- the static platform scan
class _Ctx:
    """What the evaluation of a skip condition needs: the names bound to paths in scope, the file's own path, the runner tree, and the paths it looked at."""

    def __init__(self, tree: RunnerTree, file_parts: tuple[str, ...], env: dict, refs: list, staged: tuple[str, ...] = ()):
        self.tree, self.file_parts, self.env, self.refs, self.staged = tree, file_parts, env, refs, staged      # staged: the reader fixtures the runner builds and stages for this platform


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _constants(node: ast.AST) -> list | None:
    if isinstance(node, ast.Constant):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and all(isinstance(e, ast.Constant) for e in node.elts):
        return [e.value for e in node.elts]
    return None


def _path_parts(node: ast.AST, env: dict, file_parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """The repo-relative path an expression names, as parts: `Path(__file__)` with `.resolve()`, `.parent`, `.parents[N]` and `/ "x/y"`, and the names bound to such expressions. None when it is anything else."""
    if isinstance(node, ast.Name):
        return env.get(node.id)
    if isinstance(node, ast.Call):
        if _dotted(node.func) == "Path" and len(node.args) == 1 and isinstance(node.args[0], ast.Name) and node.args[0].id == "__file__":
            return file_parts
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("resolve", "absolute") and not node.args:
            return _path_parts(node.func.value, env, file_parts)
        return None
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        base = _path_parts(node.value, env, file_parts)
        return base[:-1] if base else None
    if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents" and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
        base = _path_parts(node.value.value, env, file_parts)
        depth = node.slice.value + 1
        return base[:len(base) - depth] if base is not None and 0 < depth <= len(base) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
        left = _path_parts(node.left, env, file_parts)
        return None if left is None else left + tuple(part for part in node.right.value.split("/") if part)
    return None


def _bind_paths(body: list[ast.stmt], env: dict, file_parts: tuple[str, ...]) -> dict:
    """`env` plus every `NAME = <path expression>` in `body`, in source order (nested statements included)."""
    bound = dict(env)
    assigns = sorted((n for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)), key=lambda n: n.lineno)
    for stmt in assigns:
        parts = _path_parts(stmt.value, bound, file_parts)
        if parts is not None:
            bound[stmt.targets[0].id] = parts
    return bound


def _is_file_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "is_file" and not node.args


def _is_file(node: ast.Call, ctx: _Ctx | None) -> bool:
    """`X.is_file()`: a path the scan can resolve is looked up in the runner's tree (and recorded, so a test can require it to be there); any other path is a built binary, assumed present."""
    if ctx is None:
        return True
    parts = _path_parts(node.func.value, ctx.env, ctx.file_parts)
    if parts is None:
        return True
    rel = "/".join(parts)
    present = ctx.tree.has(rel)
    ctx.refs.append({"path": rel, "line": node.lineno, "present": present})
    return present


def _holds(node: ast.AST, platform: str, ctx: _Ctx | None = None) -> bool:
    """Does a `skipif` condition hold on `platform` ('linux', 'win32' or 'darwin')? `sys.platform` and `os.name` comparisons are evaluated; `X.is_file()` is decided against the runner's tree when `ctx`
    resolves the path (a built binary, whose path is not resolvable here, counts as present); every other term is false, so an unpredicted skip surfaces as a gate problem."""
    if isinstance(node, ast.BoolOp):
        results = [_holds(value, platform, ctx) for value in node.values]
        return any(results) if isinstance(node.op, ast.Or) else all(results)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _holds(node.operand, platform, ctx)
    if _is_file_call(node):
        return _is_file(node, ctx)
    if isinstance(node, ast.Call) and _dotted(node.func) == "hasattr" and len(node.args) == 2 and _dotted(node.args[0]) == "os" and isinstance(node.args[1], ast.Constant):
        return platform != "win32" or node.args[1].value not in POSIX_OS_ATTRIBUTES
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        subject = {"sys.platform": platform, "os.name": "nt" if platform == "win32" else "posix"}.get(_dotted(node.left))
        values = _constants(node.comparators[0])
        if subject is not None and values is not None:
            op = node.ops[0]
            if isinstance(op, ast.Eq):
                return subject == values[0]
            if isinstance(op, ast.NotEq):
                return subject != values[0]
            if isinstance(op, ast.In):
                return subject in values
            if isinstance(op, ast.NotIn):
                return subject not in values
    return False


def _fixtures_needed(node: ast.AST) -> set[str]:
    """The reader fixtures a test function opens through the descriptor: the names `claude` / `opencode` it mentions, or all of them when it loops over `rb.FIXTURES`, builds both with `rf.build` or is
    parametrized over `SINGLE_PASS` (every fixture), or when it names none (the conservative answer)."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and sub.value in READER_NAMES:
            found.add(sub.value)
        elif (isinstance(sub, ast.Attribute) and _dotted(sub) in ("rb.FIXTURES", "rf.build")) or (isinstance(sub, ast.Name) and sub.id == "SINGLE_PASS"):
            return set(READER_NAMES)
    return found or set(READER_NAMES)


def _descriptor_problem(condition: ast.AST, ctx: _Ctx | None, needs: set[str] | None = None) -> str | None:
    """A case whose gate reads a descriptor file (`X.is_file()` on a `.json` the tree holds) can only run when every absolute path the descriptor names is in the tree too. The evidence descriptor
    `reader-fixtures.json` names the fixture files by absolute path of the machine that built them; a runner holds neither those paths nor the 4.7 MB of fixtures, so the case would fail, not skip.
    For `STAGED_DESCRIPTOR` the runner builds the fixtures from the frozen generators, verifies them against the registered digests and stages its own descriptor over the packed one (rf4f H1), so a case
    that needs only fixtures the platform registers (`ctx.staged`) is runnable; one that needs another fixture is not, with that reason."""
    if ctx is None:
        return None
    for sub in ast.walk(condition):
        if not _is_file_call(sub):
            continue
        parts = _path_parts(sub.func.value, ctx.env, ctx.file_parts)
        rel = "/".join(parts) if parts else ""
        if not rel.endswith(".json") or not ctx.tree.has(rel):
            continue
        if rel == STAGED_DESCRIPTOR:
            missing = sorted((needs or set(READER_NAMES)) - set(ctx.staged))
            if not missing:
                continue
            return (f"needs the {', '.join(missing)} reader fixture(s), which this platform does not register (hosted_reader.READERS: {', '.join(ctx.staged) or 'none'}); the runner cannot build and "
                    f"digest-verify {'it' if len(missing) == 1 else 'them'} here (the opencode database header, bytes 96-99, records the SQLite version of the host that wrote it)")
        raw = ctx.tree.read(rel)
        try:
            document = json.loads(raw) if raw is not None else None
        except ValueError:
            continue
        stack = [document]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, str) and ABSOLUTE_PATH.match(item) and not ctx.tree.holds_absolute(item):
                return f"reads fixture files that {rel} names by absolute path of another machine (.../{'/'.join(Path(item.replace(chr(92), '/')).parts[-2:])}); the checkout does not hold them"
    return None


def _skipifs(node: ast.AST):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "skipif" and sub.args:
            reason = next((kw.value.value for kw in sub.keywords if kw.arg == "reason" and isinstance(kw.value, ast.Constant)), "")
            yield sub.args[0], str(reason), sub.lineno


def _top_level_posix_import(path: Path, tests_root: Path, seen: set[Path]) -> str | None:
    """The first POSIX-only module imported at the top level of `path` or, through `tests.*` imports, of a test-support module it pulls in; a guarded import (try/if) does not count."""
    if path in seen or not path.is_file():
        return None
    seen.add(path)
    for stmt in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name.split(".")[0] in POSIX_ONLY_MODULES:
                    return f"imports {alias.name} (POSIX only) at {path.name}:{stmt.lineno}"
        elif isinstance(stmt, ast.ImportFrom) and stmt.module:
            if stmt.module.split(".")[0] in POSIX_ONLY_MODULES:
                return f"imports {stmt.module} (POSIX only) at {path.name}:{stmt.lineno}"
            if stmt.module.split(".")[0] == "tests":
                for candidate in [stmt.module] + [f"{stmt.module}.{alias.name}" for alias in stmt.names]:
                    found = _top_level_posix_import(tests_root.parent / (candidate.replace(".", "/") + ".py"), tests_root, seen)
                    if found:
                        return found
    return None


def _unguarded(node: ast.AST):
    """The nodes of a test function that none of its `if` statements, conditional expressions or `try` blocks encloses (nested functions included): what runs whenever the code around it does."""
    stack = [node]
    while stack:
        item = stack.pop()
        yield item
        stack.extend(child for child in ast.iter_child_nodes(item) if not isinstance(child, (ast.If, ast.IfExp, ast.Try)))


def _posix_fixture(node: ast.AST, name: str) -> tuple[str, int] | None:
    """rh21 (diag4): (reason, line) when a test function builds a fixture that exists on POSIX only, so that on Windows the case FAILS where a guarded case would skip; None otherwise. Two forms, both read
    from the source and both unguarded (what stands inside an `if`, a conditional expression or a `try` the scan does not decide, and the case stays required):
    * a call of an `os` function Windows does not have (`POSIX_OS_CALLS`), an AttributeError there;
    * a bridge built by hand with its POSIX control socket (`x._sock = ...`) and without `x._runtime`: on Windows `cli_runtime` writes a request to the sidecar's stdin (`_send_windows` reads
      `self._runtime.stdin`), so the first request is an AttributeError.
    Such a case is deselected on Windows and stays in the row table with this reason; no other case proves it."""
    calls = sorted((sub.lineno, sub.func.attr) for sub in _unguarded(node)
                   if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in POSIX_OS_CALLS and _dotted(sub.func.value) == "os")
    if calls:
        line, attr = calls[0]
        return f"calls os.{attr} (absent from os on Windows) at {name}:{line}: a POSIX-only fixture, the case would fail there, not skip", line
    assigned: dict[tuple[str, str], int] = {}
    for sub in _unguarded(node):
        for target in sub.targets if isinstance(sub, ast.Assign) else ():
            if isinstance(target, ast.Attribute) and target.attr in POSIX_BRIDGE_CHANNEL and _dotted(target.value):
                assigned.setdefault((_dotted(target.value), target.attr), sub.lineno)
    for (owner, attr), line in sorted(assigned.items(), key=lambda item: item[1]):
        if attr == "_sock" and (owner, "_runtime") not in assigned:
            return (f"sets the bridge's POSIX control socket ({owner}._sock) at {name}:{line} and no {owner}._runtime: on Windows the bridge writes a request to the sidecar's stdin "
                    f"(cli_runtime._send_windows), which this fixture does not build; the case would fail there, not skip"), line
    return None


def scan_pytest(path: Path, platform: str, tests_root: Path, tree: RunnerTree | None = None, staged: tuple[str, ...] = ()) -> dict:
    """{runnable, reason, skips, cases, refs}. The whole module is not runnable on `platform` when a module-level `skipif` holds, `pytest.importorskip` names a POSIX-only module, or a top-level import
    (of the file or of a test-support module it imports) is POSIX-only on Windows. `cases` lists every test function as {class, case, line, run, reason, deselect}: `run` is false when a case-level `skipif`,
    a class-level `skipif` or a decidable `if <condition>: pytest.skip(...)` holds, or (`deselect`) when the case reads a descriptor naming files the tree does not hold or, on Windows, builds a
    POSIX-only fixture (`_posix_fixture`, rh21). `skips` is the not-runnable
    subset as {class, case, reason, line} (a class-level one has case None): those skips are expected, any other is a problem. `refs` lists the paths the skip conditions looked up in `tree`."""
    text = Path(path).read_text(encoding="utf-8")
    module = ast.parse(text)
    file_parts = tuple(Path(path).relative_to(Path(tests_root).parent.parent).parts) if tree is not None else ()      # the checkout is two levels above `backend/tests`
    refs: list[dict] = []
    module_env = _bind_paths(module.body, {}, file_parts) if tree is not None else {}
    ctx = _Ctx(tree, file_parts, module_env, refs, staged) if tree is not None else None
    marks: dict[str, tuple[ast.AST, str, int]] = {}
    reason = None
    for stmt in module.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets):
            for cond, why, line in _skipifs(stmt.value):
                if reason is None and _holds(cond, platform, ctx):
                    reason = f"module skipif at {Path(path).name}:{line}: {why or ast.unparse(cond)}"
        elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            for cond, why, line in _skipifs(stmt.value):
                marks[stmt.targets[0].id] = (cond, why, line)
        if isinstance(stmt, (ast.Assign, ast.Expr)):
            for sub in ast.walk(stmt.value):
                if isinstance(sub, ast.Call) and _dotted(sub.func) == "pytest.importorskip" and sub.args and isinstance(sub.args[0], ast.Constant):
                    if platform == "win32" and str(sub.args[0].value) in POSIX_ONLY_MODULES and reason is None:
                        reason = f"pytest.importorskip({sub.args[0].value!r}) at {Path(path).name}:{sub.lineno}: POSIX only"
    if reason is None and platform == "win32":
        found = _top_level_posix_import(Path(path), tests_root, set())
        reason = f"collection error on Windows: {found}" if found else None
    skips: list[dict] = []
    cases: list[dict] = []

    def decorators(node, cls):
        for decorator in node.decorator_list:
            found = list(_skipifs(decorator))
            if isinstance(decorator, ast.Name) and decorator.id in marks:
                found.append(marks[decorator.id])
            for cond, why, line in found:
                case = None if isinstance(node, ast.ClassDef) else node.name
                if _holds(cond, platform, ctx):
                    skips.append({"class": cls, "case": case, "reason": why or ast.unparse(cond), "line": line})
                elif case is not None and (problem := _descriptor_problem(cond, ctx, _fixtures_needed(node))):
                    skips.append({"class": cls, "case": case, "reason": problem, "line": line, "deselect": True})

    def visit(body, cls):
        for node in body:
            if isinstance(node, ast.ClassDef):
                decorators(node, node.name)
                visit(node.body, node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                decorators(node, cls)
                cases.append({"class": cls, "case": node.name, "line": node.lineno})
                local = _Ctx(tree, file_parts, _bind_paths(node.body, module_env, file_parts), refs, staged) if tree is not None else None
                for sub in ast.walk(node):      # `if <condition>: pytest.skip(...)` in the body, when the condition is decidable on this platform
                    if not isinstance(sub, ast.If):
                        continue
                    for inner in sub.body:
                        if isinstance(inner, ast.Expr) and isinstance(inner.value, ast.Call) and _dotted(inner.value.func) == "pytest.skip":
                            why = f"pytest.skip at {Path(path).name}:{inner.lineno}: {ast.unparse(sub.test)}"
                            if _holds(sub.test, platform, local):
                                skips.append({"class": cls, "case": node.name, "reason": why, "line": inner.lineno})
                            elif problem := _descriptor_problem(sub.test, local, _fixtures_needed(node)):
                                skips.append({"class": cls, "case": node.name, "reason": problem, "line": inner.lineno, "deselect": True})
                if platform == "win32" and (posix := _posix_fixture(node, Path(path).name)):      # rh21 (diag4): a POSIX-only fixture fails on Windows, so the case is deselected there and listed with its reason
                    skips.append({"class": cls, "case": node.name, "reason": posix[0], "line": posix[1], "deselect": True})
    visit(module.body, None)
    if reason:
        return {"runnable": False, "reason": reason, "skips": [], "cases": [], "refs": refs}
    for case in cases:
        mine = [s for s in skips if s["class"] == case["class"] and s["case"] in (None, case["case"])]
        hit = next((s for s in mine if not s.get("deselect")), mine[0] if mine else None)      # a platform skip explains the case without a deselect; the deselect is for a case that would otherwise run
        case["run"], case["reason"], case["deselect"] = hit is None, hit["reason"] if hit else None, bool(hit and hit.get("deselect"))
    return {"runnable": True, "reason": None, "skips": skips, "cases": cases, "refs": refs}


TS_DECLARATION = re.compile(r"^(?P<indent>\s*)(?P<kind>it|test|describe)(?:\.runIf\((?P<cond>.*?)\))?\(\s*(?P<q>['\"`])(?P<title>.*?)(?P=q)")
TS_TERM = re.compile(r"process\.platform\s*(?P<op>===|!==)\s*'(?P<value>\w+)'")


def _ts_holds(cond: str, platform: str) -> bool:
    """A vitest `runIf` condition on `platform`: `||` of `process.platform ===/!== 'x'` terms. The `controllerBound` constant (ticket-04 controller) is false on a hosted runner. Anything else is an error: the
    scan refuses to guess."""
    if cond.strip() == "controllerBound":
        return False
    value = False
    for term in cond.split("||"):
        match = TS_TERM.fullmatch(term.strip())
        if not match:
            raise ValueError(f"vitest condition not scannable: {cond}")
        value = value or ((match["value"] == platform) == (match["op"] == "==="))
    return value


TS_LOOP = re.compile(r"^(?P<indent>\s*)for \(const (?P<names>\w+|\[[\w, ]+\]) of (?P<values>\[.*\])(?: as const)?\) \{\s*$")
PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


def _loop_bindings(names: str, values: str) -> list[dict] | None:
    """The variable bindings of one iteration each for a single-line `for (const x of [literals])` or `for (const [a, b] of [[..], ..])`; None when the iterable is not a literal the scan can read."""
    try:
        items = ast.literal_eval(values)
    except (ValueError, SyntaxError):
        return None
    variables = [n.strip() for n in names.strip("[]").split(",")] if names.startswith("[") else [names]
    out = []
    for item in items:
        row = list(item) if names.startswith("[") and isinstance(item, (list, tuple)) else [item]
        if len(row) < len(variables):
            return None
        out.append(dict(zip(variables, row)))
    return out


def _variants(title: str, loops: list) -> dict:
    """{"variants": [full name per iteration]} for a case whose title holds `${x}` placeholders that the innermost enclosing literal loop binds: the exact names the run must produce (rf4f H3). Empty otherwise."""
    wanted = set(PLACEHOLDER.findall(title))
    if not wanted:
        return {}
    for _indent, bindings in reversed(loops):
        if bindings and all(wanted <= set(row) for row in bindings):
            return {"variants": [PLACEHOLDER.sub(lambda m, row=row: str(row[m.group(1)]), title) for row in bindings]}
    return {}


def _title_pattern(title: str) -> str:
    return ".+".join(re.escape(part) for part in re.split(r"\$\{.*?\}", title))


def scan_vitest(path: Path, platform: str) -> dict:
    """{runnable, reason, skips, cases}. `cases` lists every `it`/`test` as {case, pattern, line, run, reason}: `pattern` is the regular expression its full name (the enclosing `describe` titles and its own,
    joined by a space) must match completely, with a `${...}` placeholder as `.+`. A `runIf` guard of the case or of an enclosing `describe` that is false on `platform` makes it not runnable. A file with no
    runnable case is not runnable."""
    cases, stack, loops = [], [], []      # stack: (indent, title pattern, guard condition or None, line); loops: (indent, [(variable names, value)]) of the open `for (const x of [...])` lines
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").split("\n"), 1):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        while stack and indent <= stack[-1][0] and stripped.startswith("}"):
            stack.pop()
        while loops and indent <= loops[-1][0] and stripped.startswith("}"):
            loops.pop()
        loop = TS_LOOP.match(line)
        if loop:
            loops.append((len(loop["indent"]), _loop_bindings(loop["names"], loop["values"])))
            continue
        match = TS_DECLARATION.match(line)
        if not match:
            continue
        pattern, guards = _title_pattern(match["title"]), [(match["kind"], match["cond"], number)] if match["cond"] else []
        if match["kind"] == "describe":
            stack.append((len(match["indent"]), pattern, guards[0] if guards else None, number))
            continue
        inherited = [(g[2][0], g[2][1], g[2][2]) for g in stack if g[2]]
        full = " ".join([g[1] for g in stack] + [pattern])
        failing = next(((kind, cond, at) for kind, cond, at in inherited + guards if not _ts_holds(cond, platform)), None)
        title = " ".join([re.sub(r"\\(.)", r"\1", g[1]) for g in stack] + [match["title"]])
        cases.append({"case": title, "pattern": full, "line": number, "run": failing is None,
                      "reason": None if failing is None else f"{failing[0]}.runIf({failing[1]}) at line {failing[2]} is false on {platform}", **_variants(title, loops)})
    skips = [{"pattern": c["pattern"], "title": c["case"], "reason": c["reason"], "line": c["line"]} for c in cases if not c["run"]]
    if not any(c["run"] for c in cases):
        first = skips[0] if skips else None
        return {"runnable": False, "reason": (f"every case of {Path(path).name} is runIf-guarded and none holds on {platform} (first: line {first['line']}, {first['reason']})" if first else "no case"),
                "skips": [], "cases": [], "refs": []}
    return {"runnable": True, "reason": None, "skips": skips, "cases": cases, "refs": []}


# ---------------------------------------------------------------------------------------------------------------------------- the row table
def commands(row: str) -> list[tuple[str, str | None]]:
    """The commands of a row as (kind, target), in the registry's order: ('cargo', None), ('pytest', 'test_x.py'), ('vitest', 'x.test.ts'). The Darwin G2 part is its 17 pytest files."""
    if row == G2_ROW:
        return [("pytest", f"{name}.py") for name in MACOS_G2_FILES]
    out: list[tuple[str, str | None]] = []
    for argv in gates.REGISTRY[row]:
        if argv[:2] == ["cargo", "test"]:
            out.append(("cargo", None))
        for arg in argv:
            if arg.startswith("backend/tests/"):
                out.append(("pytest", arg[len("backend/tests/"):]))
            elif arg.startswith("tests/cli/"):
                out.append(("vitest", arg[len("tests/cli/"):]))
    return out


def plan(system: str, root: Path | str, tree: RunnerTree | None = None) -> dict:
    """The correctness plan of `system` on the checkout at `root`: the files to run and the row table's static part. {pytest, vitest, rows, scans, deselect, system}. `tree` is the runner's tree for the
    skip conditions that read a file; the default is `root` on disk, which is right on the runner and wrong anywhere else (use `local_runner_tree`)."""
    from tests.cli_regression.support.g3 import hosted_reader
    root, platform = Path(root), PLATFORMS[system]
    tree = tree or RunnerTree(root)
    staged = tuple(hosted_reader.READERS[system])      # the reader fixtures the runner builds, digest-verifies and stages for the reader cases (rf4f H1)
    scans: dict[str, dict] = {}
    rows: list[dict] = []
    for row in row_ids(system):
        entries = []
        for kind, target in commands(row):
            if kind == "cargo":
                entries.append({"kind": kind, "target": "cargo test (workflow step 'Rust tests', fail-fast ahead of this step)", "run": "workflow-step", "reason": None, "cases": []})
                continue
            key = f"{kind}:{target}"
            if row in gates.REQUIRES_ENV:
                scan = {"runnable": False, "reason": PRINCIPAL_REASON, "skips": [], "cases": [], "refs": []}
            elif row == gates.NOT_REQUIRED_WITHOUT_READER and system in ROWS_WITHOUT_READER:
                scan = {"runnable": False, "reason": f"not required on {system}: no reader rows (gates.NOT_REQUIRED_WITHOUT_READER)", "skips": [], "cases": [], "refs": []}
            else:
                if key not in scans:
                    scans[key] = scan_pytest(root / "backend/tests" / target, platform, root / "backend/tests", tree, staged) if kind == "pytest" else scan_vitest(root / "tests/cli" / target, platform)
                scan = scans[key]
            entries.append({"kind": kind, "target": target, "run": scan["runnable"], "reason": scan["reason"], "key": key, "cases": scan["cases"]})
        rows.append({"row": row, "platform": system, "commands": entries})
    pytest_files = list(dict.fromkeys(t for r in rows for c in r["commands"] for t in [c["target"]] if c["kind"] == "pytest" and c["run"] is True))
    vitest_files = list(dict.fromkeys(t for r in rows for c in r["commands"] for t in [c["target"]] if c["kind"] == "vitest" and c["run"] is True))
    deselect = [f"{ROOTDIR_TESTS}/{name}::" + (f"{c['class']}::" if c["class"] else "") + c["case"] for name in pytest_files for c in scans[f"pytest:{name}"]["cases"] if c.get("deselect")]
    return {"pytest": pytest_files, "vitest": vitest_files, "rows": rows, "scans": scans, "deselect": deselect, "system": system, "staged": list(staged)}


# ---------------------------------------------------------------------------------------------------------------------------- results
def _blank() -> dict:
    return {"collected": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0, "unexpectedSkipped": 0, "unexpected": [], "cases": {}}


def _case_key(case: dict) -> str:
    return (f"{case['class']}::" if case.get("class") else "") + case["case"]


def _junit_outcome(case: ElementTree.Element) -> str:
    for child in case:
        if child.tag in ("failure", "error", "skipped"):
            return "failed" if child.tag == "failure" else child.tag
    return "passed"


def _tally(counts: dict, key: str | None, bucket: str, name: str | None = None) -> None:
    """Count one result into the file's totals and, when it belongs to an enumerated case, into that case's own tally (which also keeps the names it saw, a parametrization suffix included)."""
    counts["collected"] += 1
    counts[bucket] = counts.get(bucket, 0) + 1      # `knownFinding` (rh22) is a key only where one was recorded
    if key is not None:
        mine = counts["cases"].setdefault(key, {"collected": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0, "names": set()})
        mine["collected"] += 1
        mine[bucket] = mine.get(bucket, 0) + 1
        mine["names"].add(name)


def node_variant(node_id: str) -> tuple[str, str, str]:
    """(file, case key, name with its parametrization) of a pytest node id like `tests/test_x.py::TestK::test_c[a-b]`; the key is `TestK::test_c`, the name `test_c[a-b]` (what JUnit calls the case)."""
    head, bracket, tail = node_id.partition("[")
    parts = head.split("::")
    return parts[0].rsplit("/", 1)[-1], "::".join(parts[1:]), parts[-1] + bracket + tail


def expected_variants(plan_: dict, name: str, key: str) -> set[str] | None:
    """The names of every variant of a required case that REAL collection produced (`plan_["collected"]`, the node ids of `pytest --collect-only` with the run's arguments), or None when the plan holds no
    collection (a static plan). A parametrized case has one name per variant; an unparametrized one has its own name."""
    collected = plan_.get("collected")
    if collected is None:
        return None
    return {variant for node in collected for file, case, variant in [node_variant(node)] if file == name and case == key}


def collect_pytest(junit_text: str, plan_: dict, accept: bool = False, evidence: dict | None = None, declared_failures: list | None = None) -> dict[str, dict]:
    """{file: counts}. A case belongs to a file when the file's module name is one dotted component of its `classname`, and to an enumerated case of the file by its class (the last component) and its name
    without the `[...]` of a parametrization. A skipped case is expected only when the scan predicted it. A failed case that is a declared finding of the platform (`known_finding`, rh22) is kept in
    the file's `findings`; it is counted as `failed` (the frozen rule) unless `accept`, which counts it as `knownFinding`. `evidence` is the plugin's records by node id (rh24, Darwin);
    `declared_failures` gets an entry per failed or errored declared case (rh25; an errored one is never a finding)."""
    root = ElementTree.fromstring(junit_text)
    counts = {name: _blank() for name in plan_["pytest"]}
    for case in root.iter("testcase"):
        parts = (case.get("classname") or "").split(".")
        for name in plan_["pytest"]:
            module = name[:-3]
            if module not in parts:
                continue
            outcome = _junit_outcome(case)
            base = (case.get("name") or "").split("[")[0]
            scan = plan_["scans"][f"pytest:{name}"]
            listed = next((c for c in scan["cases"] if c["case"] == base and parts[-1] == (c["class"] or module)), None)
            finding = known_finding(plan_.get("system"), name, base, case, evidence, declared_failures) if outcome in ("failed", "error") and listed is not None else None
            if finding:
                outcome = "knownFinding" if accept else outcome
                counts[name].setdefault("findings", []).append(finding)
            _tally(counts[name], None if listed is None else _case_key(listed), outcome, case.get("name") or "")
            if outcome == "skipped":
                expected = any((s["case"] == base or s["case"] is None) and (s["class"] is None or s["class"] in parts) for s in scan["skips"])
                if not expected:
                    counts[name]["unexpectedSkipped"] += 1
                    counts[name]["unexpected"].append(f"{(listed and _case_key(listed)) or base}")
    return counts


def collect_vitest(text: str, plan_: dict) -> dict[str, dict]:
    data = json.loads(text)
    counts = {name: _blank() for name in plan_["vitest"]}
    for result in data.get("testResults", []):
        path = str(result.get("name", "")).replace("\\", "/")
        for name in plan_["vitest"]:
            if not path.endswith("/tests/cli/" + name):
                continue
            scan = plan_["scans"][f"vitest:{name}"]
            for case in result.get("assertionResults", []):
                status = {"pending": "skipped", "todo": "skipped", "failed": "failed"}.get(case.get("status"), case.get("status"))
                bucket = status if status in ("passed", "failed", "skipped") else "error"
                full = str(case.get("fullName", ""))
                listed = next((c for c in scan["cases"] if re.fullmatch(c["pattern"], full)), None)
                _tally(counts[name], None if listed is None else _case_key(listed), bucket, full)
                if bucket == "skipped" and (listed is None or listed["run"]):
                    counts[name]["unexpectedSkipped"] += 1
                    counts[name]["unexpected"].append(full)
    return counts


def file_status(c: dict) -> tuple[str, str | None]:
    if c["collected"] == 0:
        return "missing", "not collected"
    if c["failed"] or c["error"]:
        return "failed", f"{c['failed']} failed, {c['error']} errored"
    if c["unexpectedSkipped"]:
        return "skipped", f"{c['unexpectedSkipped']} case(s) skipped that no platform condition in the source explains: {', '.join(c['unexpected'][:5])}"
    if c["passed"] == 0:
        return "skipped", f"collected {c['collected']} case(s) and none passed"
    return "passed", None


def case_status(tally: dict | None, expected: set[str] | None = None) -> str:
    """One required case: `absent` (never collected), `failed`, `skipped`, `incomplete`, `known-finding` or `passed`. A parametrized case passes only when EVERY variant did: with `expected`, the variant
    names real collection produced, a variant that never ran makes the case `incomplete` even when the others passed (rf4f H3). `known-finding` (rh22): every variant ran, none failed in another way,
    and at least one failed exactly as a declared finding of the platform (`KNOWN_FINDINGS`)."""
    if not tally or not tally["collected"]:
        return "absent"
    if tally["failed"] or tally["error"]:
        return "failed"
    if tally["skipped"] or not (tally["passed"] or tally.get("knownFinding")):
        return "skipped"
    if expected is not None and expected - set(tally.get("names", ())):
        return "incomplete"
    return "known-finding" if tally.get("knownFinding") else "passed"


def evaluate(plan_: dict, pytest_counts: dict, vitest_counts: dict) -> tuple[list[str], list[dict]]:
    """(problems, rows). A runnable command passes only when its file is collected, has no failed, errored or unexplained skipped case, AND every case the scan says can run was collected and passed by
    name; a case the source says cannot run is listed with its reason and proves nothing. A row is `failed` when any command did not pass, `partly-runnable` when everything that can run passed but a
    command or a case is listed as not runnable, `passed` when every command ran in full, and `not-runnable` when nothing of it can run here. The cargo run is the workflow step. rh22: a required case
    that failed exactly as a declared finding (`known-finding`) does not fail its file; its row is `known-finding`, never `passed` or `partly-runnable`."""
    problems: list[str] = []
    status_of = {}
    for kind, counts in (("pytest", pytest_counts), ("vitest", vitest_counts)):
        for name, c in counts.items():
            status, why = file_status(c)
            scan = plan_["scans"][f"{kind}:{name}"]
            required = []
            for case in scan["cases"]:
                if case["run"]:
                    expected = expected_variants(plan_, name, _case_key(case)) if kind == "pytest" else (set(case["variants"]) if case.get("variants") else None)      # vitest: the iterations of a literal loop
                    required.append({"case": case["case"], "id": _case_key(case) if kind == "pytest" else case["case"], "status": case_status(c["cases"].get(_case_key(case)), expected),
                                     "variants": None if expected is None else len(expected)})
            absent = [r["id"] for r in required if r["status"] == "absent"]
            short = [f"{r['id']} ({r['status']})" for r in required if r["status"] not in ("passed", "absent", "known-finding")]      # a required case that did not pass fails the file whatever its name (rf4f H4)
            if status == "passed" and (absent or short):
                status = "failed"
                why = "; ".join(([f"{len(absent)} required case(s) not collected: {', '.join(absent[:5])}"] if absent else []) + ([f"{len(short)} required case(s) not passed: {', '.join(short[:5])}"] if short else []))
            status_of[f"{kind}:{name}"] = {"status": status, "reason": why, "counts": {k: v for k, v in c.items() if k not in ("cases", "unexpected", "findings")}, "requiredCases": required}
            if status != "passed":
                problems.append(f"{name}: {why}")
    rows = []
    for row in plan_["rows"]:
        outcome = []
        for entry in row["commands"]:
            if entry["run"] is True:
                scan = plan_["scans"][entry["key"]]
                outcome.append({**{k: entry[k] for k in ("kind", "target")}, **status_of[entry["key"]], "expectedSkips": [s["reason"] for s in scan["skips"]],
                                "notRunnableCases": [{"case": s["case"] if "pattern" not in s else s["title"], "class": s.get("class"), "reason": s["reason"]} for s in scan["skips"]]})
            elif entry["run"] == "workflow-step":
                outcome.append({"kind": entry["kind"], "target": entry["target"], "status": "workflow-step", "reason": None})
            else:
                outcome.append({"kind": entry["kind"], "target": entry["target"], "status": "not-runnable", "reason": entry["reason"]})
        ran = [o for o in outcome if o["status"] != "not-runnable"]
        if not ran:
            state = "not-runnable"
        elif any(o["status"] in ("missing", "failed", "skipped") or any(r["status"] not in ("passed", "known-finding") for r in o.get("requiredCases", [])) for o in ran):
            state = "failed"      # `partly-runnable` never holds a required case that was skipped, absent, incomplete or failed (rf4f H4)
        elif any(r["status"] == "known-finding" for o in ran for r in o.get("requiredCases", [])):
            state = "known-finding"      # rh22: said of the row, so that no row with a finding reads as passed
        elif any(o["status"] == "not-runnable" or o.get("notRunnableCases") for o in outcome):
            state = "partly-runnable"
        else:
            state = "passed"
        rows.append({"row": row["row"], "platform": row["platform"], "status": state, "commands": outcome})
    return problems, rows


def sha256_file(path: str | Path) -> str | None:
    """The SHA-256 of the file's bytes, hashed here (never by `sha256sum`, which escapes a backslash path); None when the file cannot be read, so a receipt then fails the F0 comparison."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def receipt(system: str, repo: str, pre_out: str, rows: list[dict], problems: list[str], findings: dict | None = None) -> dict:
    """What the cost run's precondition compares with F0: the sidecar this correctness run used, by path and by the SHA-256 of the file on disk now, and the harness beside it. On a platform that
    declares findings (rh22) the receipt also names the ones this run recorded (id, kind, node id), the rule that decided `passed`, and whether each of the two verdicts passed."""
    sidecar = f"{repo}/{sidecar_rel(system)}"
    harness = harness_path(pre_out, system)
    return {"schema": RECEIPT_SCHEMA, "platform": system, "sidecar": {"path": sidecar, "sha256": sha256_file(sidecar)}, "harnessSha256": sha256_file(harness),
            "rowIds": [r["row"] for r in rows], "rowStatuses": {r["row"]: r["status"] for r in rows}, "problems": problems, "passed": not problems and all(r["status"] != "failed" for r in rows),
            **({"knownFindings": [{key: finding[key] for key in ("id", "kind", "node")} for finding in findings["knownFindings"]], "findingsRule": findings["findingsRule"],
                "verdicts": {name: verdict["passed"] for name, verdict in findings["verdicts"].items()}} if findings is not None else {})}


def load_evidence(pre_out: str | Path) -> tuple[dict, dict]:
    """rh24: (the plugin's records by node id, what the gate record says of the file). A missing or unreadable file, or one that is not an `EVIDENCE_SCHEMA` record, gives no record, so no declared
    case is accepted."""
    path = Path(pre_out) / EVIDENCE_NAME
    if not path.is_file():
        return {}, {"file": EVIDENCE_NAME, "present": False, "readable": False, "records": 0}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        document = None
    records = document.get("records") if isinstance(document, dict) and document.get("schema") == EVIDENCE_SCHEMA else None
    if not (isinstance(records, dict) and all(isinstance(value, dict) for value in records.values())):
        return {}, {"file": EVIDENCE_NAME, "present": True, "readable": False, "records": 0}
    return records, {"file": EVIDENCE_NAME, "present": True, "readable": True, "records": len(records), "sha256": sha256_file(path)}


def write_outputs(system: str, repo: str, pre_out: str, plan_: dict, junit_text: str | None, vitest_text: str | None, extra_problems: list[str] = ()) -> list[str]:
    """Gate the two result files against the plan, then write `correctness-gate.json` (counts, problems, the row table, the deselected ids, the reader descriptor staging and the number of collected node ids)
    and `correctness-receipt.json`. `extra_problems` are what the steps before the gate found (staging, real collection). Returns the problems of the verdict that decides. rh22, on a platform that
    declares findings: the gate is evaluated under both rules, the record and the receipt carry `knownFindings` (the ones this run recorded; empty when none showed), `findingsRule` and `verdicts`,
    and `plan_` gets the three and `failedOutsideTheFindings`, the number of failed or errored JUnit cases that are no finding, which `run` needs to read pytest's exit code."""
    problems: list[str] = list(extra_problems)
    pytest_counts: dict = {}
    other_counts: dict = {}
    vitest_counts: dict = {}
    declared = system in KNOWN_FINDINGS
    decides = FINDINGS_RULE if declared else FINDINGS_RULES[0]
    other = FINDINGS_RULES[1 - FINDINGS_RULES.index(decides)]
    findings: list[dict] = []
    evidence, evidence_status = load_evidence(pre_out) if declared else (None, None)
    declared_failures: list[dict] | None = [] if declared else None      # rh25: one entry per failed declared case, from the deciding rule's pass only
    try:
        pytest_counts = collect_pytest(junit_text, plan_, decides == FINDINGS_RULES[1], evidence, declared_failures) if junit_text is not None else {}
        if junit_text is None and plan_["pytest"]:
            problems.append("pytest wrote no JUnit file")
        if declared and junit_text is not None:
            other_counts = collect_pytest(junit_text, plan_, other == FINDINGS_RULES[1], evidence)
            findings = [finding for counts in pytest_counts.values() for finding in counts.get("findings", [])]
            plan_["failedOutsideTheFindings"] = sum(1 for case in ElementTree.fromstring(junit_text).iter("testcase") if _junit_outcome(case) in ("failed", "error")) - len(findings)
    except ElementTree.ParseError as error:
        problems.append(f"the JUnit file is not parseable ({error.__class__.__name__})")
    try:
        vitest_counts = collect_vitest(vitest_text, plan_) if vitest_text is not None else {}
        if vitest_text is None and plan_["vitest"]:
            problems.append("vitest wrote no JSON result file")
    except ValueError as error:
        problems.append(f"the vitest JSON file is not parseable ({error.__class__.__name__})")
    before_the_gate = list(problems)
    gate_problems, rows = evaluate(plan_, pytest_counts or {n: _blank() for n in plan_["pytest"]}, vitest_counts or {n: _blank() for n in plan_["vitest"]})      # no readable result: every file counts as not collected
    problems += gate_problems
    extra_keys = None
    if declared:      # rh22: the other rule's verdict on the same result files, so that one run says what each rule decides
        other_problems, other_rows = evaluate(plan_, other_counts or {n: _blank() for n in plan_["pytest"]}, vitest_counts or {n: _blank() for n in plan_["vitest"]})
        verdict = lambda found, table: {"passed": not found, "problems": found, "rowStatuses": {row["row"]: row["status"] for row in table}}      # noqa: E731
        extra_keys = {"knownFindings": findings, "findingsRule": decides, "verdicts": {decides: verdict(problems, rows), other: verdict(before_the_gate + other_problems, other_rows)}}
        plan_.update(extra_keys, declaredFailures=declared_failures)
        extra_keys = {**extra_keys, "findingEvidence": {**evidence_status, "declaredFailures": declared_failures}}      # rh24: the gate record says whether the plugin's record was there and readable; rh25: and, per failed declared case, why it was or was not accepted (the receipt keeps rh23's keys)
    Path(pre_out).mkdir(parents=True, exist_ok=True)
    record = {"schema": SCHEMA, "platform": system, "rows": rows, "deselected": plan_.get("deselect", []), "readerDescriptor": plan_.get("readerDescriptor"),
              "collectedNodeIds": None if plan_.get("collected") is None else len(plan_["collected"]), "problems": problems, "passed": not problems, **(extra_keys or {})}
    (Path(pre_out) / GATE_NAME).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (Path(pre_out) / RECEIPT_NAME).write_text(json.dumps(receipt(system, repo, pre_out, rows, problems, extra_keys), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return problems


# ---------------------------------------------------------------------------------------------------------------------------- the run
def pytest_argv(python: str, files: list[str], junit: str, temp: str, deselect: list[str] = (), plugins: list[str] = ()) -> list[str]:
    """`plugins` (rh24): `-p` modules, given on Darwin only (`PLUGIN`); empty elsewhere, so the Linux and Windows calls are rh23's."""
    return [python, "-m", "pytest", *[f"backend/tests/{name}" for name in files], *[arg for node in deselect for arg in ("--deselect", node)], "-q", "-p", "no:cacheprovider",
            *[arg for plugin in plugins for arg in ("-p", plugin)], "-o", "junit_family=xunit2", "--basetemp", temp, "--junitxml", junit]


def collect_argv(python: str, files: list[str], deselect: list[str] = ()) -> list[str]:
    """`pytest --collect-only` with the run's own file and `--deselect` arguments, so real collection enumerates exactly the node ids the run will execute (rf4f H2, H3)."""
    return [python, "-m", "pytest", *[f"backend/tests/{name}" for name in files], *[arg for node in deselect for arg in ("--deselect", node)], "--collect-only", "-q", "-p", "no:cacheprovider"]


COLLECTED_LINE = re.compile(r"(?:(\d+)/)?(\d+) tests? collected(?: \((\d+) deselected\))?")


def parse_collection(text: str) -> tuple[list[str], int | None, int]:
    """(node ids, deselected count, collected count) of `pytest --collect-only -q` output: every line that starts with `tests/` and holds `::` is a node id (a parametrization id may hold spaces); the summary
    line says how many items `--deselect` removed. `None` when the output has no summary line."""
    ids = [line.strip() for line in text.splitlines() if line.startswith(f"{ROOTDIR_TESTS}/") and "::" in line]
    summary = COLLECTED_LINE.search(text)
    return ids, (int(summary.group(3) or 0) if summary else None), (int(summary.group(1) or summary.group(2)) if summary else 0)


def real_collect(argv: list[str], cwd: str, env: dict) -> tuple[int, str]:
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    return done.returncode, done.stdout


def collection_problems(ids: list[str], deselected: int | None, collected: int, deselect: list[str], unfiltered: list[str] | None = None) -> list[str]:
    """What real collection says about the plan: it collected something, and each `--deselect` id removed exactly the cases the plan names for it (rf4g J3). `--deselect` is a prefix match, so an id is checked on
    its own against `unfiltered`, the node ids of the same collection without any `--deselect`: it must match at least one item (an id that matches nothing, like the pre-rf4f `backend/tests/...` form, removes
    none), and every item it matches must be the named case itself or one of its parametrized variants (`<id>[...]`), not a different case that merely starts with the same text. The total alone cannot tell, since an
    id that matches two items hides another that matches none. Without `unfiltered` only the total is checked."""
    out = []
    if not ids or collected != len(ids):
        out.append(f"pytest --collect-only listed {len(ids)} node id(s) but reported {collected} collected")
    if deselect and (deselected is None or deselected < len(deselect)):
        out.append(f"{len(deselect)} --deselect id(s) removed only {deselected or 0} item(s) in real collection: an id matches nothing (node ids are relative to the rootdir, `{ROOTDIR_TESTS}/<file>::<case>`)")
    for node in deselect if unfiltered is not None else ():
        hit = [item for item in unfiltered if item.startswith(node)]
        stray = [item for item in hit if item != node and not item.startswith(node + "[")]
        if not hit:
            out.append(f"--deselect id {node!r} matches no collected case")
        elif stray:
            out.append(f"--deselect id {node!r} also matches {len(stray)} case(s) the plan does not name: {stray[:3]}")
    return out


def vitest_argv(files: list[str], out: str) -> list[str]:
    return [*CLIENT, "vitest", "run", "--project", "cli", *[f"tests/cli/{name}" for name in files], "--reporter=json", f"--outputFile.json={out}"]


def stage_reader_descriptor(repo: str, pre_out: str, system: str) -> dict:
    """The runner-local fixture descriptor of the reader cases (rf4f H1). The pack carries a placeholder `reader-fixtures.json` (relative placeholder paths, rf4g J1); here the descriptor the `reader-fixtures`
    pre step wrote (`pre_out/FIXTURES.json`: fixtures generated on this runner by the frozen synthetic generators of `reader_fixtures`, SEED 20260725) is verified against the registered digests for the readers
    this platform registers, and written over the packed one. Returns {fixtures, staged, packedSha256, stagedSha256, entries, problems}; nothing is staged (and a problem recorded) when the descriptor is missing or
    does not verify, so the reader cases then fail loudly on the packed paths instead of passing on something else. A platform with no readers stages nothing."""
    from tests.cli_regression.support.g3 import fixtures as g3_fixtures, hosted_reader
    names = tuple(hosted_reader.READERS[system])
    target = Path(repo) / STAGED_DESCRIPTOR
    record: dict = {"fixtures": list(names), "staged": False, "target": STAGED_DESCRIPTOR, "problems": []}
    if not names:
        return record
    source = Path(pre_out) / "FIXTURES.json"
    problems = g3_fixtures.descriptor_problems(source, names=names)
    if problems:
        record["problems"] = problems
        return record
    descriptor = json.loads(source.read_text(encoding="utf-8"))
    record["packedSha256"] = sha256_file(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)      # never write through a link
    target.write_text(json.dumps({name: descriptor[name] for name in names}, indent=1) + "\n", encoding="utf-8")
    record.update(staged=True, stagedSha256=sha256_file(target), source=str(source), entries={name: {k: descriptor[name][k] for k in ("bytes", "entries", "sha256", "golden_sha256")} for name in names})
    return record


def run(repo: str, pre_out: str, system: str, *, python: str | None = None, call: Callable[..., subprocess.CompletedProcess] = subprocess.run, tree: RunnerTree | None = None,
        collect: Callable[[list[str], str, dict], tuple[int, str]] = real_collect) -> int:
    """The registry's pytest and vitest files that can run on `system`, on the F0 sidecar (the file the cost run measures) and the harness built beside it, then the gate. Exit 0 only when both runs exit 0
    and the gate has no problem. The runner first stages its own reader descriptor (`stage_reader_descriptor`), so the reader cases run on fixtures it built and verified; then REAL collection
    (`pytest --collect-only` with the run's arguments) fixes the node ids the gate expects, every variant of a parametrized case included, and proves each `--deselect` id removes something. What the scan lists as
    not runnable on the platform is deselected (it would fail, not skip) and stays listed in the row table."""
    sidecar = f"{repo}/{sidecar_rel(system)}"
    env = {"NAVIDE_CLI_RUNTIME_BIN": sidecar, "NAVIDE_T05_SIDECAR": sidecar, "NAVIDE_CLI_RUNTIME_HARNESS_BIN": harness_path(pre_out, system)}
    clean = {k: v for k, v in os.environ.items() if not k.startswith("ACR_G3_HOSTED") and k not in ("BUN_OPTIONS", "NAVIDE_T04_WRITER_BINDING_DIR")}      # the tests run on the product, not under the measurement seams, and without a ticket-04 controller
    chosen = plan(system, repo, tree)
    junit, vitest = f"{pre_out}/{JUNIT_NAME}", f"{pre_out}/{VITEST_NAME}"
    Path(pre_out).mkdir(parents=True, exist_ok=True)
    chosen["readerDescriptor"] = stage_reader_descriptor(repo, pre_out, system)
    extra = [f"reader descriptor: {p}" for p in chosen["readerDescriptor"]["problems"]]
    codes = []
    if chosen["pytest"]:
        interpreter = python or f"{repo}/{python_rel(system)}"
        code, text = collect(collect_argv(interpreter, chosen["pytest"], chosen["deselect"]), repo, {**clean, **env})
        ids, deselected, collected = parse_collection(text)
        chosen["collected"] = ids
        unfiltered = None
        if chosen["deselect"]:      # rf4g J3: the same collection with no --deselect, so each id is checked on its own
            full_code, full_text = collect(collect_argv(interpreter, chosen["pytest"], []), repo, {**clean, **env})
            unfiltered = parse_collection(full_text)[0]
            extra += [f"pytest --collect-only (no --deselect) exited {full_code}"] if full_code != 0 else []
        extra += ([f"pytest --collect-only exited {code}"] if code != 0 else []) + collection_problems(ids, deselected, collected, chosen["deselect"], unfiltered)
        plugins = (PLUGIN,) if system in KNOWN_FINDINGS else ()
        if plugins:
            Path(f"{pre_out}/{EVIDENCE_NAME}").unlink(missing_ok=True)      # rh24: only this run's plugin record can be read by this run's gate
        codes.append(call(pytest_argv(interpreter, chosen["pytest"], junit, basetemp(system), chosen["deselect"], plugins), cwd=repo, env={**clean, **env}).returncode)
    if chosen["vitest"]:
        codes.append(call(vitest_argv(chosen["vitest"], vitest), cwd=repo, env={**clean, **env}).returncode)
    problems = write_outputs(system, repo, pre_out, chosen, Path(junit).read_text(encoding="utf-8") if Path(junit).is_file() else None, Path(vitest).read_text(encoding="utf-8") if Path(vitest).is_file() else None, extra)
    for problem in problems:
        print(f"correctness gate: {problem}", file=sys.stderr)
    for finding in chosen.get("knownFindings") or []:
        print(f"correctness gate: declared finding {finding['id']} ({finding['kind']}): {finding['node']}", file=sys.stderr)
    for entry in chosen.get("declaredFailures") or []:      # rh25 (review A F-1 of rh24): one line per declared case the gate did not accept
        if not entry["accepted"]:
            print(f"correctness gate: not accepted as {entry['finding']} ({entry['rejection']['code']}): {entry['node']}: {entry['rejection']['text']}"
                  + (f"; plugin error: {entry['pluginError']}" if entry["pluginError"] else ""), file=sys.stderr)
    for name, verdict in (chosen.get("verdicts") or {}).items():
        print(f"correctness gate: verdict {name}: {'passed' if verdict['passed'] else 'FAILED'}" + (" (decides)" if name == chosen["findingsRule"] else ""), file=sys.stderr)
    if chosen.get("findingsRule") == FINDINGS_RULES[1] and chosen["pytest"] and codes[0] == 1 and chosen.get("knownFindings") and chosen.get("failedOutsideTheFindings") == 0:
        codes[0] = 0      # rh22, only when the declared findings decide: pytest exits 1 when a case failed, and every failed case of this run is a finding the gate recorded
    return 0 if all(code == 0 for code in codes) and not problems else 1


# ---------------------------------------------------------------------------------------------------------------------------- binding to F0
def _load(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def receipt_problems(f0: dict, pre_out: str | Path, system: str) -> list[str]:
    """The precondition: the correctness receipt (every system) and the G2 binding (Linux, Windows) exist, passed, and name F0's sidecar by path and by SHA-256. A receipt of another sidecar fails."""
    problems = []
    f0_sidecar = f0.get("sidecar") or {}
    for name, schema in [(RECEIPT_NAME, RECEIPT_SCHEMA)] + ([(G2_BINDING_NAME, G2_SCHEMA)] if system in G2_PLATFORMS else []):
        record = _load(Path(pre_out) / name)
        if not (isinstance(record, dict) and record.get("schema") == schema):
            problems.append(f"{name} is missing or not a {schema} record")
            continue
        if record.get("passed") is not True:
            problems.append(f"{name} did not pass: {record.get('problems')}")
        sidecar = record.get("sidecar") or {}
        if sidecar.get("sha256") != f0_sidecar.get("sha256"):
            problems.append(f"{name} names another sidecar than F0 (sha256 differs)")
        if os.path.realpath(str(sidecar.get("path", ""))) != os.path.realpath(str(f0_sidecar.get("path", ""))):
            problems.append(f"{name} names another sidecar path than F0")
        if name == RECEIPT_NAME and sorted(record.get("rowIds", [])) != sorted(row_ids(system)):
            problems.append(f"{name} does not cover the 21 correctness rows" + (" and the macOS G2 part" if system == "Darwin" else ""))
    return problems


RECEIPTS_LINE = re.compile(r"ready receipts=(\d+) expected executableSha256=([0-9a-fA-F]+) problems=(\d+)")
OUTCOME_LINE = re.compile(r"\bcase=(.+) status=(\S+) stage=\S+\s*$", re.M)      # `g2Native.mjs guard` lines: `... case=<id> status=<status> stage=<stage>`; a parametrized id may hold spaces


def required_ids(text: str) -> list[str]:
    """The node ids of a `tests/cli/g2-required-<platform>.txt`: blank lines and `#` comments skipped (as `g2Native.requiredIds`)."""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def bind_g2(repo: str, pre_out: str, system: str, receipts_out: str, outcomes: str, junit: str, required: str) -> list[str]:
    """Re-check, by name, the G2 required cases the workflow's guard ran, and bind the receipts step to the F0 sidecar: the receipts output must say `problems=0` for the SHA-256 of the sidecar file as it is now,
    every required id must be `passed` in the outcomes log, and the record names the sidecar by path and SHA-256 (`g2-receipt-binding.json`)."""
    sidecar = f"{repo}/{sidecar_rel(system)}"
    sha = sha256_file(sidecar)
    problems = []
    text = Path(receipts_out).read_text(encoding="utf-8")
    matches = RECEIPTS_LINE.findall(text)
    if len(matches) != 1:
        problems.append("the receipts output has no single `ready receipts=... problems=...` line")
    else:
        ready, named, bad = matches[0]
        if named.lower() != sha:
            problems.append("the receipts were checked against another sidecar hash than the F0 sidecar's")
        if int(bad) != 0 or int(ready) == 0:
            problems.append(f"the receipts step reported {ready} ready receipt(s) and {bad} problem(s)")
    ids = required_ids(Path(required).read_text(encoding="utf-8"))
    states = dict(OUTCOME_LINE.findall(Path(outcomes).read_text(encoding="utf-8")))
    problems += [f"{case}: {states.get(case, 'missing')}" for case in ids if states.get(case) != "passed"]
    if not ids:
        problems.append("the required list names no case")
    record = {"schema": G2_SCHEMA, "platform": system, "sidecar": {"path": sidecar, "sha256": sha}, "harnessSha256": sha256_file(harness_path(pre_out, system)), "requiredCases": len(ids),
              "passedCases": sum(states.get(case) == "passed" for case in ids), "receiptsOutputSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "junitSha256": sha256_file(junit),
              "requiredListSha256": sha256_file(required), "problems": problems, "passed": not problems}
    Path(pre_out).mkdir(parents=True, exist_ok=True)
    (Path(pre_out) / G2_BINDING_NAME).write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return problems


def table_markdown(rows: list[dict]) -> str:
    """The row table as Markdown: row id, platform, status, then each command with its outcome or reason and its case counts. Each command's required and not-runnable cases follow the table, by name."""
    lines = ["| row | platform | status | commands |", "|---|---|---|---|"]
    for row in rows:
        cells = "; ".join(f"{c['kind']} {c['target']}: {c['status']}" + (f" ({c['reason']})" if c.get("reason") else "")
                          + (f" [{len(c['requiredCases'])} required case(s), {len(c['notRunnableCases'])} not runnable]" if c.get("requiredCases") is not None and (c["requiredCases"] or c["notRunnableCases"]) else "")
                          for c in row["commands"])
        lines.append(f"| {row['row']} | {row['platform']} | {row['status']} | {cells} |")
    detail = ["", "## Cases by row", ""]
    for row in rows:
        for c in row["commands"]:
            if c.get("requiredCases") is None or not (c["requiredCases"] or c["notRunnableCases"]):
                continue
            detail.append(f"- `{row['row']}` / {c['kind']} `{c['target']}`")
            detail += [f"  - required: `{r['id']}`" + (f" ({r['status']})" if r["status"] not in ("passed", "absent") else "") for r in c["requiredCases"]]
            detail += [f"  - not runnable: `{(n['class'] + '::' if n.get('class') else '') + n['case']}`: {n['reason']}" for n in c["notRunnableCases"]]
    return "\n".join(lines) + "\n" + ("\n".join(detail) + "\n" if len(detail) > 3 else "")


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[0] == "run":
        return run(argv[1], argv[2], argv[3])
    if len(argv) == 3 and argv[0] == "rows":
        chosen = plan(argv[2], argv[1])
        print(table_markdown(evaluate(chosen, {n: _blank() for n in chosen["pytest"]}, {n: _blank() for n in chosen["vitest"]})[1]))
        return 0
    if len(argv) == 8 and argv[0] == "bind":
        problems = bind_g2(*argv[1:])
        for problem in problems:
            print(problem)
        return 0 if not problems else 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
