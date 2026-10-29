"""G1-E trial summariser: manifested trial receipts -> admitted trials, per-trial scalars, per-row pairs (ticket E4; derived from the ticket 06 summariser).

What is RECOMPUTED here from receipts (never read from a producer's verdict): the byte-ledger equalities and bounds (E2 v10 section 3 item 4 c-e, g), the log
size/rotation/replacement rule (4g), the after-exit log final check (4f, through the reference checker), the control-plane frame-type set (item 5), the
isolation identity (item 7), the Rust ownership lists, the per-trial statistic of every metric key, the pair assembly and the canary series.
What is TAKEN from the producer's record (the E4 producer obligation, E2 v10 section 12 "Limit"): that a timestamp was taken where it says, that the peer
and the stream digests were computed over the same offsets, and that the driver's text comparison of the log was made.
Any structural problem makes the receipt inadmissible (an integrity failure, the slot counts as missing); a recomputed validity problem makes the admitted
trial evidence-invalid. Neither can ever help a CONTINUE: the decider derives INCONCLUSIVE from either.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

from agent_team_backend import cli_runtime_frames as wire
from tests.cli_regression.support.g1e import quiet_gate
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e.bundle import Bundle, BundleError

MODES = ref.ARMS
CELLS = ref.CELLS
REPEATS = ref.N_PAIRS
RESOURCE_MIN_SAMPLES = 30            # E2 v10 C2: a resource phase counts only with >= 30 samples
MIB = 1048576
TRIAL_NAME = re.compile(r"^trials/trial-(python|rust-shell)-(1|4)-([0-9])\.json$")
#: The discarded warm-up receipts (v14-r2 V19): `calibration/warmup-python-00` (the calibration's, listed in F1) and `warmup/warmup-python-00`, `warmup/warmup-rust-00` (the matrix's, bound to F1).
WARMUP_NAME = re.compile(r"^(calibration|warmup)/warmup-(python|rust)-([0-9]{2})\.json$")
WARMUP_ARM = {"python": "python", "rust": "rust-shell"}
RESOURCES_SUFFIX = "-resources.jsonl"
FREEZE_NAME = "freeze.json"
PHASE_SEQUENCE = ("cold_startup", "empty_idle", "shell_startup", "agent_startup", "live_idle", "input_output", "burst", "observation", "reconnect",
                  "resize_ack", "kill", "empty_started_idle", "persistence_restart", "finished")
PHASE_OUTCOME_VALUES = ("passed", "failed", "running")
LATENCY_PHASE = {
    "cold_backend_ready_ms": "cold_startup", "first_shell_ack_ms": "shell_startup", "first_shell_ready_ms": "shell_startup", "warm_shell_ack_ms": "shell_startup",
    "warm_shell_ready_ms": "shell_startup", "cold_to_first_usable_ms": "shell_startup", "agent_ack_ms": "agent_startup", "agent_ready_ms": "agent_startup",
    "io_ms": "input_output", "burst_ms": "burst", "burst_input_ms": "burst", "observation_ms": "observation", "reconnect_io_ms": "reconnect",
    "reconnect_recovery_ms": "reconnect", "resize_ack_ms": "resize_ack", "kill_reap_ms": "kill", "persistence_restart_ms": "persistence_restart",
}
RESOURCE_PHASES = ("empty_idle", "shell_startup", "agent_startup", "live_idle", "input_output", "burst", "observation", "reconnect", "kill", "empty_started_idle")
VIEWS = {"runtime": lambda p: p["category"] == "runtime", "controlled_child": lambda p: p["category"] == "controlled_child", "combined": lambda p: True}
KNOWN_COMPONENTS = ("backend", "sidecar", "helper")
KNOWN_CATEGORIES = ("runtime", "controlled_child")
OWNERSHIP_LISTS = ("missingCleaned", "duplicateCleaned", "orphanCleaned", "duplicateOwned", "noRootIdentity", "sharedRootIdentity",
                   "mismatchedCleaned", "incompleteCleanup", "unownedTerminals", "unclaimedRoots")
# the frozen E1 frame-type names of wire v1 (none carries terminal output or input bytes): receipts may only name these
WIRE_V1_FRAME_TYPES = frozenset(name for name, value in vars(wire.code).items() if isinstance(value, int) and not name.startswith("_"))
MARKER_LINE_BYTES = ref.fixture_output_bytes()["start_line"]
FINAL_LINE_MAX = ref.fixture_output_bytes()["final_max"]


def is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def is_int(x: Any, lo: int = 0) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= lo


def p95(values: list[float]) -> float | None:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))] if ordered else None


def expected_vendors(count: int, repeat: int, vendors: list[str]) -> list[str]:
    return [vendors[(index + repeat) % len(vendors)] for index in range(count)]


def warmup_file_name(mode: str, index: int) -> str:
    """The receipt name (without folder or suffix) of the `index`-th discarded warm-up of an arm: `warmup-python-00`, `warmup-rust-00`."""
    return f"warmup-{'rust' if mode == 'rust-shell' else 'python'}-{index:02d}"


def trial_id(mode: str, cell: int, repeat: int) -> str:
    return f"{mode}:{cell}:{repeat}"


def expected_slots() -> list[tuple[str, int, int]]:
    return [(m, c, r) for m in MODES for c in CELLS for r in range(REPEATS)]


# ---------------------------------------------------------------------------------------------------------------- the freeze
REGISTERED_WORKLOAD = {"cells": [1, 4], "repeats": 10, "ioRounds": 1500, "idleMs": 10000, "burstRounds": {"1": 40, "4": 10}, "burstLines": 128, "burstWidth": 2048,
                       "resizeProbes": 20, "resourceIntervalMs": 50}


F0_SCHEMA = "acr-g1e-freeze/2"
F1_SCHEMA = "acr-g1e-freeze-f1/2"
RUN_SCHEMA = "acr-g1e-run/1"
F1_NAME = "freeze-f1.json"
RUN_NAME = "run.json"
#: E2 v13 section 4: the v10 text that the pre-registration chain (v14-r2 -> v13 -> v10) incorporates by hash; F0 names it beside the pre-registration's own digest.
V10_SHA256 = "aa69dbcea6d4c6ee248e99f50102dd5924cdcc970fd878461b03fdbc75d9523e"
#: E2 v13, now a bound text of the pre-registration (v14-r2 changes v13's rules where it says so); F0 binds its digest too.
V13_SHA256 = "e4cac3e9876d68a13f3e853054e5b8290ab9e63b2bfeed2437462e5b820193b0"
#: The registered digest of this pre-registration (E2 v14-r2, user-approved 2026-10-21), which F0 must bind by computing the digest of the file itself (`preregistration`).
V14R2_SHA256 = "67ab288132cbab8fae90d7e7ab2ffc93ee56c8224f3eaa43777aae87dca710a8"
RULE_FILES = {"v14r2": V14R2_SHA256, "v13": V13_SHA256, "v10": V10_SHA256, "e1v8b": ref.E1_BOUND_SHA256}
PREREGISTRATION_RULE = "v14r2"          # the RULE_FILES entry that is F0's `preregistration`
#: The items F1 re-verifies (V10 item 1: "F0's SHA-256 and a re-verification of every F0 item"), in order.
F0_ITEMS = ("preregistration", "e1Protocol", "schema", "fixtureRegistry", "logPolicy", "schedule", "workload", "options", "inputs", "head", "trackedDiff", "sidecar",
            "pythonArmFileList", "referenceManifest", "localityScope", "toolchain", "quietGate", "carriedVerdicts")
#: Where the frozen quiet-gate script and the locality allowlist live (F0 inputs, relative to the repository root).
QUIET_GATE_PATH = "backend/tests/cli_regression/support/g1e/quiet_gate.py"
ALLOWLIST_PATH = "backend/tests/cli_regression/support/g1e/locality-allowlist.json"
STOP_REASONS = ("toolchain-mismatch", "attempt-invalid", "freeze-mismatch", "operator-stop")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _hex(value: Any, size: int = 64) -> bool:
    return isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{size}}}", value) is not None


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def python_arm_problems(arm: Any) -> list[str]:
    """F0's Python-arm record (V5): the registered commits, the loaded-byte file list, `E3set` and the reference manifest, each consistent with its digest."""
    from tests.cli_regression.support.g1e import baseline_identity as bi
    keys = {"upstreamMerge", "e3Parent", "e3Commit", "e3Set", "fileList", "fileListSha256", "referenceManifest", "referenceManifestSha256"}
    if not isinstance(arm, dict) or set(arm) != keys:
        return ["freeze.pythonArm is not the V5 record"]
    out = []
    if (arm["upstreamMerge"], arm["e3Parent"], arm["e3Commit"]) != (bi.UPSTREAM_MERGE, bi.E3_PARENT, bi.E3_COMMIT):
        out.append("freeze.pythonArm names other commits than U, the E3 parent and the E3 commit fixed by v13")
    files, manifest, e3set = arm["fileList"], arm["referenceManifest"], arm["e3Set"]
    if not (isinstance(files, list) and files and all(isinstance(f, dict) and set(f) == {"path", "sha256"} and _text(f["path"]) and (_hex(f["sha256"]) or f["sha256"] == bi.ABSENT) for f in files)):
        return out + ["freeze.pythonArm.fileList is malformed"]
    paths = [f["path"] for f in files]
    if paths != sorted(set(paths)):
        out.append("freeze.pythonArm.fileList is not sorted and unique")
    if not _hex(arm["fileListSha256"]) or arm["fileListSha256"] != bi.manifest_sha256(files):
        out.append("freeze.pythonArm.fileListSha256 does not hash the file list")
    if not (isinstance(e3set, list) and e3set == sorted(set(e3set)) and set(e3set) <= set(paths)):
        out.append("freeze.pythonArm.e3Set is not a sorted subset of the file list")
        e3set = []
    if not (isinstance(manifest, list) and all(isinstance(m, dict) and set(m) == {"path", "sha256", "source"} for m in manifest) and [m["path"] for m in manifest] == paths):
        return out + ["freeze.pythonArm.referenceManifest is not one entry per file-list path"]
    for entry in manifest:
        want = arm["e3Parent"] if entry["path"] in set(e3set) else arm["upstreamMerge"]
        if entry["source"] != want or not (_hex(entry["sha256"]) or entry["sha256"] == bi.ABSENT):
            out.append(f"reference manifest entry {entry['path']} has the wrong source or digest (E3set files reference the E3 parent, all others U)")
    if not _hex(arm["referenceManifestSha256"]) or arm["referenceManifestSha256"] != bi.manifest_sha256(manifest):
        out.append("freeze.pythonArm.referenceManifestSha256 does not hash the manifest")
    return out


def toolchain_record_problems(toolchain: Any, uv: Any, sidecar_sha256: str) -> list[str]:
    """F0's toolchain identities (V7 item 1) and uv's informational record (V8)."""
    out = []
    triple = lambda x: isinstance(x, dict) and set(x) == {"realPath", "version", "sha256"} and _text(x["realPath"]) and _text(x["version"]) and _hex(x["sha256"])
    if not (isinstance(toolchain, dict) and set(toolchain) == {"node", "pnpm", "python", "sidecar"} and all(triple(toolchain[k]) for k in ("node", "pnpm", "python"))):
        return ["freeze.toolchain does not record node, pnpm and python as (real path, version, SHA-256) and the sidecar"]
    sidecar = toolchain["sidecar"]
    if not (isinstance(sidecar, dict) and set(sidecar) == {"realPath", "sha256", "readyVersion"} and _text(sidecar["realPath"]) and _hex(sidecar["sha256"]) and _text(sidecar["readyVersion"])):
        out.append("freeze.toolchain.sidecar is not (real path, SHA-256, ready-line version)")
    elif sidecar["sha256"] != sidecar_sha256:
        out.append("freeze.toolchain.sidecar.sha256 differs from freeze.sidecarSha256")
    if not (isinstance(uv, dict) and set(uv) == {"realPath", "version", "sha256"} and all(v is None or isinstance(v, str) for v in uv.values())):
        out.append("freeze.uv is not the informational (real path, version, SHA-256) record")
    return out


F0_DRYRUN_SCHEMA = "acr-g1e-freeze/2-dryrun"      # a dry-run F0 (no approval, never a decision): accepted only where a caller says `dryrun=True`, which no decision path does
LEDGER_RELATIVE = ".scratch/agent-cli-runtime-evaluation/progress.md"
APPROVAL_MIN_DATE = "2026-10-21"                    # the user's approval of v14-r2 (progress ledger "USER APPROVED E2 v14-r2"); an F0 approval cannot predate it (a v13 line is not F0's approval)
_FAKE_APPROVAL = re.compile(r"\b(smoke|fake|dry[ -]?run|dummy|synthetic|test(?:s|ing)?|not an approval|not a freeze)\b", re.IGNORECASE)


#: The user's approval entry in the progress ledger: ONE line, `- YYYY-MM-DD: USER APPROVED E2 v14-r2 ...`, which also quotes the registered v14-r2 digest.
_APPROVAL_LINE = re.compile(r"- (\d{4}-\d{2}-\d{2}): USER APPROVED E2 v14-r2 \S.*")


def approval_problems(approval: Any, ledger_text: str | None, *, ledger_path: Path | None, base: Path | None, today: datetime.date | None = None) -> list[str]:
    """F0's `preregistration.userApproval` must be the user's own dated approval entry (E2 v14-r2 approval of 2026-10-21), checked at VERIFICATION time against the ledger file actually read:
    * `ledger` names that file (`ledger_path`; `ledger` is a path relative to `base`, the repository root; the two are compared resolved);
    * `quote` is ONE WHOLE LINE of its text (`ledger_text`), exactly (a fragment of a line is not an entry);
    * that line has the form `- YYYY-MM-DD: USER APPROVED E2 v14-r2 ...` and carries the registered v14-r2 digest (a v13 approval line is not F0's approval);
    * `date` is a real ISO date (`datetime.date.fromisoformat`, written `YYYY-MM-DD`), equal to the line's own date, not before the v14-r2 approval (`APPROVAL_MIN_DATE`) and not after `today`
      (the verification date);
    * nothing in the record is marked smoke / test / fake / dry run / synthetic.
    A record that cannot show all of this is not an approval and its F0 is not an F0. (That a human wrote the line cannot be checked here; it is the ledger's trust.)"""
    if not (isinstance(approval, dict) and set(approval) == {"ledger", "date", "quote"} and all(isinstance(v, str) and v.strip() for v in approval.values())):
        return ["the approval record is not {ledger, date, quote}"]
    out = []
    if _FAKE_APPROVAL.search(approval["ledger"]) or _FAKE_APPROVAL.search(approval["quote"]):
        out.append("the approval is marked smoke / test / fake / dry run / synthetic: it is not an approval")
    if ledger_path is None or base is None or (Path(base) / approval["ledger"]).resolve() != Path(ledger_path).resolve():
        out.append(f"the approval names the ledger {approval['ledger']!r}, which is not the ledger file read ({LEDGER_RELATIVE})")
    if not isinstance(ledger_text, str):
        return out + ["the ledger cannot be read, so the approval cannot be found in it"]
    if approval["quote"] not in [line[:-1] if line.endswith("\r") else line for line in ledger_text.split("\n")]:
        return out + ["the approval quote is not one whole line of the ledger (a fragment of a line, or a line that is not there, is not an approval entry)"]
    line = _APPROVAL_LINE.fullmatch(approval["quote"])
    if line is None:
        out.append("the approved line is not of the form '- YYYY-MM-DD: USER APPROVED E2 v14-r2 ...': it is not the user's approval entry")
    if V14R2_SHA256 not in approval["quote"]:
        out.append(f"the approved line does not carry the registered v14-r2 digest {V14R2_SHA256[:12]}...")
    date = approval["date"]
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise ValueError(date)
        parsed = datetime.date.fromisoformat(date)
    except ValueError:
        out.append(f"the approval date {date!r} is not a date (YYYY-MM-DD)")
        return out
    if line is not None and line.group(1) != date:
        out.append(f"the approval date {date!r} is not the line's date ({line.group(1)})")
    if parsed < datetime.date.fromisoformat(APPROVAL_MIN_DATE):
        out.append(f"the approval date {date} is before the v14-r2 approval ({APPROVAL_MIN_DATE})")
    if parsed > (today or datetime.date.today()):
        out.append(f"the approval date {date} is after the verification date")
    return out


def locality_scope_record_problems(scope: Any) -> list[str]:
    """F0's frozen locality scope (E2 v13 section 4; `locality_check.scope_record`): a non-empty sorted list of unique repository-relative paths with the SHA-256 of each file, and the digest of that list."""
    if not (isinstance(scope, dict) and set(scope) == {"files", "sha256"}):
        return ["freeze.localityScope is not {files, sha256}"]
    files = scope["files"]
    if not (isinstance(files, list) and files and all(isinstance(f, dict) and set(f) == {"path", "sha256"} and _text(f["path"]) and _hex(f["sha256"]) for f in files)):
        return ["freeze.localityScope.files is not a non-empty list of {path, sha256}"]
    out = []
    paths = [f["path"] for f in files]
    if paths != sorted(set(paths)):
        out.append("freeze.localityScope.files is not sorted and unique")
    if any(Path(p).is_absolute() or ".." in Path(p).parts for p in paths):
        out.append("freeze.localityScope.files holds a path that is not repository-relative")
    if not _hex(scope["sha256"]) or scope["sha256"] != hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest():
        out.append("freeze.localityScope.sha256 does not hash the list")
    return out


def quiet_gate_record_problems(record: Any) -> list[str]:
    """F0's quiet-gate record (E2 v14-r2 V14 item 5): the frozen script `{path, sha256}` and the registered rule and parameters. The registered values ARE the constants of `quiet_gate.py`
    (no second copy of the numbers); the record must equal them exactly, type included. That the script's bytes are the recorded ones is `freeze.verify_freeze`'s check (it reads the tree)."""
    if not (isinstance(record, dict) and set(record) == {"script", "rule", "loadLimit", "intervalS", "maxWaitMin"}):
        return ["freeze.quietGate is not {script, rule, loadLimit, intervalS, maxWaitMin}"]
    out = []
    script = record["script"]
    if not (isinstance(script, dict) and set(script) == {"path", "sha256"} and script["path"] == QUIET_GATE_PATH and _hex(script["sha256"])):
        out.append(f"freeze.quietGate.script is not {{path: {QUIET_GATE_PATH}, sha256}}")
    registered = {"rule": quiet_gate.RULE, "loadLimit": quiet_gate.LIMIT, "intervalS": quiet_gate.INTERVAL_S, "maxWaitMin": quiet_gate.MAX_WAIT_S // 60}
    out += [f"freeze.quietGate.{key} is {record[key]!r}, not the registered {want!r}" for key, want in registered.items() if type(record[key]) is not type(want) or record[key] != want]
    return out


def carried_verdicts_problems(carried: Any, arm: Any) -> list[str]:
    """F0's carried verdicts (E2 v14-r2 V15 item 3), absent (`None`) or `{fromF0Sha256, pythonArm: [{path, loadedSha256, referenceSha256}], allowlist: {path, sha256} | None}`.
    Each Python-arm entry must be a LISTED file of this F0 (its loaded bytes differ from its reference) whose digests are exactly this F0's own record of it: a verdict carries only on byte equality, so
    an entry the F0 itself does not bear out is refused. The old/new equality was established where the record was built (`freeze.carried_verdicts`); the allowlist digest against the F0 inputs is
    `freeze.verify_freeze`'s check."""
    if carried is None:
        return []
    if not (isinstance(carried, dict) and set(carried) == {"fromF0Sha256", "pythonArm", "allowlist"}):
        return ["freeze.carriedVerdicts is neither null nor {fromF0Sha256, pythonArm, allowlist}"]
    out = []
    if not _hex(carried["fromF0Sha256"]):
        out.append("freeze.carriedVerdicts.fromF0Sha256 is not a SHA-256 digest")
    entries = carried["pythonArm"]
    if not (isinstance(entries, list) and all(isinstance(e, dict) and set(e) == {"path", "loadedSha256", "referenceSha256"} and all(isinstance(v, str) for v in e.values()) for e in entries)):
        out.append("freeze.carriedVerdicts.pythonArm is not a list of {path, loadedSha256, referenceSha256}")
    else:
        paths = [e["path"] for e in entries]
        if paths != sorted(set(paths)):
            out.append("freeze.carriedVerdicts.pythonArm is not sorted and unique")
        if isinstance(arm, dict) and not python_arm_problems(arm):
            loaded = {f["path"]: f["sha256"] for f in arm["fileList"]}
            reference = {m["path"]: m["sha256"] for m in arm["referenceManifest"]}
            for entry in entries:
                path = entry["path"]
                if path not in loaded:
                    out.append(f"freeze.carriedVerdicts carries a verdict for {path}, which is not a Python-arm file of this F0")
                elif loaded[path] == reference[path]:
                    out.append(f"freeze.carriedVerdicts carries a verdict for {path}, whose loaded bytes equal its reference (it is not a listed file)")
                elif (entry["loadedSha256"], entry["referenceSha256"]) != (loaded[path], reference[path]):
                    out.append(f"freeze.carriedVerdicts.{path} digests are not this F0's loaded and reference digests")
    allowlist = carried["allowlist"]
    if allowlist is not None and not (isinstance(allowlist, dict) and set(allowlist) == {"path", "sha256"} and allowlist["path"] == ALLOWLIST_PATH and _hex(allowlist["sha256"])):
        out.append(f"freeze.carriedVerdicts.allowlist is neither null nor {{path: {ALLOWLIST_PATH}, sha256}}")
    return out


def freeze_problems(freeze: Any, sha: str, *, dryrun: bool = False) -> list[str]:
    """The frozen run binding of one matrix (F0): every constant the decider relies on must be present and equal to the registered one. `dryrun` admits the dry-run schema id only."""
    problems: list[str] = []
    if not isinstance(freeze, dict):
        return ["freeze is not an object"]
    need = {"schema", "head", "branch", "sidecarSha256", "methodRevision", "evidenceRevision", "preregistration", "e1ProtocolSha256", "schemaSha256",
            "fixtureRegistrySha256", "logPolicySha256", "scheduleSeed", "scheduleSha256", "workload", "inputsSha256", "trackedDiffSha256", "options", "vendors",
            "earlierRuns", "pythonArm", "toolchain", "uv", "ruleFiles", "examples", "localityScope", "quietGate", "carriedVerdicts"}
    missing = sorted(need - set(freeze))
    extra = sorted(set(freeze) - need)
    if missing or extra:
        return [f"freeze keys differ (missing {missing}, unknown {extra})"]
    if freeze["schema"] != (F0_DRYRUN_SCHEMA if dryrun else F0_SCHEMA):
        problems.append("freeze schema id" + (" (a dry-run F0 is not an F0)" if freeze["schema"] == F0_DRYRUN_SCHEMA else ""))
    earlier = freeze["earlierRuns"]
    if not (isinstance(earlier, list) and all(_hex(d) for d in earlier) and len(set(earlier)) == len(earlier)):
        problems.append("freeze.earlierRuns is not a list of distinct run-manifest digests")
    rules = freeze["ruleFiles"]
    if not (isinstance(rules, dict) and set(rules) == set(RULE_FILES) and all(isinstance(rules[k], dict) and set(rules[k]) == {"path", "sha256"} and _text(rules[k]["path"]) for k in RULE_FILES)):
        problems.append("freeze.ruleFiles does not name the v14-r2, v13, v10 and E1 v8b files with their digests")
    else:
        for name, registered in RULE_FILES.items():
            if rules[name]["sha256"] != registered:
                problems.append(f"freeze.ruleFiles.{name} is not the registered digest {registered[:12]}...")
        pre = freeze["preregistration"]
        if isinstance(pre, dict) and (pre.get("sha256") != rules[PREREGISTRATION_RULE]["sha256"] or pre.get("document") != rules[PREREGISTRATION_RULE]["path"]):
            problems.append("freeze.preregistration is not the v14-r2 file that ruleFiles binds")
    if freeze["examples"] != ref.example_digests():
        problems.append("freeze.examples are not the digests of the synthetic examples in force (a changed example or checker)")
    problems += python_arm_problems(freeze["pythonArm"])
    problems += locality_scope_record_problems(freeze["localityScope"])
    problems += quiet_gate_record_problems(freeze["quietGate"])
    problems += carried_verdicts_problems(freeze["carriedVerdicts"], freeze["pythonArm"])
    problems += toolchain_record_problems(freeze["toolchain"], freeze["uv"], freeze["sidecarSha256"]) if _hex(freeze["sidecarSha256"]) else []
    hexes = {"sidecarSha256": 64, "e1ProtocolSha256": 64, "schemaSha256": 64, "fixtureRegistrySha256": 64, "logPolicySha256": 64, "inputsSha256": 64, "trackedDiffSha256": 64, "scheduleSha256": 64, "head": 40}
    for key, size in hexes.items():
        if not (isinstance(freeze[key], str) and re.fullmatch(f"[0-9a-f]{{{size}}}", freeze[key])):
            problems.append(f"freeze.{key} is not a {size}-hex digest")
    for key in ("branch", "methodRevision", "evidenceRevision", "scheduleSeed"):
        if not (isinstance(freeze[key], str) and freeze[key].strip()):
            problems.append(f"freeze.{key} is empty or not a string")
    if freeze["e1ProtocolSha256"] != ref.FROZEN["e1_sha256"]:
        problems.append("freeze binds another E1 protocol than the frozen v8b")
    if freeze["schemaSha256"] != ref.FROZEN["schema_sha256"]:
        problems.append("freeze binds another outcome schema than the frozen one")
    if freeze["fixtureRegistrySha256"] != ref.FROZEN["fixture_registry_sha256"] or freeze["fixtureRegistrySha256"] != ref.fixture_registry_digest():
        problems.append("fixture registry digest differs from the frozen/in-force one")
    if freeze["logPolicySha256"] != ref.FROZEN["log_policy_sha256"] or freeze["logPolicySha256"] != ref.log_policy_digest():
        problems.append("log policy digest differs from the frozen/in-force one")
    if freeze["workload"] != REGISTERED_WORKLOAD:
        problems.append("freeze workload differs from the registered quantities")
    if not (isinstance(freeze["vendors"], list) and freeze["vendors"] and all(isinstance(v, str) and v for v in freeze["vendors"])):
        problems.append("freeze.vendors is not a non-empty list of names")
    opts = freeze["options"]
    if not (isinstance(opts, dict) and set(opts) == {"readerRequired", "calibration", "c1Noisy", "noisyGuard"} and isinstance(opts["readerRequired"], bool)
            and opts["calibration"] in ref.CALIBRATION_MODES and (opts["readerRequired"] == (opts["calibration"] == "rows+reader"))):
        problems.append("freeze.options is not an executable option combination (D6 required iff calibration rows+reader; c1Noisy and noisyGuard, no cap)")
    elif {"c1Noisy": opts["c1Noisy"], "noisyGuard": opts["noisyGuard"]} != ref.noisy_options(opts["calibration"]) or type(opts["noisyGuard"]) not in (int, str):
        problems.append("freeze.options carries a noisy option other than the registered constants (v14-r2: C1 option b, guard 8, 'n/a' without calibration)")
    pre = freeze["preregistration"]
    if not (isinstance(pre, dict) and set(pre) == {"document", "sha256", "baseSha256", "userApproval"} and isinstance(pre["document"], str) and pre["document"]
            and isinstance(pre["sha256"], str) and re.fullmatch("[0-9a-f]{64}", pre["sha256"]) and isinstance(pre["userApproval"], dict)):
        problems.append("freeze.preregistration is malformed")
    elif pre["baseSha256"] != V10_SHA256:
        problems.append("freeze.preregistration.baseSha256 is not the v10 text the pre-registration chain (v14-r2, v13) incorporates by hash")
    if not problems:
        schedule = ref.make_schedule(freeze["scheduleSeed"])
        if ref.schedule_sha(schedule) != freeze["scheduleSha256"]:
            problems.append("freeze scheduleSha256 is not the SHA-256 of the schedule reproduced from its seed")
    return problems


READER_FIXTURES = ("claude", "opencode")
READER_METRICS = ("m1_wall_ms", "m2_cpu_s", "m3_peak_rss_mib")


def f1_problems(f1: Any, f0_sha: str, freeze: dict) -> list[str]:
    """F1 (V10 items 1-3): F0's SHA-256, a re-verification of every F0 item, the calibration receipts (the warm-up among them) with the noisy and the uncalibrated rows, and the absolute reader margins."""
    keys = {"schema", "f0Sha256", "reverified", "calibration", "readerMargins"}
    if not isinstance(f1, dict) or set(f1) != keys:
        return ["F1 is not the F1 record"]
    out = []
    if f1["schema"] != F1_SCHEMA:
        out.append("F1 schema id")
    if f1["f0Sha256"] != f0_sha:
        out.append("F1 names another F0 than the one it is bound to")
    rv = f1["reverified"]
    if not (isinstance(rv, dict) and set(rv) == {"ok", "items"} and rv["ok"] is True and rv["items"] == list(F0_ITEMS)):
        out.append("F1 does not record a passing re-verification of every F0 item")
    cal = f1["calibration"]
    row_ids = {s["id"] for s in ref.REG}
    lists = ("noisyRows", "uncalibratedRows")
    if not (isinstance(cal, dict) and set(cal) == {"receipts", *lists} and isinstance(cal["receipts"], list)
            and all(isinstance(r, dict) and set(r) == {"path", "sha256"} and _text(r["path"]) and _hex(r["sha256"]) for r in cal["receipts"])
            and all(isinstance(cal[k], list) and all(isinstance(x, str) for x in cal[k]) and len(set(cal[k])) == len(cal[k]) and set(cal[k]) <= row_ids for k in lists)):
        out.append("F1.calibration is not {receipts, noisyRows, uncalibratedRows}")
    elif freeze["options"]["calibration"] == "none" and (cal["receipts"] or cal["noisyRows"] or cal["uncalibratedRows"]):
        out.append("F1 carries calibration output although the freeze asks for none")
    elif set(cal["noisyRows"]) & set(cal["uncalibratedRows"]):
        out.append("F1.calibration lists a row as both noisy and uncalibrated")
    elif freeze["options"]["calibration"] != "none":
        # v14-r2 V19: ten scored Python attempts per cell (the two chains) and ONE discarded warm-up before the matrix; V10 item 4: an incomplete calibration gives no F1. (The decider re-judges each receipt; this is the static floor.)
        cells = {1: set(), 4: set()}
        warmups = []
        for receipt in cal["receipts"]:
            found = re.fullmatch(r"calibration/trial-python-(1|4)-([0-9])\.json", receipt["path"])
            if found:
                cells[int(found.group(1))].add(int(found.group(2)))
            elif re.fullmatch(r"calibration/warmup-python-[0-9]{2}\.json", receipt["path"]):
                warmups.append(receipt["path"])
        if any(len(attempts) < 10 for attempts in cells.values()):
            out.append("F1 lists fewer than ten distinct Python calibration attempts in a cell (v14-r2 V19; an incomplete calibration gives no F1)")
        if len(warmups) != 1:
            out.append(f"F1 lists {len(warmups)} calibration warm-up receipts; v14-r2 V19 runs exactly one discarded warm-up")
    margins = f1["readerMargins"]
    if freeze["options"]["readerRequired"]:
        if not (isinstance(margins, dict) and set(margins) == set(READER_FIXTURES)
                and all(isinstance(margins[fx], dict) and set(margins[fx]) == set(READER_METRICS) and all(is_num(v) and v > 0 for v in margins[fx].values()) for fx in READER_FIXTURES)):
            out.append("F1.readerMargins are not the absolute margins per fixture and metric")
    elif margins is not None:
        out.append("F1 carries reader margins although the reader is not required")
    return out


def run_problems(run: Any) -> list[str]:
    """`run.json`: what a runner may persist outside the sealed receipts (blindness, V7 item 6): per-attempt validity and the stop reason, never a measured value."""
    if not isinstance(run, dict) or set(run) != {"schema", "kind", "attempts", "stopReason", "complete"} or run["schema"] != RUN_SCHEMA:
        return ["run.json is not an acr-g1e-run/1 record"]
    out = []
    if run["kind"] not in ref.RUN_KINDS:
        out.append("run.json names an unknown run kind")
    if run["stopReason"] is not None and run["stopReason"] not in STOP_REASONS:
        out.append("run.json names an unknown stop reason")
    attempts = run["attempts"]
    if not (isinstance(attempts, list) and all(isinstance(a, dict) and set(a) == {"name", "validity", "stopReason"} and _text(a["name"]) and a["validity"] in ("clean", "invalid")
                                               and (a["stopReason"] is None or a["stopReason"] in STOP_REASONS) for a in attempts)):
        return out + ["run.json attempts are malformed"]
    if not isinstance(run["complete"], bool):
        out.append("run.json complete is not a boolean")
    elif run["complete"] and (run["stopReason"] is not None or any(a["validity"] != "clean" for a in attempts)):
        out.append("run.json is complete although it records a stop or an invalid attempt")
    elif not run["complete"] and run["stopReason"] is None:
        out.append("run.json is incomplete without a stop reason")
    if any(a["validity"] == "invalid" for a in attempts) and (run["stopReason"] is None or attempts[-1]["validity"] != "invalid"):
        out.append("run.json: an invalid attempt must stop the run and be its last attempt")
    return out


CHECK_KEYS = {"recorded", "observed", "match", "mismatches", "uv"}


def check_record_problems(record: Any, freeze: dict, extra_keys: frozenset | set = frozenset()) -> list[str]:
    """One per-run toolchain check record (`toolchain.check`): the recorded identities are F0's, the observed ones equal them (RECOMPUTED here, the declared `match` is never trusted),
    and the informational uv record is present. Used for every attempt receipt and for every reader run (V7 item 2)."""
    from tests.cli_regression.support.g1e import toolchain as tc
    if not isinstance(record, dict) or set(record) != CHECK_KEYS | set(extra_keys):
        return ["the toolchain identity record is missing or malformed (V7 item 2)"]
    frozen = freeze["toolchain"]
    out = []
    # every attempt, either arm, checks the sidecar's real path and SHA-256 before it starts (V7 item 2); the ready-line version only matters where a sidecar runs
    scope = {k: frozen[k] for k in ("node", "pnpm", "python")} | {"sidecar": {"realPath": frozen["sidecar"]["realPath"], "sha256": frozen["sidecar"]["sha256"]}}
    if record["recorded"] != scope:
        out.append("the toolchain record names other recorded identities than F0's")
    observed = record["observed"]
    if not isinstance(observed, dict):
        return out + ["the toolchain record has no observed identities"]
    if tc.compare(scope, observed) or record["match"] is not True or record["mismatches"] != []:
        out.append("toolchain-mismatch: an observed identity differs from F0's, or the match was not recorded")
    uv = record["uv"]
    if not (isinstance(uv, dict) and set(uv) == {"realPath", "version", "sha256"} and all(v is None or isinstance(v, str) for v in uv.values())):
        out.append("the uv record is malformed (informational, but it must be present)")
    return out


def reader_toolchain_problems(records: Any, freeze: dict, expected: int, label: str) -> list[str]:
    """`expected` check records, one per reader run, each valid (V7 item 2: a reader-calibration or reader-measure run checks the toolchain before it starts)."""
    if not isinstance(records, list) or len(records) != expected:
        return [f"{label}: expected {expected} toolchain check records, one per run"]
    return [f"{label}[{index}]: {m}" for index, record in enumerate(records) for m in check_record_problems(record, freeze)]


def trial_toolchain_problems(trial: dict, freeze: dict, mode: str) -> list[str]:
    """V7 item 2: the attempt's receipt carries the observed identities and the match result. The match is RECOMPUTED here from the recorded and observed identities, never read."""
    record = trial.get("toolchain")
    if not isinstance(record, dict) or set(record) != CHECK_KEYS | {"readyVersion"}:
        return ["the receipt carries no toolchain identity record (V7 item 2)"]
    frozen = freeze["toolchain"]
    out = check_record_problems(record, freeze, {"readyVersion"})
    ready = record["readyVersion"]
    if mode == "rust-shell":
        want = frozen["sidecar"]["readyVersion"]
        if not (isinstance(ready, dict) and set(ready) == {"recorded", "observed", "match"} and ready["recorded"] == want and ready["observed"] == want and ready["match"] is True):
            out.append("toolchain-mismatch: the ready-line version of the Rust-arm attempt is not F0's")
    elif ready is not None:
        out.append("a python-arm attempt records a ready-line version")
    return out


# ------------------------------------------------------------------------------------------------------ per-trial validation
def _ownership_envelope_problems(proof: dict, mode: str) -> list[str]:
    out = []
    flag, ownership = proof.get("ownershipFailure"), proof.get("ownership")
    if not isinstance(flag, bool):
        out.append("proof ownershipFailure is not a boolean")
    if mode == "python":
        if ownership is not None:
            out.append("python proof carries an ownership record")
        if flag is not False:
            out.append("python proof ownershipFailure is not false")
        return out
    if not isinstance(ownership, dict) or set(ownership) != set(OWNERSHIP_LISTS) or not all(isinstance(ownership[k], list) for k in OWNERSHIP_LISTS):
        out.append("rust proof lacks the complete ownership record (ten lists)")
        return out
    anything = any(ownership[k] for k in OWNERSHIP_LISTS)
    if isinstance(flag, bool) and flag != anything:
        out.append("proof ownershipFailure disagrees with its ownership lists")
    if proof.get("ok") is True and anything:
        out.append("proof is inconsistent (ok although it records an ownership failure)")
    return out


def consistency_problems(trial: dict) -> list[str]:
    out = []
    valid, passed, proof = trial.get("evidenceValid"), trial.get("passed"), trial.get("proof")
    if not isinstance(valid, bool) or not isinstance(passed, bool):
        return ["evidenceValid/passed are not booleans"]
    if not isinstance(trial.get("harnessRescued"), bool):
        out.append("harnessRescued is missing or not a boolean")
    proof_ok = None
    if proof is not None:
        if not isinstance(proof, dict) or not isinstance(proof.get("ok"), bool) or not isinstance(proof.get("failures"), list):
            out.append("proof record is malformed")
        else:
            proof_ok = proof["ok"]
            if proof_ok != (len(proof["failures"]) == 0):
                out.append("proof is inconsistent (ok does not match its failures list)")
            if not isinstance(proof.get("facts"), dict):
                out.append("proof record is malformed (facts)")
            elif proof["facts"].get("mode") != trial.get("selectedRuntime"):
                out.append("proof belongs to another runtime arm")
            out += _ownership_envelope_problems(proof, trial.get("selectedRuntime"))
    if valid:
        if proof is None:
            out.append("evidenceValid=true without a proof")
        elif proof_ok is not True:
            out.append("evidenceValid=true although the proof failed")
        if (trial.get("phaseOutcomes") or {}).get("cleanup") != "passed":
            out.append("evidenceValid=true although the driver cleanup did not pass")
        if trial.get("harnessRescued") is True:
            out.append("evidenceValid=true after a harness rescue")
    if passed and not valid:
        out.append("passed=true without valid evidence")
    return out


def _rows_in_phase(phases: list[dict], samples: list[dict], name: str) -> list[dict]:
    index = next((i for i, phase in enumerate(phases) if phase["name"] == name), None)
    if index is None:
        return []
    start = phases[index]["wallMs"]
    stop = phases[index + 1]["wallMs"] if index + 1 < len(phases) else float("inf")
    return [s for s in samples if start <= s["wallMs"] < stop]


DECISION_RESOURCE_PHASES = ("empty_idle", "live_idle", "empty_started_idle", "input_output")
ACTIVE_RESOURCE_PHASES = ("input_output",)


def coverage_problems(samples: list[dict], phases: list[dict], outcomes: dict, mode: str) -> list[str]:
    out = []
    if mode == "python" and any(p["component"] == "sidecar" for row in samples for p in row["processes"]):
        out.append("resources: the python arm shows a sidecar component")
    for name in DECISION_RESOURCE_PHASES:
        if outcomes.get(name) != "passed":
            continue
        rows = _rows_in_phase(phases, samples, name)
        if len(rows) < RESOURCE_MIN_SAMPLES:
            continue
        if any(not any(p["component"] == "backend" for p in row["processes"]) for row in rows):
            out.append(f"resources: the backend is missing from rows of the eligible {name} phase")
        if mode == "rust-shell" and name in ACTIVE_RESOURCE_PHASES and any(not any(p["component"] == "sidecar" for p in row["processes"]) for row in rows):
            out.append(f"resources: the sidecar is missing from rows of the eligible {name} phase")
    return out


def burst_rounds(work: dict, cell: int) -> int:
    """The burst rounds of ONE peer in `cell` (v14-r2 V17: cell 1 = 40, cell 4 = 10); `work` is a workload record (`REGISTERED_WORKLOAD` or the freeze's copy of it)."""
    return work["burstRounds"][str(cell)]


def required_latency_counts(count: int, io_rounds: int, burst_rounds: int, vendors_claude: int) -> dict[str, tuple[str, int]]:
    """key -> (phase, samples a passed phase must hold). Per-peer keys have `count` samples, burst keys `count * burst_rounds` (`burst_rounds` of that cell: 40 samples in both cells)."""
    return {
        "cold_backend_ready_ms": ("cold_startup", 1), "first_shell_ack_ms": ("shell_startup", 1), "first_shell_ready_ms": ("shell_startup", 1),
        "warm_shell_ack_ms": ("shell_startup", 1), "warm_shell_ready_ms": ("shell_startup", 1), "cold_to_first_usable_ms": ("shell_startup", 1),
        "agent_ack_ms": ("agent_startup", count), "agent_ready_ms": ("agent_startup", count), "io_ms": ("input_output", count * io_rounds),
        "burst_input_ms": ("burst", count * burst_rounds), "burst_ms": ("burst", count * burst_rounds),
        "reconnect_io_ms": ("reconnect", count), "reconnect_recovery_ms": ("reconnect", 1), "resize_ack_ms": ("resize_ack", 20),
        "kill_reap_ms": ("kill", 1), "persistence_restart_ms": ("persistence_restart", 1),
    }


AMBIENT_POINTS = ("ambientBefore", "ambientAfter")


def ambient_problems(trial: dict) -> list[str]:
    """v14-r2 V19 item 5 (the host record): a receipt carries `warmup` (a boolean) and, before and after the attempt, numeric host numbers: `at`, three `loadavg` values, `ncpu` and `processCount`.
    They are information only: no outcome, status or classification reads them (the decider ignores everything in this function but its verdict on the shape)."""
    out = []
    if not isinstance(trial.get("warmup"), bool):
        out.append("warmup is missing or not a boolean")
    for point in AMBIENT_POINTS:
        ambient = trial.get(point)
        load = ambient.get("loadavg") if isinstance(ambient, dict) else None
        if not (isinstance(ambient, dict) and is_num(ambient.get("at")) and ambient["at"] >= 0 and isinstance(load, list) and len(load) == 3 and all(is_num(x) and x >= 0 for x in load)
                and is_int(ambient.get("ncpu"), 1) and is_int(ambient.get("processCount"), 1)):
            out.append(f"{point} (ambient host numbers) is missing or not numeric: at, loadavg[3], ncpu, processCount")
    return out


def structure_problems(trial: dict, freeze: dict, bundle: Bundle, resources_name: str) -> tuple[list[str], list[dict]]:
    out: list[str] = []
    count, repeat = trial.get("count"), trial.get("repeat")
    work = freeze["workload"]
    latencies = trial.get("latencies")
    if not isinstance(latencies, dict) or not all(isinstance(v, list) and all(is_num(x) and x >= 0 for x in v) for v in latencies.values()):
        out.append("latencies is missing or holds non-numeric, negative or non-finite values")
        latencies = None
    phases = trial.get("phases")
    names_ok = isinstance(phases, list) and tuple(p.get("name") if isinstance(p, dict) else None for p in phases) == PHASE_SEQUENCE
    if not names_ok or not all(is_num(p.get("wallMs")) for p in phases) or any(b["wallMs"] < a["wallMs"] for a, b in zip(phases, phases[1:])):
        out.append("phases is missing, malformed, out of sequence or not time-ordered")
    outcomes = trial.get("phaseOutcomes")
    if not isinstance(outcomes, dict) or set(outcomes) != set(PHASE_SEQUENCE) | {"cleanup"} or any(v not in PHASE_OUTCOME_VALUES for v in outcomes.values()):
        out.append("phaseOutcomes is missing, incomplete or holds an unknown outcome")
        outcomes = None
    if latencies is not None and outcomes is not None and isinstance(count, int) and str(count) not in work["burstRounds"]:
        out.append("count is not a registered cell")
    elif latencies is not None and outcomes is not None and isinstance(count, int):
        for key, (phase, samples) in required_latency_counts(count, work["ioRounds"], burst_rounds(work, count), 0).items():
            if outcomes.get(phase) == "passed" and len(latencies.get(key, [])) != samples:
                out.append(f"latencies[{key}] does not have the {samples} samples a passed {phase} phase records")
    observed = trial.get("observationOutcomes")
    if not isinstance(observed, list) or not all(isinstance(o, dict) and all(isinstance(o.get(k), str) and o.get(k) for k in ("pane", "vendor", "kind", "status")) for o in observed):
        out.append("observationOutcomes is missing or malformed")
    peers = trial.get("peerIdentities")
    if not isinstance(peers, list) or not all(isinstance(p, dict) and isinstance(p.get("vendor"), str) and isinstance(p.get("pane"), str) and p["pane"] for p in peers):
        out.append("peerIdentities is missing or malformed")
    elif isinstance(count, int) and isinstance(repeat, int) and sorted(p["vendor"] for p in peers) != sorted(expected_vendors(count, repeat, freeze["vendors"])):
        out.append("peerIdentities does not match the frozen vendor rotation for this slot")
    if isinstance(observed, list) and isinstance(peers, list) and isinstance(outcomes, dict) and outcomes.get("finished") == "passed" \
            and all(isinstance(o, dict) for o in observed) and all(isinstance(p, dict) and isinstance(p.get("pane"), str) for p in peers):
        for peer in peers:
            if peer.get("vendor") != "claude":
                continue
            for kind in ("activity", "live_totals"):
                if not any(o.get("pane") == peer["pane"] and o.get("kind") == kind for o in observed):
                    out.append(f"observationOutcomes has no {kind} outcome for claude peer {peer['pane'][:8]} although the run finished")
    if not is_int(trial.get("freezeCheckedInputs"), 1):
        out.append("freezeCheckedInputs does not cover the frozen inputs")
    out += ambient_problems(trial)
    samples: list[dict] = []
    if not bundle.exists(resources_name):
        out.append("resources file is missing")
    else:
        try:
            for row in bundle.read_jsonl(resources_name):
                ok = isinstance(row, dict) and is_num(row.get("wallMs")) and is_num(row.get("monotonicNs")) and isinstance(row.get("processes"), list)
                for proc in (row.get("processes") or []) if ok else []:
                    ok = ok and isinstance(proc, dict) and all(is_num(proc.get(k)) for k in ("pid", "created", "cpuSeconds", "rssBytes")) \
                        and proc["pid"] > 0 and proc["created"] >= 0 and proc["cpuSeconds"] >= 0 and proc["rssBytes"] >= 0 \
                        and proc.get("category") in KNOWN_CATEGORIES and proc.get("component") in KNOWN_COMPONENTS
                if not ok:
                    raise ValueError("malformed sample row")
                samples.append(row)
        except (ValueError, BundleError, UnicodeDecodeError):
            out.append("resources file is malformed (row shape, known component/category names, non-negative numbers)")
            samples = []
        if not samples and not any("malformed" in p for p in out):
            out.append("resources file is empty")
        elif samples and isinstance(phases, list) and isinstance(outcomes, dict) and not any("phases" in p or "phaseOutcomes" in p for p in out):
            out += coverage_problems(samples, phases, outcomes, trial.get("selectedRuntime"))
    return out, samples


# ------------------------------------------------------------------------------- recomputed validity (E2 v10 section 3)
def _record(rec: Any, fields: dict, path: str) -> list[str]:
    if not isinstance(rec, dict):
        return [f"{path} is not a record"]
    missing, extra = sorted(set(fields) - set(rec)), sorted(set(rec) - set(fields))
    if missing or extra:
        return [f"{path} keys differ (missing {missing}, unknown {extra})"]
    out = []
    for key, check in fields.items():
        if not check(rec[key]):
            out.append(f"{path}.{key} has the wrong type or range")
    return out


def _b(x):
    return isinstance(x, bool)


def _i(x):
    return is_int(x)


def _h(x):
    return isinstance(x, str) and bool(re.fullmatch("[0-9a-f]{64}", x))


def _wid_set(peer: int, cell: int) -> set[str]:
    return {f"i{peer}"} | {f"b{peer}r{k:02d}" for k in range(burst_rounds(REGISTERED_WORKLOAD, cell))}


COUNTER = {"read_total": _i, "emitted_total": _i, "dropped_oldest_total": _i}


def ledger_problems(rec: Any, peer_index: int, cell: int) -> list[str]:
    """E2 v10 section 3 item 4 c-e and g, recomputed from one session's ledger record (the equalities, the bounds, the window digests; the window ids are the cell's rounds, v14-r2 V17)."""
    shape = {
        "session": lambda x: isinstance(x, str) and bool(x), "pane": lambda x: isinstance(x, str) and bool(x), "generation": _i, "peerIndex": _i, "nonceSha256": _h,
        "rawMode": lambda x: isinstance(x, dict) and set(x) == {"opost", "onlcr", "echo"} and all(_b(v) for v in x.values()),
        "origin": lambda x: isinstance(x, dict) and set(x) == {"read_total", "emitted_total", "dropped_oldest_total", "stable", "peerAcceptedAtOrigin"} and all(_i(x[k]) for k in COUNTER) and _b(x["stable"]) and _i(x["peerAcceptedAtOrigin"]),
        "close": lambda x: isinstance(x, dict) and set(x) == set(COUNTER) and all(_i(x[k]) for k in COUNTER),
        "peer": lambda x: isinstance(x, dict) and set(x) == {"acceptedSinceOrigin", "finalLineBytes", "totalDigest", "writeFailed", "windows"} and _i(x["acceptedSinceOrigin"]) and _i(x["finalLineBytes"]) and _h(x["totalDigest"]) and _b(x["writeFailed"]) and isinstance(x["windows"], list),
        "stream": lambda x: isinstance(x, dict) and set(x) == {"markerCount", "finalSeen", "finalAccepted", "finalDigest", "windows"} and _i(x["markerCount"]) and _b(x["finalSeen"]) and _i(x["finalAccepted"]) and _h(x["finalDigest"]) and isinstance(x["windows"], list),
        "logIdentity": lambda x: isinstance(x, dict) and set(x) == {"startMarkerCount", "finalPresent", "textEqual", "windowsEqual", "preOriginLogBytes"} and _i(x["startMarkerCount"]) and _b(x["finalPresent"]) and _b(x["textEqual"]) and _b(x["windowsEqual"]) and _i(x["preOriginLogBytes"]),
        "logFile": lambda x: isinstance(x, dict) and set(x) == {"regular", "dev", "ino", "inodeAtCreate", "size", "siblingExists", "logSentUsed"} and _b(x["regular"]) and _i(x["dev"]) and _i(x["ino"]) and isinstance(x["inodeAtCreate"], list) and len(x["inodeAtCreate"]) == 2 and all(_i(v) for v in x["inodeAtCreate"]) and _i(x["size"]) and _b(x["siblingExists"]) and _b(x["logSentUsed"]),
    }
    shape_problems = _record(rec, shape, "ledger")
    if shape_problems:
        return shape_problems
    out = []
    if rec["peerIndex"] != peer_index:
        out.append("ledger peerIndex does not match its position")
    if any(rec["rawMode"].values()):
        out.append("peer slave not raw (OPOST/ONLCR/ECHO set)")
    o, c, p, s, li, lf = rec["origin"], rec["close"], rec["peer"], rec["stream"], rec["logIdentity"], rec["logFile"]
    if not o["stable"]:
        out.append("origin counters were not stable across two reads")
    if o["read_total"] < MARKER_LINE_BYTES or o["read_total"] - MARKER_LINE_BYTES > ref.PRE_ORIGIN_READ_MAX:
        out.append("pre-origin read bytes outside [marker line, marker line + PRE_ORIGIN_READ_MAX]")
    delta = {k: c[k] - o[k] for k in COUNTER}
    if any(v < 0 for v in delta.values()):
        out.append("a counter went backwards between the origin and the close")
    if delta["read_total"] != p["acceptedSinceOrigin"]:
        out.append("delta read_total != peer accepted since origin (including the FINAL line)")
    if delta["read_total"] != delta["emitted_total"] + delta["dropped_oldest_total"]:
        out.append("delta read_total != delta emitted + delta dropped_oldest")
    if delta["dropped_oldest_total"] != 0:
        out.append("drop-oldest fired in a measured session")
    if p["writeFailed"] or p["acceptedSinceOrigin"] > ref.PEER_MAX_BYTES:
        out.append("peer write failure or peer over its byte limit")
    if p["finalLineBytes"] <= 0 or p["finalLineBytes"] > FINAL_LINE_MAX:
        out.append("FINAL line length outside its registered maximum")
    if s["markerCount"] != 1:
        out.append("the start marker did not occur exactly once on the renderer stream")
    if not s["finalSeen"]:
        out.append("the FINAL line was not seen on the renderer stream")
    if s["finalAccepted"] + p["finalLineBytes"] != p["acceptedSinceOrigin"]:
        out.append("FINAL accepted count plus the FINAL line is not the peer's accepted-since-origin total")
    if s["finalDigest"] != p["totalDigest"]:
        out.append("FINAL digest differs from the peer's total digest")
    pw = {w.get("wid"): w for w in p["windows"] if isinstance(w, dict)}
    sw = {w.get("wid"): w for w in s["windows"] if isinstance(w, dict)}
    expected = _wid_set(peer_index, cell)
    if len(pw) != len(p["windows"]) or len(sw) != len(s["windows"]) or set(pw) != expected or set(sw) != expected:
        out.append(f"the window id sets of the peer and of the stream are not exactly the registered {len(expected)} windows")
    else:
        for wid in expected:
            a, b = pw[wid], sw[wid]
            if not (isinstance(a.get("digest"), str) and a.get("digest") == b.get("digest") and _h(a.get("digest"))):
                out.append(f"window {wid}: renderer-stream digest differs from the peer's digest")
    if li["startMarkerCount"] != 1 or not li["finalPresent"] or not li["textEqual"] or not li["windowsEqual"]:
        out.append("log text identity failed (marker not once, FINAL missing, or whitespace-insensitive text differs)")
    if li["preOriginLogBytes"] > 3 * ref.PRE_ORIGIN_READ_MAX:
        out.append("pre-origin log text above its registered bound")
    if not lf["regular"] or lf["siblingExists"] or lf["size"] > ref.LOG_LIMIT_BYTES or [lf["dev"], lf["ino"]] != lf["inodeAtCreate"]:
        out.append("log file irregular, replaced, rotated (sibling), or over the limit")
    if lf["logSentUsed"]:
        out.append("terminal.log_sent was used in a measured session")
    return out


def log_final_problems(rec: Any, arm: str) -> list[str]:
    if not isinstance(rec, dict):
        return ["log final record is not an object"]
    fields = {"arm", "sidecar_live", "inventory", "reads", "t_exit_ms", "t_inventory_ms"}
    if set(rec) != fields:
        return ["log final record keys differ from the checker's arguments"]
    if rec["arm"] != arm:
        return ["log final record belongs to another arm"]
    verdict = ref.log_final_check(**rec)
    return [] if verdict.ok else [f"log final check: {verdict.kind}: {verdict.why}"]


def ck_proof_problems(trial: dict, mode: str) -> list[str]:
    """E2 v10 section 3 item 2 (per-create kernel checkpoint accounting), as collected by the driver's `trial_proof.prove`: required on the Rust arm (every create has exactly one CK or one failed-create
    receipt, none fails), and the known-empty record on the Python arm. A missing, null or failing record is a problem; nothing is assumed."""
    ck = trial.get("ckProof")
    if not isinstance(ck, dict) or not isinstance(ck.get("ok"), bool) or not isinstance(ck.get("failures"), list) or not isinstance(ck.get("applicable"), bool):
        return ["the CK proof record is missing or malformed"]
    if mode == "python":
        return [] if ck["applicable"] is False and ck["ok"] is True and ck["failures"] == [] and ck.get("items") is None else ["the Python arm must carry the known-empty CK record"]
    out = []
    if ck["applicable"] is not True or ck["ok"] is not True or ck["failures"] != []:
        out.append("the CK proof failed or is not applicable on the Rust arm")
    items, created = ck.get("items"), trial.get("createdTerminals")
    two = items.get("2") if isinstance(items, dict) else None
    if not (isinstance(two, dict) and is_int(two.get("ck")) and is_int(two.get("failed_creates")) and isinstance(created, list)):
        out.append("the CK accounting (items 2) or the created-terminal list is missing")
    elif two["ck"] + two["failed_creates"] != len(created):
        out.append("the CK accounting does not cover exactly the terminals the driver created")
    return out


def trial_problems(trial: dict, freeze: dict, mode: str, count: int) -> list[str]:
    """Everything the decider recomputes about one admitted trial; empty means the trial's evidence is valid."""
    out = []
    if trial.get("evidenceValid") is not True:
        out.append("the producer marked the evidence invalid")
    if trial.get("passed") is not True:
        out.append("the trial did not pass")
    if trial.get("harnessRescued") is not False:
        out.append("harness rescue")
    if (trial.get("phaseOutcomes") or {}).get("cleanup") != "passed" or (trial.get("phaseOutcomes") or {}).get("finished") != "passed":
        out.append("cleanup or the finished phase did not pass")
    proof = trial.get("proof")
    if not isinstance(proof, dict) or proof.get("ok") is not True:
        out.append("the runtime proof is missing or failed")
    elif mode == "rust-shell" and any((proof.get("ownership") or {}).get(k) for k in OWNERSHIP_LISTS):
        out.append("a Rust-arm ownership failure")
    out += ck_proof_problems(trial, mode)
    out += trial_toolchain_problems(trial, freeze, mode)
    if trial.get("fixtureRegistrySha256") != ref.FROZEN["fixture_registry_sha256"] or trial.get("logPolicySha256") != ref.FROZEN["log_policy_sha256"]:
        out.append("the trial was produced under another fixture registry or log policy than the frozen ones")
    start, end = trial.get("backend"), trial.get("backendEnd")
    ident = {"pid": lambda x: is_int(x, 1), "start": lambda x: isinstance(x, str) and bool(x.strip())}
    id_problems = _record(start, ident, "backend") + _record(end, ident, "backendEnd")
    if id_problems:
        out += id_problems
    elif start != end:
        out.append("isolation: the backend pid/start time at the end differs from the start")
    ledgers, finals = trial.get("ledgers"), trial.get("logFinals")
    if not isinstance(ledgers, list) or len(ledgers) != count:
        out.append(f"expected {count} byte-ledger records, one per peer")
    else:
        for index, rec in enumerate(ledgers):
            out += [f"ledger[{index}]: {m}" for m in ledger_problems(rec, index, count)]
    if not isinstance(finals, list) or len(finals) != count:
        out.append(f"expected {count} log final records, one per peer")
    else:
        for index, rec in enumerate(finals):
            out += [f"logFinal[{index}]: {m}" for m in log_final_problems(rec, mode)]
    bursts = trial.get("burstChecks")
    if not isinstance(bursts, list) or len(bursts) != count or not all(isinstance(b, dict) and b.get("kind") == "pass" and b.get("rounds") == burst_rounds(REGISTERED_WORKLOAD, count) for b in bursts):
        out.append("a burst endpoint check is missing or not a pass for every peer")
    if "controlLedger" not in trial:
        out.append("the control-plane ledger field is absent (a known-empty null is required on the python arm)")
    control = trial.get("controlLedger")
    if mode == "python":
        if control is not None:
            out.append("the python arm carries a control-plane ledger")
    else:
        if not isinstance(control, dict) or set(control) != {"tx", "rx", "unknownTypes", "controlBytes", "streamBytes"}:
            out.append("control-plane ledger missing or malformed")
        else:
            counts_ok = all(isinstance(control[d], dict) and all(isinstance(k, str) and is_int(v) for k, v in control[d].items()) for d in ("tx", "rx"))
            if not counts_ok or not is_int(control["controlBytes"]) or not is_int(control["streamBytes"]) or not isinstance(control["unknownTypes"], list):
                out.append("control-plane ledger counts are not non-negative integers")
            elif control["unknownTypes"] != [] or not (set(control["tx"]) | set(control["rx"])) <= WIRE_V1_FRAME_TYPES:
                out.append("a frame type outside the frozen wire v1 set, or an unknown-type record")
    resize = trial.get("resizeBehaviour")
    if resize is not None:
        out.append("an unexpected resize behaviour record in a measured trial")
    return out


# ----------------------------------------------------------------------------------------------------------------- loading
def load_matrix(bundle: Bundle, freeze: dict, freeze_sha256: str) -> dict:
    """Admit trial receipts. Returns {'trials': {slot: trial}, 'integrity': [...], 'missing': [slots], 'invalid': [slots], 'problems': {slot: [...]}}."""
    schedule = ref.make_schedule(freeze["scheduleSeed"])
    first_arm = {(s["cell"], s["repeat"]): (s["slot"], s["firstArm"]) for s in schedule}
    integrity: list[str] = []
    trials: dict = {}
    for name in bundle.names():
        if not name.startswith("trials/") or name.endswith(RESOURCES_SUFFIX):
            continue
        match = TRIAL_NAME.match(name)
        if not match:
            integrity.append(f"unexpected file {name}")
            continue
        slot = (match.group(1), int(match.group(2)), int(match.group(3)))
        try:
            trial = bundle.read_json(name)
        except BundleError as error:
            integrity.append(f"{name}: {error}")
            continue
        if not isinstance(trial, dict):
            integrity.append(f"{name}: not a JSON object")
            continue
        bad = []
        if trial.get("schema") != "acr-g1e-trial/1":
            bad.append("not an acr-g1e-trial/1 receipt")
        if (trial.get("selectedRuntime"), trial.get("count"), trial.get("repeat")) != slot:
            bad.append("receipt content does not match its file name")
        if trial.get("measuring") is not True or trial.get("warmup") is not False or trial.get("kind") != "measured":
            bad.append("not a measured-matrix receipt (a warm-up receipt is never a trial)")
        if trial.get("freezeSha256") != freeze_sha256:
            bad.append("freeze digest differs (stale or unbound receipt)")
        if trial.get("evidenceRevision") != freeze["evidenceRevision"] or trial.get("methodRevision") != freeze["methodRevision"]:
            bad.append("evidence or method revision differs from the freeze")
        if slot[0] == "rust-shell" and trial.get("expectedSidecarSha256") != freeze["sidecarSha256"]:
            bad.append("expected sidecar hash differs from the freeze")
        expected_schedule = first_arm.get((slot[1], slot[2]))
        if expected_schedule is None or (trial.get("slot"), trial.get("firstArm")) != expected_schedule:
            bad.append("slot/firstArm differ from the frozen schedule")
        if bad:
            integrity.append(f"{name}: " + "; ".join(bad))
            continue
        resources = name[:-len(".json")] + RESOURCES_SUFFIX
        shape, samples = structure_problems(trial, freeze, bundle, resources)
        broken = shape + (consistency_problems(trial) if not shape else [])
        if broken:
            integrity.append(f"{name}: " + "; ".join(broken))
            continue
        trial["_samples"] = samples
        trial["_name"] = name[:-len(".json")]
        trials[slot] = trial
    missing = [s for s in expected_slots() if s not in trials]
    problems = {slot: trial_problems(t, freeze, slot[0], slot[1]) for slot, t in trials.items()}
    invalid = [s for s, p in problems.items() if p]
    return {"trials": trials, "integrity": integrity, "missing": missing, "invalid": invalid, "problems": problems, "schedule": schedule}


# ----------------------------------------------------------------------------------------------------- per-trial statistics
def _phase_samples(trial: dict, name: str) -> list[dict]:
    return _rows_in_phase(trial["phases"], trial["_samples"], name)


def view_numbers(selected: list[dict], predicate) -> tuple[float | None, float | None]:
    rss = [sum(p["rssBytes"] for p in sample["processes"] if predicate(p)) for sample in selected]
    cpus: dict = {}
    for sample in selected:
        for p in sample["processes"]:
            if predicate(p):
                cpus.setdefault((p["pid"], p["created"]), []).append(p["cpuSeconds"])
    delta = sum(max(v) - min(v) for v in cpus.values())
    duration = (selected[-1]["monotonicNs"] - selected[0]["monotonicNs"]) / 1e9 if len(selected) > 1 else 0
    return (statistics.mean(rss) / MIB if rss else None), (delta / duration * 100 if duration else None)


def trial_scalars(trial: dict) -> dict[str, float]:
    """Flat metric key -> per-trial value (06 unchanged: per-trial median of the pooled list; io_ms also its p95; v14-r2 V16: `burst_input_ms` is the arithmetic MEAN of the pooled acks; resource phases need >= 30 samples).
    `observation_ms` is the Claude peers' value only (the recorded list is already Claude-only, see the runner)."""
    scalars: dict[str, float] = {}
    for key, values in trial["latencies"].items():
        phase = LATENCY_PHASE.get(key)
        if phase is None or trial["phaseOutcomes"].get(phase) != "passed" or not values:
            continue
        scalars[f"lat:{key}"] = statistics.mean(values) if key == "burst_input_ms" else statistics.median(values)   # v14-r2 V16: the ack latency is bimodal, its mean is continuous in the mode mix
        if key == "io_ms":
            scalars["lat:io_ms:p95"] = p95(values)
    for name in RESOURCE_PHASES:
        if trial["phaseOutcomes"].get(name) != "passed":
            continue
        selected = _phase_samples(trial, name)
        if len(selected) < RESOURCE_MIN_SAMPLES:
            continue
        for view, predicate in VIEWS.items():
            rss, cpu = view_numbers(selected, predicate)
            if rss is not None:
                scalars[f"res:{name}:view:{view}:rssMiB"] = rss
            if cpu is not None:
                scalars[f"res:{name}:view:{view}:cpuOneCorePercent"] = cpu
        for component in sorted({p["component"] for s in selected for p in s["processes"]}):
            rss, cpu = view_numbers(selected, lambda p, c=component: p["component"] == c)
            if rss is not None:
                scalars[f"res:{name}:component:{component}:rssMiB"] = rss
            if cpu is not None:
                scalars[f"res:{name}:component:{component}:cpuOneCorePercent"] = cpu
    return scalars


def claude_assessment(trial: dict, vendors: list[str]) -> dict:
    count, repeat = trial["count"], trial["repeat"]
    expected = sum(1 for index in range(count) if vendors[(index + repeat) % len(vendors)] == "claude")
    peers = [p["pane"] for p in trial["peerIdentities"] if p["vendor"] == "claude"]
    outcomes = [o for o in trial["observationOutcomes"] if o["vendor"] == "claude"]
    failures = []
    if len(peers) != expected:
        failures.append(f"claude-peers:{len(peers)}!={expected}")
    for outcome in outcomes:
        if outcome["pane"] not in peers:
            failures.append(f"claude-outcome-for-unknown-pane:{outcome['kind']}")
        elif outcome["status"] != "passed":
            failures.append(f"{outcome['kind']}:{outcome['pane'][:8]}:{outcome['status']}")
    for pane in peers:
        for kind in ("activity", "live_totals"):
            if not any(o["pane"] == pane and o["kind"] == kind and o["status"] == "passed" for o in outcomes):
                failures.append(f"{kind}:{pane[:8]}:missing")
    return {"expected": expected, "failures": failures}


def claude_state(trials: dict, freeze: dict) -> dict:
    vendors = freeze["vendors"]
    findings, states = [], set()
    for count in CELLS:
        for repeat in range(REPEATS):
            if not any(vendors[(i + repeat) % len(vendors)] == "claude" for i in range(count)):
                continue
            py, ru = trials.get(("python", count, repeat)), trials.get(("rust-shell", count, repeat))
            if py is None or ru is None:
                states.add("incomplete")
                continue
            pf, rf = claude_assessment(py, vendors)["failures"], claude_assessment(ru, vendors)["failures"]
            if pf or rf:
                findings.append(f"{count}/{repeat}: python {pf} rust {rf}")
                states.add("rust_only_failure" if rf and not pf else "other_failure")
    for state in ("rust_only_failure", "other_failure", "incomplete"):
        if state in states:
            return {"state": state, "findings": findings}
    return {"state": "ok", "findings": findings}


def build_rows(freeze: dict, matrix: dict) -> dict[str, dict]:
    """Registry row id -> {pairs: [10 pair objects]} from the admitted, evidence-valid trials only (an invalid or missing trial never yields a value)."""
    trials, invalid = matrix["trials"], set(matrix["invalid"])
    scalars: dict = {}
    for slot, trial in trials.items():
        scalars[slot] = trial_scalars(trial) if slot not in invalid else {}
    rows = {}
    for spec in ref.REG:
        pairs = []
        for repeat in range(REPEATS):
            py = scalars.get(("python", spec["cell"], repeat), {}).get(spec["metric"])
            ru = scalars.get(("rust-shell", spec["cell"], repeat), {}).get(spec["metric"])
            if spec["source"] == "rust-value":
                value = ru
                pairs.append({"repeat": repeat, "python": None, "rust": value if is_num(value) else None, "valid": is_num(value)})
            else:
                ok = is_num(py) and is_num(ru)
                pairs.append({"repeat": repeat, "python": py if ok else None, "rust": ru if ok else None, "valid": ok})
        rows[spec["id"]] = {"pairs": pairs}
    return rows


def canary_values(freeze: dict, matrix: dict) -> dict[str, list]:
    """12 canary series of 10 values, ordered by execution slot; a missing value stays None (the screen then fails, INCONCLUSIVE)."""
    schedule = matrix["schedule"]
    order = {(s["cell"], s["repeat"]): s["slot"] for s in schedule}
    trials, invalid = matrix["trials"], set(matrix["invalid"])
    out = {}
    for cid in ref.CANARY_IDS:
        _tag, arm, cell_s, metric = cid.split("|", 3)
        cell = int(cell_s[4:])
        series = []
        for repeat in sorted(range(REPEATS), key=lambda r: order[(cell, r)]):
            slot = (arm, cell, repeat)
            trial = trials.get(slot)
            value = trial_scalars(trial).get(metric) if trial is not None and slot not in invalid else None
            series.append(value if is_num(value) else None)
        out[cid] = series
    return out


def resize_report(matrix: dict) -> dict:
    """reportOnly.resizeAckMs: paired median Rust - Python per cell over the valid pairs (never a decision fact)."""
    out = {}
    invalid = set(matrix["invalid"])
    for cell in CELLS:
        deltas = []
        for repeat in range(REPEATS):
            ps, rs = ("python", cell, repeat), ("rust-shell", cell, repeat)
            if ps in matrix["trials"] and rs in matrix["trials"] and ps not in invalid and rs not in invalid:
                a = trial_scalars(matrix["trials"][ps]).get("lat:resize_ack_ms")
                b = trial_scalars(matrix["trials"][rs]).get("lat:resize_ack_ms")
                if is_num(a) and is_num(b):
                    deltas.append(b - a)
        out[str(cell)] = {"validPairs": len(deltas), "medianDelta": statistics.median(deltas) if deltas else None}
    return out


# ------------------------------------------------------------------------------------------------ S1 / S2 facts of one receipt
def ownership_failure(trial: dict) -> bool:
    """S1's per-trial fact: a Rust-arm session survived or its cleanup/ownership failed (v10 section 8)."""
    proof = trial.get("proof") if isinstance(trial.get("proof"), dict) else {}
    ownership = proof.get("ownership") if isinstance(proof.get("ownership"), dict) else {}
    return ((trial.get("phaseOutcomes") or {}).get("cleanup") == "failed" or trial.get("harnessRescued") is True or proof.get("ownershipFailure") is True
            or any(ownership.get(k) for k in OWNERSHIP_LISTS))


def identity_ok(x: Any) -> bool:
    return isinstance(x, dict) and set(x) == {"pid", "start"} and is_int(x["pid"], 1) and isinstance(x["start"], str) and bool(x["start"].strip())


def isolation_breach(trial: dict) -> bool:
    """S2's per-trial fact for an ADMITTED matrix trial: the backend identity at the end differs from the start (any difference, as in v10)."""
    return trial.get("backend") != trial.get("backendEnd")


def isolation_breach_recorded(trial: dict) -> bool:
    """S2 over a receipt that was never admitted (an earlier run's record, a calibration attempt, a matrix receipt that failed admission): v10's definition, a start identity that differs from
    the end identity. The start identity must be well-formed (a pid-0 receipt of an attempt that never started has none); the end identity is whatever was recorded, so a backend that
    died (an empty end start, or no end record) IS a breach, exactly as in the current-run rule `isolation_breach`."""
    return identity_ok(trial.get("backend")) and trial.get("backend") != trial.get("backendEnd")
