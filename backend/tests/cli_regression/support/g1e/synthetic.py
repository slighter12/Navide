"""Synthetic G1-E evidence bundle for the decider tests (SYNTHETIC: no number here is a measurement; nothing here is evidence).

`make_bundle(root)` writes a complete, valid 40-trial bundle plus every auxiliary record, WITHOUT the manifest; `seal(root)` writes the manifest and returns
its SHA-256. A test mutates files between the two (content damage with a consistent manifest) or after `seal` (tampering with a pinned manifest).
The positive control is essential: a decider that never derives CONTINUE would pass every "never CONTINUE" mutation test, so the unmutated bundle must derive it.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from tests.cli_regression.support.g1e import quiet_gate
from tests.cli_regression.support.g1e import reference as ref
from tests.cli_regression.support.g1e import summarise
from tests.cli_regression.support.g1e.bundle import write_manifest

VENDORS = ["claude", "opencode"]
V13_PATH = ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v13.md"
V14R2_PATH = ".scratch/agent-cli-runtime-evaluation/design/e2-g1e-prereg-proposal-v14-r2.md"
SYNTHETIC_LISTING = [{"path": "synthetic/frozen-input.txt", "sha256": hashlib.sha256(b"synthetic frozen input").hexdigest()}]    # the frozen-input listing of a synthetic F0 (never compared with a tree)
HEAD = "a" * 40
SIDECAR = hashlib.sha256(b"synthetic-sidecar").hexdigest()
FIXTURE_DIGEST = ref.FROZEN["fixture_registry_sha256"]
LOG_DIGEST = ref.FROZEN["log_policy_sha256"]
T0 = 1_700_000_000_000.0
SAMPLES_PER_PHASE = 31
SAMPLE_MS = 100.0
FINAL_LINE = 60


def h(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def write_json(root: Path, name: str, obj) -> dict:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, indent=1, sort_keys=True).encode() + b"\n"
    path.write_bytes(raw)
    return {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}


def write_text(root: Path, name: str, text: str) -> dict:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text.encode()
    path.write_bytes(raw)
    return {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}


def evidence(label: str) -> dict:
    return {"ledger": "synthetic/progress.md", "date": "2000-01-01", "quote": f"SYNTHETIC {label}"}


def pick_seed() -> str:
    """A seed whose canary series do not trip the screen for the synthetic values (deterministic)."""
    for n in range(1, 200):
        seed = f"synthetic-e4-seed-{n}"
        if all(not _trips(seed, arm, cell) for arm in ref.ARMS for cell in ref.CELLS):
            return seed
    raise AssertionError("no synthetic seed found")


def _noise(cell: int, repeat: int, salt: str) -> float:
    return random.Random(f"{salt}|{cell}|{repeat}").random()


def _trips(seed: str, arm: str, cell: int) -> bool:
    order = {(s["cell"], s["repeat"]): s["slot"] for s in ref.make_schedule(seed)}
    for salt in ("cold", "rss", "io"):
        series = [_noise(cell, r, salt) for r in sorted(range(10), key=lambda r: order[(cell, r)])]
        if abs(ref.kendall_s(series)) >= ref.S_TRIP - 6:
            return True
    return False


def base_values(cell: int, repeat: int) -> dict[str, float]:
    """The per-slot 'true' values both arms share; the arms differ only where the registry expects it (ack deltas, kill/reap beat, the sidecar budgets)."""
    n = lambda salt: _noise(cell, repeat, salt)
    return {
        "cold_backend_ready_ms": 900 + 5 * n("cold"), "first_shell_ack_ms": 40 + n("a"), "first_shell_ready_ms": 60 + n("b"), "warm_shell_ack_ms": 20 + 0.2 * n("c"),
        "warm_shell_ready_ms": 30 + n("d"), "io_ms": 6 + 0.05 * n("io"), "burst_ms": 50 + n("e"), "burst_input_ms": 8 + 0.1 * n("f"), "observation_ms": 120 + n("g"),
        "reconnect_recovery_ms": 90 + n("h"), "resize_ack_ms": 3 + 0.1 * n("i"), "kill_reap_ms": 330 + n("j"), "backend_rss": 100 + 0.2 * n("rss"),
        "backend_cpu": 1.0 + 0.01 * n("k"), "helper_cpu": 0.3, "idle_cpu": 0.02, "sidecar_rss": 5.0, "sidecar_cpu_active": 0.1, "sidecar_cpu_idle": 0.02,
    }


def phase_names() -> list[str]:
    return list(summarise.PHASE_SEQUENCE)


def make_samples(arm: str, cell: int, repeat: int, phases: list[dict], backend: dict) -> list[dict]:
    v = base_values(cell, repeat)
    rows = []
    helper_pids = [5000 + i for i in range(cell)]
    for index, phase in enumerate(phases[:-1]):
        name = phase["name"]
        active = name == "input_output"
        idle = name in ("empty_idle", "live_idle", "empty_started_idle")
        for k in range(SAMPLES_PER_PHASE):
            t_ms = phase["wallMs"] + k * SAMPLE_MS
            seconds = (t_ms - T0) / 1000.0
            procs = []
            backend_rate = (v["backend_cpu"] if active else v["idle_cpu"]) / 100.0
            procs.append({"pid": backend["pid"], "created": 1.0, "ppid": 1, "component": "backend", "versionProbe": False, "category": "runtime",
                          "cpuSeconds": backend_rate * seconds, "rssBytes": int(v["backend_rss"] * summarise.MIB), "name": "python", "executable": "/synthetic/python"})
            if arm == "rust-shell":
                rate = (v["sidecar_cpu_active"] if active else v["sidecar_cpu_idle"]) / 100.0
                if name in ("empty_idle", "cold_startup"):
                    pass
                else:
                    procs.append({"pid": 4000, "created": 2.0, "ppid": backend["pid"], "component": "sidecar", "versionProbe": False, "category": "runtime",
                                  "cpuSeconds": rate * seconds, "rssBytes": int(v["sidecar_rss"] * summarise.MIB), "name": "navide-cli-runtime", "executable": "/synthetic/navide-cli-runtime"})
            if name in ("live_idle", "input_output", "burst", "observation", "reconnect", "resize_ack", "kill"):
                for pid in helper_pids:
                    procs.append({"pid": pid, "created": 3.0, "ppid": 4000 if arm == "rust-shell" else backend["pid"], "component": "helper", "versionProbe": False,
                                  "category": "controlled_child", "cpuSeconds": (v["helper_cpu"] if active else 0.0) / 100.0 * seconds, "rssBytes": 8 * summarise.MIB,
                                  "name": "python", "executable": "/synthetic/python"})
            rows.append({"wallMs": t_ms, "monotonicNs": int(t_ms * 1e6), "generation": "first", "backend": {"pid": backend["pid"], "created": 1.0}, "processes": procs})
    return rows


def bimodal_acks(n: int, slow: int) -> list[float]:
    """A mid-burst ack list of the shape V16 describes: `n - slow` acks in the fast mode (1, 2, ... ms) and `slow` acks in the slow mode (50.0, 50.5, ... ms), fast ones first."""
    return [1.0 + i for i in range(n - slow)] + [50.0 + 0.5 * i for i in range(slow)]


def make_ledger(peer: int, cell: int) -> dict:
    accepted = 100_000
    origin_read = summarise.MARKER_LINE_BYTES + 3000
    windows = [{"wid": wid, "digest": h(f"win-{peer}-{wid}")} for wid in sorted(summarise._wid_set(peer, cell))]
    return {
        "session": f"sess-{peer}", "pane": f"pane-{peer}", "generation": 1, "peerIndex": peer, "nonceSha256": h(f"nonce-{peer}"),
        "rawMode": {"opost": False, "onlcr": False, "echo": False},
        "origin": {"read_total": origin_read, "emitted_total": origin_read, "dropped_oldest_total": 0, "stable": True, "peerAcceptedAtOrigin": summarise.MARKER_LINE_BYTES},
        "close": {"read_total": origin_read + accepted, "emitted_total": origin_read + accepted, "dropped_oldest_total": 0},
        "peer": {"acceptedSinceOrigin": accepted, "finalLineBytes": FINAL_LINE, "totalDigest": h(f"total-{peer}"), "writeFailed": False, "windows": windows},
        "stream": {"markerCount": 1, "finalSeen": True, "finalAccepted": accepted - FINAL_LINE, "finalDigest": h(f"total-{peer}"), "windows": [dict(w) for w in windows]},
        "logIdentity": {"startMarkerCount": 1, "finalPresent": True, "textEqual": True, "windowsEqual": True, "preOriginLogBytes": 1000},
        "logFile": {"regular": True, "dev": 1, "ino": 7 + peer, "inodeAtCreate": [1, 7 + peer], "size": 5000, "siblingExists": False, "logSentUsed": False},
    }


def make_log_final(arm: str) -> dict:
    read = {"dev": 1, "ino": 7, "size": 5000, "mtime_ns": 5, "sha256": h("log"), "regular": True}
    return {"arm": arm, "sidecar_live": None if arm == "python" else True, "inventory": {"backend": False} if arm == "python" else {"backend": False, "sidecar": False},
            "reads": [{"t_ms": 1.0, **read}, {"t_ms": 301.0, **read}], "t_exit_ms": 0.0, "t_inventory_ms": 0.5}


def synthetic_toolchain() -> dict:
    triple = lambda name: {"realPath": f"/synthetic/{name}", "version": f"{name}-1.0.0", "sha256": h(f"toolchain-{name}")}
    return {"node": triple("node"), "pnpm": triple("pnpm"), "python": triple("python"), "sidecar": {"realPath": "/synthetic/navide-cli-runtime", "sha256": SIDECAR, "readyVersion": "0.0.0-synthetic"}}


SYNTHETIC_UV = {"realPath": "/synthetic/uv", "version": "uv 1.0.0", "sha256": hashlib.sha256(b"toolchain-uv").hexdigest()}
READY_VERSION = "0.0.0-synthetic"
PYTHON_ARM_FILES = ["backend/agent_team_backend/cli_runtime.py", "backend/agent_team_backend/terminals.py", "backend/agent_team_backend/app.py"]


def toolchain_record(freeze: dict, arm: str) -> dict:
    """The per-attempt record V7 item 2 requires: recorded and observed identities (equal), the match, the Rust-arm ready-line version, uv (informational)."""
    frozen = freeze["toolchain"]
    scope = {k: dict(frozen[k]) for k in ("node", "pnpm", "python")} | {"sidecar": {"realPath": frozen["sidecar"]["realPath"], "sha256": frozen["sidecar"]["sha256"]}}
    version = frozen["sidecar"]["readyVersion"]
    return {"recorded": scope, "observed": json.loads(json.dumps(scope)), "match": True, "mismatches": [],
            "readyVersion": {"recorded": version, "observed": version, "match": True} if arm == "rust-shell" else None, "uv": dict(SYNTHETIC_UV)}


def synthetic_python_arm(*, listed: bool) -> dict:
    """F0's V5 record. `listed`: cli_runtime.py (E3set) differs from its reference and is listed; otherwise nothing differs."""
    e3set = ["backend/agent_team_backend/cli_runtime.py"] if listed else []
    loaded = [{"path": p, "sha256": h(f"loaded-{p}")} for p in sorted(PYTHON_ARM_FILES)]
    manifest = [{"path": p, "sha256": h(f"loaded-{p}") if not (listed and p in e3set) else h(f"reference-{p}"),
                 "source": "09bf4d8666212c84abab5deda6780541e3312f2c" if p in e3set else "7c2b13ad59fa6dd0d59984dbe2fd128acb1b2901"} for p in sorted(PYTHON_ARM_FILES)]
    return {"upstreamMerge": "7c2b13ad59fa6dd0d59984dbe2fd128acb1b2901", "e3Parent": "09bf4d8666212c84abab5deda6780541e3312f2c", "e3Commit": "840b9d930c433e1a7443ebb84ef3b06d465f0476",
            "e3Set": e3set, "fileList": loaded, "fileListSha256": canonical(loaded), "referenceManifest": manifest, "referenceManifestSha256": canonical(manifest)}


def canonical(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


APPROVAL_LINE = f"- 2026-10-21: USER APPROVED E2 v14-r2 design/e2-g1e-prereg-proposal-v14-r2.md SHA-256 {summarise.V14R2_SHA256} (reviewed PASS by reviewer C) with combination a, C1 option (b), g = 8 and no cap"


def valid_approval(quote: str = APPROVAL_LINE, date: str = "2026-10-21", ledger: str = summarise.LEDGER_RELATIVE) -> dict:
    """An approval record of the shape a real F0 carries: the whole dated ledger line of the user's approval. It is only accepted where the ledger file at hand holds `quote` as one whole line."""
    return {"ledger": ledger, "date": date, "quote": quote}


def synthetic_locality_scope() -> dict:
    """F0's locality-scope record of a synthetic F0 (never compared with a tree): the sorted scoped paths with their SHA-256 and the digest of that list."""
    files = [{"path": "native/navide-cli-runtime/src/posix.rs", "sha256": h("synthetic-posix.rs")}]
    return {"files": files, "sha256": canonical(files)}


def synthetic_quiet_gate() -> dict:
    """F0's quiet-gate record of a synthetic F0 (v14-r2 V14): the registered rule and parameters (the constants of `quiet_gate.py`, no second copy) and a script hash never compared with a tree."""
    return {"script": {"path": summarise.QUIET_GATE_PATH, "sha256": h("synthetic-quiet-gate")}, "rule": quiet_gate.RULE, "loadLimit": quiet_gate.LIMIT, "intervalS": quiet_gate.INTERVAL_S,
            "maxWaitMin": quiet_gate.MAX_WAIT_S // 60}


def install_quiet_gate(root: Path) -> None:
    """Put the frozen quiet-gate script, byte for byte, into a scratch tree at the path F0 records (F0 hashes it from the tree it is built in)."""
    target = Path(root) / summarise.QUIET_GATE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(Path(quiet_gate.__file__).read_bytes())


def synthetic_freeze(seed: str, *, reader_required: bool, calibration: str, listed: bool = True, earlier: list[str] | None = None, branch: str = "eval/agent-cli-runtime",
                     overrides: dict | None = None) -> dict:
    schedule = ref.make_schedule(seed)
    return {**_synthetic_freeze(seed, schedule, reader_required, calibration, listed, earlier, branch), **(overrides or {})}


def _synthetic_freeze(seed, schedule, reader_required, calibration, listed, earlier, branch) -> dict:
    return {
        "schema": summarise.F0_SCHEMA, "head": HEAD, "branch": branch, "sidecarSha256": SIDECAR, "methodRevision": "method-10-e", "evidenceRevision": "synthetic-1",
        "preregistration": {"document": V14R2_PATH, "sha256": summarise.V14R2_SHA256, "baseSha256": summarise.V10_SHA256, "userApproval": evidence("approval")},
        "ruleFiles": {"v14r2": {"path": V14R2_PATH, "sha256": summarise.V14R2_SHA256}, "v13": {"path": V13_PATH, "sha256": summarise.V13_SHA256},
                      "v10": {"path": "synthetic/v10.md", "sha256": summarise.V10_SHA256}, "e1v8b": {"path": "synthetic/e1-v8b.md", "sha256": ref.E1_BOUND_SHA256}}, "examples": dict(ref.example_digests()),
        "e1ProtocolSha256": ref.FROZEN["e1_sha256"], "schemaSha256": ref.FROZEN["schema_sha256"], "fixtureRegistrySha256": FIXTURE_DIGEST, "logPolicySha256": LOG_DIGEST,
        "scheduleSeed": seed, "scheduleSha256": ref.schedule_sha(schedule), "workload": dict(summarise.REGISTERED_WORKLOAD), "inputsSha256": canonical(SYNTHETIC_LISTING), "trackedDiffSha256": h("tracked-diff"),
        "options": {"readerRequired": reader_required, "calibration": calibration, **ref.noisy_options(calibration)}, "vendors": VENDORS,
        "earlierRuns": sorted(earlier or []), "pythonArm": synthetic_python_arm(listed=listed), "toolchain": synthetic_toolchain(), "uv": dict(SYNTHETIC_UV),
        "localityScope": synthetic_locality_scope(), "quietGate": synthetic_quiet_gate(), "carriedVerdicts": None,
    }


def synthetic_f1(freeze: dict, f0_sha: str, calibration_record: dict) -> dict:
    reader = freeze["options"]["readerRequired"]
    return {"schema": summarise.F1_SCHEMA, "f0Sha256": f0_sha, "reverified": {"ok": True, "items": list(summarise.F0_ITEMS)},
            "calibration": calibration_record if freeze["options"]["calibration"] != "none" else {"receipts": [], "noisyRows": [], "uncalibratedRows": []},
            "readerMargins": {fx: {m: 1.0 for m in summarise.READER_METRICS} for fx in summarise.READER_FIXTURES} if reader else None}


def run_record(kind: str, names: list[str], *, stop_reason: str | None = None, invalid_last: bool = False) -> dict:
    attempts = [{"name": n, "validity": "clean", "stopReason": None} for n in names]
    if invalid_last:
        attempts[-1] = {"name": names[-1], "validity": "invalid", "stopReason": stop_reason}
    return {"schema": summarise.RUN_SCHEMA, "kind": kind, "attempts": attempts, "stopReason": stop_reason, "complete": stop_reason is None}


def host_numbers(serial: int, at: float, salt: int) -> dict:
    """Deterministic host numbers (v14-r2 V19 item 5) for one point of one attempt; `salt` changes every one of them (the T16 test compares two bundles that differ only in it)."""
    return {"at": at + 1000.0 * salt, "loadavg": [round(0.5 + 0.01 * (serial % 50) + 0.7 * salt, 3), round(0.6 + 0.01 * (serial % 30) + 0.7 * salt, 3), round(0.7 + 0.7 * salt, 3)],
            "ncpu": 15 + salt, "processCount": 400 + (serial % 40) + 53 * salt}


def make_trial(freeze: dict, freeze_sha: str, schedule: dict, arm: str, cell: int, repeat: int, serial: int, *, warmup: bool = False, kind: str | None = None,
               host_salt: int = 0) -> tuple[dict, list[dict]]:
    v = base_values(cell, repeat)
    count = cell
    rounds = summarise.burst_rounds(summarise.REGISTERED_WORKLOAD, cell)                  # per peer: cell 1 = 40, cell 4 = 10 (V17); count * rounds = 40 acks in both cells
    peers_vendor = summarise.expected_vendors(count, repeat, VENDORS)
    backend = {"pid": 10_000 + serial, "start": f"synthetic-start-{serial}"}
    lat = lambda value, n=1: [value] * n
    rust = arm == "rust-shell"
    latencies = {
        "cold_backend_ready_ms": lat(v["cold_backend_ready_ms"]), "first_shell_ack_ms": lat(v["first_shell_ack_ms"] + (2.0 if rust else 0.0)), "first_shell_ready_ms": lat(v["first_shell_ready_ms"]),
        "warm_shell_ack_ms": lat(v["warm_shell_ack_ms"] + (0.5 if rust else 0.0)), "warm_shell_ready_ms": lat(v["warm_shell_ready_ms"]),
        "cold_to_first_usable_ms": lat(v["cold_backend_ready_ms"] + v["first_shell_ready_ms"]), "agent_ack_ms": lat(30.0, count), "agent_ready_ms": lat(45.0, count),
        "io_ms": lat(v["io_ms"], count * 1500), "burst_ms": lat(v["burst_ms"], count * rounds), "burst_input_ms": lat(v["burst_input_ms"], count * rounds),
        "observation_ms": lat(v["observation_ms"], sum(1 for x in peers_vendor if x == "claude")), "reconnect_io_ms": lat(7.0, count),
        "reconnect_recovery_ms": lat(v["reconnect_recovery_ms"]), "resize_ack_ms": lat(v["resize_ack_ms"] + (0.3 if rust else 0.0), 20),
        "kill_reap_ms": lat(v["kill_reap_ms"] if not rust else (v["kill_reap_ms"] - 250 if cell == 4 else v["kill_reap_ms"] + 5)), "persistence_restart_ms": lat(800.0),
    }
    phases = []
    clock = T0 + serial * 100_000.0
    for name in phase_names():
        phases.append({"name": name, "wallMs": clock})
        clock += SAMPLES_PER_PHASE * SAMPLE_MS + 50
    outcomes = {name: "passed" for name in phase_names()}
    outcomes["cleanup"] = "passed"
    peer_ids = [{"vendor": vendor, "pane": f"pane-{arm}-{cell}-{repeat}-{i}", "tid": f"tid-{i}", "pid": 7000 + i, "fixtureSessionId": f"s{i}", "identifiedSessionId": f"s{i}"}
                for i, vendor in enumerate(peers_vendor)]
    observed = []
    for peer in peer_ids:
        if peer["vendor"] == "claude":
            observed += [{"pane": peer["pane"], "vendor": "claude", "kind": "activity", "status": "passed"}, {"pane": peer["pane"], "vendor": "claude", "kind": "live_totals", "status": "passed"}]
        else:
            observed.append({"pane": peer["pane"], "vendor": "opencode", "kind": "activity", "status": "censored"})
    proof = {"ok": True, "failures": [], "facts": {"mode": arm}, "ownership": None if not rust else {k: [] for k in summarise.OWNERSHIP_LISTS}, "ownershipFailure": False}
    trial = {
        "schema": "acr-g1e-trial/1", "method": 9, "methodRevision": freeze["methodRevision"], "selectedRuntime": arm, "count": count, "repeat": repeat, "measuring": not warmup,
        "kind": kind or "measured", "warmup": warmup, "ambientBefore": host_numbers(serial, phases[0]["wallMs"] - 50.0, host_salt),
        "ambientAfter": host_numbers(serial + 1, phases[-1]["wallMs"] + 50.0, host_salt),
        "slot": schedule["slot"], "firstArm": schedule["firstArm"], "freezeSha256": freeze_sha, "evidenceRevision": freeze["evidenceRevision"],
        "expectedSidecarSha256": freeze["sidecarSha256"] if rust else None, "freezeCheckedInputs": 7,
        "fixtureRegistrySha256": FIXTURE_DIGEST, "logPolicySha256": LOG_DIGEST,
        "backend": backend, "backendEnd": dict(backend), "passed": True, "evidenceValid": True, "harnessRescued": False, "failure": None,
        "latencies": latencies, "phases": phases, "phaseOutcomes": outcomes, "observationOutcomes": observed, "peerIdentities": peer_ids, "proof": proof,
        "toolchain": toolchain_record(freeze, arm),
        "ledgers": [make_ledger(i, cell) for i in range(count)], "logFinals": [make_log_final(arm) for _ in range(count)],
        "burstChecks": [{"kind": "pass", "rounds": rounds} for _ in range(count)],
        "createdTerminals": [{"tid": f"t{i}", "pane": f"p{i}", "vendor": "terminal" if i < 2 else peer_ids[i - 2]["vendor"], "label": "shell" if i < 2 else "agent", "createPid": 1000 + i, "peerPid": 1000 + i} for i in range(count + 2)],
        "ckProof": ({"ok": True, "failures": [], "applicable": True, "records": 5 * (count + 2), "items": {"1": 1, "2": {"ck": count + 2, "failed_creates": 0}, "3": {"owned": count + 2, "cleaned": count + 2}}}
                    if rust else {"ok": True, "failures": [], "items": None, "applicable": False}),
        "controlLedger": None if not rust else {"tx": {"CREATE": 2 + count, "KILL": 2 + count}, "rx": {"SESSION_OWNED": 2 + count, "SESSION_CLEANED": 2 + count}, "unknownTypes": [], "controlBytes": 4000, "streamBytes": 100_000 * count},
    }
    return trial, make_samples(arm, cell, repeat, phases, backend)


def baseline_record(root: Path, freeze: dict) -> dict:
    """`baseline-identity.json` for F0's V5 record: exactly the listing item 4 defines over F0's hashes, each listed file with two neutral verdicts from distinct reviewers on its loaded bytes."""
    arm = freeze["pythonArm"]
    loaded = {f["path"]: f["sha256"] for f in arm["fileList"]}
    reference = {m["path"]: m["sha256"] for m in arm["referenceManifest"]}
    changed = []
    for path in sorted(p for p in loaded if loaded[p] != reference[p]):
        verdicts = [{"reviewer": name, "session": f"session-{name}", "report": write_text(root, f"baseline/verdict-{name}-{path.rsplit('/', 1)[-1]}.md", f"{name} reviewed {path}"), "verdict": "behaviour-neutral", "loadedSha256": loaded[path]}
                    for name in ("reviewer-a", "reviewer-b")]
        changed.append({"path": path, "loadedSha256": loaded[path], "referenceSha256": reference[path], "diff": write_text(root, f"baseline/diff-{path.rsplit('/', 1)[-1]}.diff", f"diff of {path}"), "verdicts": verdicts})
    return {"status": "refactor_reviewed" if changed else "identical", "upstreamMerge": arm["upstreamMerge"], "e3Parent": arm["e3Parent"], "e3Commit": arm["e3Commit"], "e3Set": arm["e3Set"],
            "pythonArmFileList": write_json(root, "baseline/python-arm-files.json", arm["fileList"]), "referenceManifest": write_json(root, "baseline/reference-manifest.json", arm["referenceManifest"]),
            "changedFiles": changed}


CALIBRATION_SERIAL = 500
WARMUP_SERIAL = CALIBRATION_SERIAL - 1                                           # the calibration warm-up starts before every scored attempt
MATRIX_WARMUP_SERIALS = {"python": 1, "rust-shell": 2}                           # v14-r2 V19 item 2: the Python warm-up, then the Rust warm-up, before the first scored pair (serial 3)
FIRST_SCORED_SERIAL = 3
WILD_SHIFT = {(4, 3): {"burst_ms": 500.0, "io_ms": 5.0}}                         # one wild INTERIOR attempt (slot 3 of the cell-4 chain): two events of eighteen in each shifted row
RAMP_SHIFT = {(1, s): {"io_ms": 0.25 * s} for s in range(10)}                    # a drift ramp over the cell-1 chain: 0.25 ms a step against a 0.5 ms margin


def shifted(trial: dict, shift: dict | None) -> None:
    """Add a constant to every sample of the named latency lists (a shift is always non-negative in the result: callers pick values that keep it so)."""
    for key, add in (shift or {}).items():
        trial["latencies"][key] = [x + add for x in trial["latencies"][key]]


def write_calibration(root: Path, freeze: dict, f0_sha: str, attempts: list | None, damaged: set, wild: bool, edit=None, *, slot_of: dict | None = None, serial_of: dict | None = None,
                      calibration_shift: dict | None = None, warmup_shift: dict | None = None, warmup_cell: int | None = None, warmup: bool = True, host_salt: int = 0) -> dict:
    """The Python-only calibration receipts (v14-r2 V19) and the record F1 freezes, {receipts, noisyRows, uncalibratedRows}: the discarded warm-up `calibration/warmup-python-00.json` (the first
    chain's cell, listed first) and the scored `calibration/trial-python-<cell>-<k>.json` (+ resources), bound to F0, kind `calibration`, not measuring. `attempts` defaults to the 10 per cell.
    Each scored receipt carries its frozen `slot` (`slot_of[(cell, k)]`, default k) and starts at a clock that follows the chain order (`CALIBRATION_SERIAL + chain * 10 + slot`; `serial_of`
    overrides). `damaged` attempts are evidence-invalid; `wild` / `calibration_shift` ({(cell, k): {latency key: addition}}) move values; `warmup_shift` moves the warm-up's."""
    from tests.cli_regression.support.g1e import calibration as cal
    attempts = attempts if attempts is not None else [(cell, k) for cell in ref.CELLS for k in range(10)]
    cells = cal.chain_cells(freeze["scheduleSeed"])
    slot_of, serial_of = slot_of or {}, serial_of or {}
    receipts, chains = [], {cell: [None] * cal.CHAIN_LENGTH for cell in ref.CELLS}
    if warmup:
        trial, samples = make_trial(freeze, f0_sha, {"slot": None, "firstArm": None}, "python", warmup_cell or cells[0], 0, WARMUP_SERIAL, warmup=True, kind="calibration", host_salt=host_salt)
        shifted(trial, warmup_shift)
        receipts.append(write_json(root, "calibration/warmup-python-00.json", trial))
        write_text(root, "calibration/warmup-python-00-resources.jsonl", "".join(json.dumps(r) + "\n" for r in samples))
    for cell, k in attempts:
        slot = slot_of.get((cell, k), k)
        serial = serial_of.get((cell, k), CALIBRATION_SERIAL + cells.index(cell) * cal.CHAIN_LENGTH + slot)
        trial, samples = make_trial(freeze, f0_sha, {"slot": slot, "firstArm": None}, "python", cell, k, serial, kind="calibration", host_salt=host_salt)
        trial.update(measuring=False)
        shifted(trial, (WILD_SHIFT if wild else {}).get((cell, k)))
        shifted(trial, (calibration_shift or {}).get((cell, k)))
        if (cell, k) in damaged:
            trial.update(evidenceValid=False, passed=False)
        if edit and (cell, k) == (1, 3):
            edit(trial)                                                             # a receipt that is not what a calibration attempt must be, with its digest frozen consistently
        name = f"calibration/trial-python-{cell}-{k}"
        receipts.append(write_json(root, name + ".json", trial))
        write_text(root, name + "-resources.jsonl", "".join(json.dumps(r) + "\n" for r in samples))
        if (cell, k) not in damaged and 0 <= slot < cal.CHAIN_LENGTH:
            chains[cell][slot] = summarise.trial_scalars({**trial, "_samples": samples})
    report = cal.rows_calibration(chains)
    return {"receipts": receipts, "noisyRows": report["noisyRows"], "uncalibratedRows": report["uncalibratedRows"]}


def write_matrix_warmups(root: Path, freeze: dict, f1_sha: str, host_salt: int = 0) -> None:
    """The two discarded matrix warm-ups (`warmup/warmup-python-00`, `warmup/warmup-rust-00`): cell 4, bound to F1, `measuring` false, `warmup` true; never trials."""
    from tests.cli_regression.support.g1e import calibration as cal
    for warm in cal.matrix_warmups():
        trial, samples = make_trial(freeze, f1_sha, {"slot": None, "firstArm": None}, warm["mode"], warm["cell"], warm["index"], MATRIX_WARMUP_SERIALS[warm["mode"]], warmup=True, host_salt=host_salt)
        name = f"warmup/{summarise.warmup_file_name(warm['mode'], warm['index'])}"
        write_json(root, name + ".json", trial)
        write_text(root, name + "-resources.jsonl", "".join(json.dumps(r) + "\n" for r in samples))


def make_bundle(root: Path, *, reader_required: bool = True, calibration: str | None = None, listed: bool = True, earlier: list[str] | None = None,
                calibration_attempts: list | None = None, damage_calibration: set | list | None = None, wild_calibration: bool = False,
                declare_noisy: list | None = None, declare_uncalibrated: list | None = None, edit_calibration=None, tree: dict | None = None,
                calibration_shift: dict | None = None, slot_of: dict | None = None, serial_of: dict | None = None, warmup_shift: dict | None = None, warmup_cell: int | None = None,
                host_salt: int = 0) -> dict:
    """Write the complete valid bundle (no manifest). Returns the F0 freeze dict for tests that need constants.
    `tree` = {"root": Path, "freeze": {F0 fields of a real scratch tree: head, trackedDiffSha256, pythonArm, inputsSha256, preregistration}, "inputs": listing}: F0 describes that tree, the bundle
    carries its frozen-input listing and `locality.json` is produced by the REAL `assemble.locality_record` on it (so `decider.make_locality_recheck(tree["root"])` can re-run it).
    `listed`: the V5 listing holds one E3set file with two neutral verdicts (`refactor_reviewed`); False = nothing differs from the reference (`identical`).
    `earlier`: manifest digests of earlier runs F0 names (V7 item 5)."""
    root = Path(root)
    calibration = calibration or ("rows+reader" if reader_required else "rows")
    seed = pick_seed()
    schedule = ref.make_schedule(seed)
    freeze = synthetic_freeze(seed, reader_required=reader_required, calibration=calibration, listed=listed, earlier=earlier, overrides=(tree or {}).get("freeze"))
    freeze_ref = write_json(root, "freeze.json", freeze)
    write_json(root, "freeze-inputs.json", (tree or {}).get("inputs", SYNTHETIC_LISTING))
    calibration_record = (write_calibration(root, freeze, freeze_ref["sha256"], calibration_attempts, set(damage_calibration or ()), wild_calibration, edit_calibration, slot_of=slot_of,
                                            serial_of=serial_of, calibration_shift=calibration_shift, warmup_shift=warmup_shift, warmup_cell=warmup_cell, host_salt=host_salt)
                          if calibration != "none" else None)
    if calibration_record is not None:                                            # a producer that lies about the lists: the decider recomputes them
        if declare_noisy is not None:
            calibration_record["noisyRows"] = list(declare_noisy)
        if declare_uncalibrated is not None:
            calibration_record["uncalibratedRows"] = list(declare_uncalibrated)
    f1 = synthetic_f1(freeze, freeze_ref["sha256"], calibration_record)
    f1_ref = write_json(root, "freeze-f1.json", f1)
    write_json(root, "run.json", run_record("matrix", [f"{arm}:{cell}:{repeat}" for arm in ref.ARMS for cell in ref.CELLS for repeat in range(10)]))
    by_slot = {(s["cell"], s["repeat"]): s for s in schedule}
    serial = FIRST_SCORED_SERIAL - 1
    for arm in ref.ARMS:
        for cell in ref.CELLS:
            for repeat in range(10):
                serial += 1
                trial, samples = make_trial(freeze, f1_ref["sha256"], by_slot[(cell, repeat)], arm, cell, repeat, serial, host_salt=host_salt)
                write_json(root, f"trials/trial-{arm}-{cell}-{repeat}.json", trial)
                write_text(root, f"trials/trial-{arm}-{cell}-{repeat}-resources.jsonl", "".join(json.dumps(r) + "\n" for r in samples))
    write_matrix_warmups(root, freeze, f1_ref["sha256"], host_salt)
    # --- gates ---
    rows = {}
    for cid in ref.CORRECTNESS_IDS:
        if cid == ref.OPTIONAL_CORRECTNESS and not reader_required:
            continue
        receipt = write_json(root, f"gates/{cid}.json", {"schema": "acr-g1e-gate/1", "row": cid, "head": freeze["head"], "sidecarSha256": SIDECAR,
                                                         "commands": [{"argv": ["pytest", cid], "exit": 0}], "result": "pass"})
        rows[cid] = {"status": "pass-current", "receipt": receipt}
    write_json(root, "gates.json", {"schema": "acr-g1e-gates/1", "rows": rows})
    # --- calibration / reader ---
    if calibration != "none":
        write_json(root, "calibration.json", calibration_record)
    if reader_required:
        fixtures = [write_text(root, f"reader/fixture-{fx}.jsonl", fx) for fx in ("claude", "opencode")]
        golden = write_text(root, "reader/golden.json", "[]")
        frozen_in = write_text(root, "reader/calibration-margins.json", "{}")
        reader_rows = {rid: {"id": rid, "unit": "ms" if "m1_" in rid else ("s" if "m2_" in rid else "MiB"), "margin": {"mode": "absolute", "value": 1.0, "frozenIn": frozen_in},
                             "pairs": [{"repeat": i, "python": 10.0, "rust": 10.0, "valid": True} for i in range(10)]} for rid in ref.READER_ROWS}
        check = {k: v for k, v in toolchain_record(freeze, "python").items() if k != "readyVersion"}
        write_json(root, "reader/toolchain-calibration.json", [check] * 20)         # one record per reader-calibration run: 2 fixtures x 10
        write_json(root, "reader/toolchain-measure.json", [check] * 40)             # one per reader-measure run: 2 fixtures x 10 pairs x 2 readers
        write_json(root, "reader.json", {"fixtures": fixtures, "goldenEvents": golden, "calibrationRuns": 10, "runs": 10, "findings": [],
                                         "m0": {fx: {"python": True, "rust": True} for fx in ("claude", "opencode")}, "rows": reader_rows})
    # --- baseline identity / locality / maintainability / review / misc ---
    write_json(root, "baseline-identity.json", baseline_record(root, freeze))
    if tree is not None:
        from tests.cli_regression.support.g1e import assemble
        assemble.locality_record(tree["root"], root, freeze, tree.get("allowlist"))               # the REAL record, made by the real check on the scratch tree
    else:
        write_json(root, "locality.json", {"violation": False, "checkScript": write_text(root, "locality/check.py", "# check"), "scopeList": write_text(root, "locality/scope.txt", "scope"),
                                            "allowlist": write_json(root, "locality/allowlist.json", {"entries": [], "schema": "acr-g1e-locality-allowlist/2"}), "findings": [],
                                            "referenceManifestSha256": freeze["pythonArm"]["referenceManifestSha256"]})
    write_json(root, "maintainability.json", {"complete": True, "analyses": [write_text(root, "maintainability/analysis.md", "analysis")], "touchedResponsibilities": ["spawn"],
                                               "repeatedFacts": ["vendor key"], "manualCompositionSites": ["roster"]})
    write_json(root, "joint-review.json", {"status": "pass", "recordedBy": "synthetic-manager",
                                            "reports": [write_text(root, "review/reviewer-a.md", "a"), write_text(root, "review/reviewer-b.md", "b")]})
    write_json(root, "limitations.json", ["macOS arm64 only; synthetic fixtures", "descendant snapshot is not measured (named limitation against D4)"])
    write_json(root, "manifest-record.json", evidence("manifest"))
    write_json(root, "continuation.json", {"recorded": False})
    write_json(root, "authorizations.json", {"hostedActions": evidence("hosted actions authorization")})
    from tests.cli_regression.support.g1e import assemble
    assemble.write_host_record(root)                                                          # v14-r2 V19 item 5: the table of the host numbers; information only, nothing reads it
    return freeze


def make_no_matrix_bundle(root: Path, *, kind: str = "calibration", stop_reason: str | None = "toolchain-mismatch", earlier: list[str] | None = None, reader_required: bool = True,
                          calibration: str | None = None, listed: bool = True) -> dict:
    """A bundle of a run that never reached a matrix attempt (calibration or reader-calibration stopped, or a restarted round that stopped early). Holds F0, the run record and the two
    manifest-level records every decision needs; no trial, no baseline / locality / maintainability record, no calibration output (the no-matrix path derives `not_derivable`)."""
    root = Path(root)
    calibration = calibration or ("rows+reader" if reader_required else "rows")
    freeze = synthetic_freeze(pick_seed(), reader_required=reader_required, calibration=calibration, listed=listed, earlier=earlier)
    write_json(root, "freeze.json", freeze)
    write_json(root, "freeze-inputs.json", SYNTHETIC_LISTING)
    write_json(root, "run.json", run_record(kind, ["python:1:0", "python:1:1"], stop_reason=stop_reason, invalid_last=stop_reason is not None))
    write_json(root, "limitations.json", ["macOS arm64 only; synthetic fixtures", "descendant snapshot is not measured (named limitation against D4)"])
    write_json(root, "manifest-record.json", evidence("manifest"))
    return freeze


def echo_locality_recheck(freeze: dict, bundle) -> dict:
    """The locality re-check for a SYNTHETIC bundle: there is no repository at the synthetic F0 tree to run the frozen script against, so this stands in for `decider.make_locality_recheck` and
    answers what a clean tree would: whatever the bundle recorded, bound to F0's manifest digest and the bundle's own copy of the script. The real mechanics are tested in `test_g1e_repair1.py`
    (a scratch tree, the real tree) and the decider's comparison is tested by passing a different `recheck`."""
    record = bundle.read_json("locality.json")
    problems = []
    try:                                           # the one part of the real binding a synthetic bundle can honour: the listing travels in the bundle and hashes to F0's inputsSha256
        from tests.cli_regression.support.g1e import freeze as fz
        if fz.canonical_sha256(bundle.read_json("freeze-inputs.json")) != freeze["inputsSha256"]:
            problems.append("freeze-inputs.json does not hash to F0's inputsSha256")
    except Exception as error:                     # noqa: BLE001  absent, unreadable or not JSON: a problem, never a pass
        problems.append(f"freeze-inputs.json is not usable: {error}")
    return {"problems": problems, "violation": record["violation"], "findings": list(record["findings"]), "scopeList": bundle.read(record["scopeList"]["path"]).decode(),
            "checkScriptSha256": record["checkScript"]["sha256"], "referenceManifestSha256": freeze["pythonArm"]["referenceManifestSha256"]}


def echo_approval_check(freeze: dict) -> list[str]:
    """Stand-in for `decider.make_approval_check` on SYNTHETIC bundles, which have no ledger. The real rules are tested in `test_g1e_repair2.py`."""
    return []


def decide(bundle_dir, manifest_sha256, earlier=(), recheck=echo_locality_recheck, approval=echo_approval_check):
    """`decider.decide` for the synthetic bundles (with the stand-in locality re-check and approval check above unless a test passes its own)."""
    from tests.cli_regression.support.g1e import decider
    return decider.decide(bundle_dir, manifest_sha256, earlier, locality_recheck=recheck, approval_check=approval)


def seal(root: Path) -> str:
    return write_manifest(Path(root))
