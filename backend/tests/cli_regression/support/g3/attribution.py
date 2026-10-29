"""`python_arm_attributable` (manager answer 5): derived by code from a rerun of the G1-E baseline-identity tool, never entered by hand.

The G1-E tool (`g1e.baseline_identity`) is used UNCHANGED: `trace_python_arm`, `identity_record` (the listing of Python-arm files whose loaded bytes differ from the baseline reference), and
`classify` (two distinct reviewers' `behaviour-neutral` verdicts, each given on the file's loaded bytes, for every listed file). The reviewer verdicts are the ones the G1-E bundle recorded
(`baseline-identity.json`); `classify` itself rejects a verdict whose `loadedSha256` is not the file's loaded bytes now, so a file edited since G1-E is `changed`. The baseline reference is the G1-E
one: the freshly built manifest must equal the manifest G1-E froze, else the report says so and the value is false.

`python_arm_attributable` is true only for a report that equals the facts rebuilt from the sealed, REGISTERED frozen G1-E record (any of the three statuses), with a matching reference. The status must be the
one the record's own verdicts give (`frozen_status`), and both the record's `status` field and the report's must equal it. With no registered bundle manifest digest, nothing is attributable.
`attribution_verdict(report, g1e_record, g1e_manifest)` recomputes it from the report's facts and says why it refused; `attributable` is its Boolean.

`disclosed_not_passed` (the G1-E record's own status for this freeze): `classify` answers `changed` only because at least one verdict is `other`, while every listed file has a complete two-reviewer
judgement (two distinct reviewers, sessions and report hashes, each verdict `behaviour-neutral` or `other`, each given on the file's loaded bytes now). The baseline is then the evaluation-branch Python
arm (V5 disclosed, not passed), not upstream. Both arms of a G3 pair differ only by the runtime selector, so a difference between them stays attributable on the same head. A listed file without that
complete judgement (a file added or edited since G1-E, a missing or non-distinct second verdict, a verdict value other than the two) keeps the status `changed`, which is not attributable.

A disclosed report is never trusted for its own fields. The listing, the verdicts and `unreviewed` are rebuilt from the frozen G1-E record (`frozen_listing`, `frozen_unreviewed`) and the report must equal
them exactly (the listing in path order, entry for entry). The record, its reference manifest, its file list and its diffs are read only through the sealed G1-E bundle (`g1e.bundle.Bundle`), whose manifest
must hash to the digest REGISTERED outside the bundle (`REGISTERED_ENV`): a changed, unlisted or unresolvable file is a refusal, never an exception.

Trust anchor (rf3h J7, by design). The registration is the anchor: the manager records the digest `g1e-register` printed for the sealed bundle, and from then on a G3 run accepts exactly that bundle. A bundle
that was rewritten AND resealed, with its new digest registered, is accepted when it is internally consistent (status, verdicts, listing and diffs agree with each other): nothing inside a bundle can tell
it from the original, and no code here tries to. The registered digest therefore comes only from the seal step's own output, never from a recomputation later and never from the artifact a G3 run downloads.
There is exactly one digest source on every path (`load_g1e`, `g1e_pairs`, the pre-run check, `decide`, `g1e-register`): the registered value, validated with `is_registered_digest`.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, NamedTuple

from tests.cli_regression.support.g1e import baseline_identity as bi
from tests.cli_regression.support.g1e import bundle as g1e_bundle

SCHEMA = "acr-g3-python-arm-identity/1"
#: The registered SHA-256 of the G1-E bundle's `bundle-manifest.json`, given to the run from OUTSIDE the bundle (the manager's ledger line; the hosted G1-E workflow publishes it, the G3 workflow reads it).
#: It travels in the environment, not in the plan's argv, so the registered plan (and its golden hash) is unchanged. It is never the hash of the file about to be read.
REGISTERED_ENV = "ACR_G3_G1E_MANIFEST_SHA256"
REGISTRATION_SCHEMA = "acr-g3-g1e-bundle-registration/1"
_HEX64 = re.compile(r"[0-9a-f]{64}")      # always used with `fullmatch`: `$` would let a trailing newline through
#: What reading a sealed G1-E bundle can raise: all of it is a refusal with a reason, never an exception out of a run.
READ_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, g1e_bundle.BundleError)
ATTRIBUTABLE_STATUSES = ("identical", "refactor_reviewed", bi.DISCLOSED_NOT_PASSED)
#: The baseline the report names, by status. Only `disclosed_not_passed` says the baseline is not upstream.
BASELINE_LABELS = {bi.DISCLOSED_NOT_PASSED: "evaluation-branch Python arm (V5 disclosed, not passed)"}
BASELINE_LABEL_DEFAULT = "upstream reference (G1-E baseline identity)"
JUDGED_VERDICTS = ("behaviour-neutral", "other")
RECORD_NAME = "baseline-identity.json"


class FrozenRecordError(ValueError):
    """The frozen G1-E record cannot be used: it is unreadable, not in the sealed bundle, or malformed. The message is the reason."""


def g1e_verdicts(g1e_record: dict) -> dict[str, list[dict]]:
    """{path: verdicts} as the G1-E record stored them (each carries its `loadedSha256`)."""
    return {c["path"]: list(c.get("verdicts") or []) for c in g1e_record.get("changedFiles", [])}


def _fully_judged(entry: dict, given: list[dict]) -> bool:
    """True iff `entry` has a complete two-reviewer judgement on its loaded bytes: `classify`'s distinctness and bytes checks, with `other` counted as a judgement and any other value refused."""
    if any(v.get("verdict") not in JUDGED_VERDICTS for v in given):
        return False
    as_reviewed = [{**v, "verdict": "behaviour-neutral"} for v in given]
    return bi.classify({"changed": [entry]}, {entry["path"]: as_reviewed}) == "refactor_reviewed"


def _unreviewed(entries: list[dict], verdicts: dict[str, list[dict]]) -> list[str]:
    return [c["path"] for c in entries if bi.classify({"changed": [c]}, {c["path"]: verdicts.get(c["path"], [])}) != "refactor_reviewed"]


def build_report(record: dict, g1e_record: dict, g1e_manifest: list[dict]) -> dict:
    """The report from an identity record (`bi.identity_record`) against the G1-E record and its reference manifest. Pure."""
    verdicts = g1e_verdicts(g1e_record)
    status = bi.classify(record, verdicts)
    unreviewed = _unreviewed(record["changed"], verdicts)
    unjudged = [c["path"] for c in record["changed"] if not _fully_judged(c, verdicts.get(c["path"], []))]
    if status == "changed" and not unjudged:
        status = bi.DISCLOSED_NOT_PASSED       # every listed file fully judged; only an `other` verdict stops `refactor_reviewed`
    return {"schema": SCHEMA, "status": status, "baseline": BASELINE_LABELS.get(status, BASELINE_LABEL_DEFAULT), "upstreamMerge": record["upstreamMerge"], "e3Parent": record["e3Parent"],
            "e3Commit": record["e3Commit"], "traced": len(record["files"]),
            "manifestSha256": record["manifestSha256"], "g1eManifestSha256": bi.manifest_sha256(g1e_manifest),
            "referenceMatchesG1E": record["manifestSha256"] == bi.manifest_sha256(g1e_manifest),
            "listed": [{k: v for k, v in c.items() if k != "diff"} for c in record["changed"]], "unreviewed": unreviewed, "unjudged": unjudged}


def frozen_listing(g1e_record: dict) -> list[dict]:
    """The report's `listed` entries as the frozen G1-E record fixes them, ordered by path: what `bi.identity_record` lists for the same F0 tree. The status comes from the absent markers, `inE3Set` from
    the record's `e3Set` and `diffSha256` from the stored diff's digest (the diff file holds exactly the text that was hashed)."""
    e3_set = set(g1e_record["e3Set"])
    listing = []
    for c in g1e_record["changedFiles"]:
        status = "added" if c["referenceSha256"] == bi.ABSENT else ("removed" if c["loadedSha256"] == bi.ABSENT else "modified")
        listing.append({"path": c["path"], "status": status, "inE3Set": c["path"] in e3_set, "loadedSha256": c["loadedSha256"], "referenceSha256": c["referenceSha256"], "diffSha256": c["diff"]["sha256"]})
    return sorted(listing, key=lambda entry: entry["path"])


def frozen_unreviewed(g1e_record: dict) -> list[str]:
    """The listed paths whose frozen verdicts do not make them `refactor_reviewed` (a missing or `other` verdict), sorted."""
    return sorted(_unreviewed(g1e_record["changedFiles"], g1e_verdicts(g1e_record)))


def frozen_status(g1e_record: dict) -> str:
    """The status the frozen record's own verdicts give, by the unchanged G1-E rule: `identical` for no listed file; `refactor_reviewed` when `bi.classify` passes; `disclosed_not_passed` when it does not but
    every listed file has a complete two-reviewer judgement (the rule `bi.compose` applies); else `changed`. Neither the record's `status` field nor the report's is read."""
    entries = g1e_record["changedFiles"]
    if not entries:
        return "identical"
    verdicts = g1e_verdicts(g1e_record)
    status = bi.classify({"changed": [{"path": c["path"], "loadedSha256": c["loadedSha256"]} for c in entries]}, verdicts)
    if status == "changed" and all(_fully_judged({"path": c["path"], "loadedSha256": c["loadedSha256"]}, verdicts[c["path"]]) for c in entries):
        return bi.DISCLOSED_NOT_PASSED
    return status


def _same_json(a: object, b: object) -> bool:
    """Equality that tells `True` from `1` and `3.0` from `3` (Python's `==` does not): the report is a byte-exact restatement of the record, so its types must be the record's, not merely equal-looking."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same_json(a[key], b[key]) for key in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same_json(x, y) for x, y in zip(a, b))
    return a == b


def _refusal(report: dict, g1e_record: dict, g1e_manifest: list[dict]) -> str | None:
    """Why a report is not accepted, or None. A report is never trusted for its own fields: it must be a restatement of the sealed, registered G1-E record. The status the record's verdicts give
    (`frozen_status`) must be the record's own `status` and the report's `status`; the reference identity, manifest and traced count must be the record's; the listing must equal the one rebuilt from the
    record exactly, entry for entry and in path order, without duplicates; `unreviewed` must be the one the verdicts leave; every listed file needs a complete two-reviewer judgement on its bytes; a
    `disclosed_not_passed` record needs at least one `other` verdict; nothing unjudged; the baseline label of the status."""
    expected = frozen_status(g1e_record)
    if expected not in ATTRIBUTABLE_STATUSES:
        return f"the frozen G1-E verdicts leave the Python arm `{expected}`: a change without a complete judgement"
    if g1e_record.get("status") != expected:
        return f"the frozen G1-E record says `{g1e_record.get('status')}` but its own verdicts give `{expected}`"
    if report.get("status") != expected:
        return f"the report says `{report.get('status')}` but the frozen G1-E verdicts give `{expected}`"
    if report.get("baseline") != BASELINE_LABELS.get(expected, BASELINE_LABEL_DEFAULT):
        return f"the report does not carry the baseline label of `{expected}`"
    if report.get("unjudged") != []:
        return "the report names unjudged files, or no `unjudged` list"
    if any(report.get(key) != g1e_record.get(key) for key in ("upstreamMerge", "e3Parent", "e3Commit")):
        return "the report's upstream merge or E3 commits differ from the frozen G1-E record"
    frozen_manifest_sha = bi.manifest_sha256(g1e_manifest)
    if report.get("manifestSha256") != frozen_manifest_sha or report.get("g1eManifestSha256") != frozen_manifest_sha:
        return "the report's reference manifest is not the frozen G1-E one"
    traced = report.get("traced")
    if type(traced) is not int or traced != len(g1e_manifest):
        return "the report's traced file count is not the frozen G1-E record's"
    seen = report.get("listed")
    if not isinstance(seen, list) or not all(isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in seen):
        return "the report's listing is not a list of files"
    if len({entry["path"] for entry in seen}) != len(seen):
        return "the report's listing names a file twice"
    if not _same_json(seen, frozen_listing(g1e_record)):
        return "the report's listing is not the frozen G1-E listing, in path order"
    own = record_problems(g1e_record, g1e_manifest)
    if own:
        return own[0]
    given = report.get("unreviewed")
    if not _same_json(given, frozen_unreviewed(g1e_record)):
        return "the report's unreviewed files are not the ones the frozen verdicts leave, in path order"
    return None


def record_problems(g1e_record: dict, g1e_manifest: list[dict]) -> list[str]:
    """The facts of the frozen record alone (no report) that make every report against it refused: a listed file's reference digest that is not the one the reference manifest holds, a listed file
    without a complete two-reviewer judgement, and a `disclosed_not_passed` record with no `other` verdict. `read_g1e` runs it for a record whose status is attributable, so `g1e-register`, the pre-run
    check and `decide` all refuse a record that could never be attributed (rf4a K2); `_refusal` uses the same function."""
    reference = {m["path"]: m["sha256"] for m in g1e_manifest}
    out = []
    for entry in g1e_record["changedFiles"]:
        if entry["referenceSha256"] != reference.get(entry["path"]):
            out.append(f"the frozen reference digest of {entry['path']} is not the one its manifest holds")
        elif not _fully_judged(entry, list(entry.get("verdicts") or [])):
            out.append(f"{entry['path']} has no complete two-reviewer judgement in the frozen record")
    if frozen_status(g1e_record) == bi.DISCLOSED_NOT_PASSED and not any(v.get("verdict") == "other" for entry in g1e_record["changedFiles"] for v in entry["verdicts"]):
        out.append("the frozen record has no `other` verdict, so it is not a disclosed report")
    return out


def attribution_verdict(report: object, g1e_record: dict | None = None, g1e_manifest: list[dict] | None = None) -> tuple[bool, str | None]:
    """(attributable, reason). The reason is None when the value is true and the first refusal otherwise. Never raises: a malformed report or record is a refusal. Every status needs the sealed, registered
    G1-E record: with none (`g1e_record` or `g1e_manifest` missing) nothing is attributable, `identical` included."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        return False, "the report is not an acr-g3-python-arm-identity/1 record"
    if report.get("referenceMatchesG1E") is not True:
        return False, "the Python-arm reference manifest is not the one G1-E froze"
    status = report.get("status")
    if status not in ATTRIBUTABLE_STATUSES:
        return False, f"the Python arm is `{status}`: a change without a complete judgement"
    if not isinstance(g1e_record, dict) or not isinstance(g1e_manifest, list):
        return False, "the report cannot be checked without the sealed frozen G1-E record"
    try:
        reason = _refusal(report, g1e_record, g1e_manifest)
    except (KeyError, TypeError, AttributeError, ValueError):
        return False, "the frozen G1-E record or the report is malformed"
    return (reason is None), reason


def attributable(report: object, g1e_record: dict | None = None, g1e_manifest: list[dict] | None = None) -> bool:
    """True only if the report equals the facts rebuilt from the sealed, registered frozen G1-E record (`_refusal`) and shows no Python-arm change without a complete judgement: status `identical`,
    `refactor_reviewed` or `disclosed_not_passed`, the status being the one the record's verdicts give. Without the record and its manifest it cannot be checked, so it is false. A malformed report is false."""
    return attribution_verdict(report, g1e_record, g1e_manifest)[0]


def is_registered_digest(value: object) -> bool:
    """True only for a lowercase 64-hex string, whole: no trailing newline, no padding."""
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def registered_manifest_sha256(environ=None) -> str | None:
    """The registered G1-E bundle manifest digest the run was given (`REGISTERED_ENV`), or None when it is absent or not a lowercase SHA-256."""
    value = (os.environ if environ is None else environ).get(REGISTERED_ENV)
    return value if is_registered_digest(value) else None


def load_g1e(g1e_dir: Path, manifest_sha256: str | None) -> tuple[dict, list[dict]]:
    """The G1-E `baseline-identity.json` record and the reference manifest it points at, both read through the sealed bundle in `g1e_dir` (`g1e.bundle.Bundle`: the bundle manifest must hash to
    `manifest_sha256`, and each file to its listed digest). `manifest_sha256` is the REGISTERED digest, given from outside the bundle: it is never defaulted to the hash of the manifest file in `g1e_dir`, because a
    bundle that was rewritten and resealed would then vouch for itself. The record's `referenceManifest` and its file list, and each listed file's `diff`, must resolve in the bundle and their bytes must
    hash to their recorded digests. Raises `FrozenRecordError` (a ValueError carrying the reason) for a missing digest and for any unreadable, unsealed, tampered or malformed input."""
    g1e_dir = Path(g1e_dir)
    if not is_registered_digest(manifest_sha256):
        raise FrozenRecordError(f"no registered G1-E bundle manifest SHA-256 was given ({REGISTERED_ENV}): the bundle cannot vouch for itself")
    try:
        sealed = g1e_bundle.Bundle(g1e_dir, manifest_sha256)
        record = sealed.read_json(RECORD_NAME)
        if not isinstance(record, dict) or not isinstance(record.get("referenceManifest"), dict):
            raise FrozenRecordError(f"{RECORD_NAME} is not a record with a referenceManifest")
        if not sealed.resolves(record["referenceManifest"]):
            raise FrozenRecordError("the record's referenceManifest does not resolve in the sealed bundle with its digest")
        manifest = sealed.read_json(record["referenceManifest"]["path"])
        _check_shape(record, manifest, sealed)
    except FrozenRecordError:
        raise
    except g1e_bundle.BundleError as error:
        raise FrozenRecordError(f"the sealed G1-E bundle refused a read: {error}") from None
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as error:
        raise FrozenRecordError(f"the frozen G1-E record is unreadable or malformed ({type(error).__name__})") from None
    return record, manifest


def _check_shape(record: dict, manifest: object, sealed: g1e_bundle.Bundle) -> None:
    """The parts of the frozen record attribution reads must be present with their types; the file list and each stored diff must resolve in the sealed bundle, and the bytes read must hash to the
    digest the record names. A listed path may appear once. The traced count (the file list's length) must be the reference manifest's."""
    if not (isinstance(manifest, list) and all(isinstance(m, dict) and isinstance(m.get("path"), str) and isinstance(m.get("sha256"), str) for m in manifest)):
        raise FrozenRecordError("the reference manifest is not a list of {path, sha256} entries")
    if not sealed.resolves(record.get("pythonArmFileList")):
        raise FrozenRecordError("the record's pythonArmFileList does not resolve in the sealed bundle with its digest")
    traced = sealed.read_json(record["pythonArmFileList"]["path"])
    if not isinstance(traced, list) or len(traced) != len(manifest):
        raise FrozenRecordError("the record's traced file count is not the reference manifest's")
    if not (isinstance(record.get("e3Set"), list) and all(isinstance(p, str) for p in record["e3Set"])):
        raise FrozenRecordError("the record has no e3Set list")
    if not all(isinstance(record.get(key), str) for key in ("upstreamMerge", "e3Parent", "e3Commit")):
        raise FrozenRecordError("the record lacks its upstream merge or E3 commits")
    changed = record.get("changedFiles")
    if not isinstance(changed, list):
        raise FrozenRecordError("the record has no changedFiles list")
    for c in changed:
        if not (isinstance(c, dict) and all(isinstance(c.get(key), str) for key in ("path", "loadedSha256", "referenceSha256")) and isinstance(c.get("verdicts"), list)
                and all(isinstance(v, dict) for v in c["verdicts"])):
            raise FrozenRecordError("a changedFiles entry lacks its path, digests or verdicts")
        if not sealed.resolves(c.get("diff")):
            raise FrozenRecordError(f"the stored diff of {c['path']} does not resolve in the sealed bundle with its digest")
        sealed.read(c["diff"]["path"])      # the bytes themselves: `resolves` made the recorded digest the manifest's, and `read` raises (BundleError) for a missing file or bytes that do not hash to it
    if len({c["path"] for c in changed}) != len(changed):
        raise FrozenRecordError("the record lists a file twice")


class G1eRead(NamedTuple):
    """What one pass over the registered G1-E bundle gave. `frozen` = (record, reference manifest) or None; `pairs` = the per-pair data (None when not read or unreadable); `pairs_read` = whether it was;
    `problems` = every reason the bundle cannot be used as it is (the load refusal, a status that contradicts the record's own verdicts, a record no report could match, unreadable per-pair data, an
    unreadable host class), empty when it can; `host` = the host class G1-E ran on, read from the sealed bundle (None when not read or unreadable)."""
    frozen: tuple[dict, list[dict]] | None
    pairs: object
    pairs_read: bool
    problems: list[str]
    host: dict | None = None


def read_g1e(g1e_dir: Path | None, manifest_sha256: str | None, pairs: Callable[[Path, str], object] | None = None, host: Callable[[Path, str], dict] | None = None) -> G1eRead:
    """The ONE reading of the G1-E bundle that `decide`, the pre-run check and `g1e-register` share. Every read takes the registered digest (`manifest_sha256`, never a hash of the manifest file in `g1e_dir`):
    `load_g1e` for the record and its reference manifest, then (when `pairs` is given: `bundle.g1e_pairs`) the per-pair data and (when `host` is given: `bundle.g1e_host`) the host class. It never raises: a
    missing, wrong or malformed digest, an unsealed or tampered bundle, a record whose `status` is not the one its own verdicts give (`frozen_status`), a record of an attributable status that no report
    could match (`record_problems`: the reference digests, the two-reviewer judgements, the `other` verdict of a disclosed record), per-pair data that cannot be read and a host class that cannot be read
    each come back as a reason. With no loadable record the per-pair data and the host class are not read."""
    if g1e_dir is None:
        return G1eRead(None, None, False, ["no G1-E bundle was given"])
    try:
        frozen = load_g1e(Path(g1e_dir), manifest_sha256)      # FrozenRecordError is a ValueError carrying its reason
    except READ_ERRORS as error:
        return G1eRead(None, None, False, [str(error)])
    problems = []
    try:
        derived = frozen_status(frozen[0])
        if frozen[0].get("status") != derived:
            problems.append(f"the frozen G1-E record says `{frozen[0].get('status')}` but its own verdicts give `{derived}`")
        if derived in ATTRIBUTABLE_STATUSES:
            problems += record_problems(frozen[0], frozen[1])
    except READ_ERRORS:
        problems.append("the frozen G1-E record's status cannot be derived from its verdicts")
    data, pairs_read = None, False
    if pairs is not None:
        try:
            data, pairs_read = pairs(Path(g1e_dir), manifest_sha256), True
        except READ_ERRORS as error:
            problems.append(f"the per-pair data cannot be read ({type(error).__name__}: {str(error)[:200]})")
    g1e_host = None
    if host is not None:
        try:
            g1e_host = host(Path(g1e_dir), manifest_sha256)
        except READ_ERRORS as error:
            problems.append(f"the host class cannot be read ({type(error).__name__}: {str(error)[:200]})")
    return G1eRead(frozen, data, pairs_read, problems, g1e_host)


def bundle_problems(g1e_dir: Path, manifest_sha256: str | None, pairs: Callable[[Path, str], object] | None = None, host: Callable[[Path, str], dict] | None = None) -> list[str]:
    """Why the G1-E bundle named for a run cannot be used, empty when it can (`read_g1e`'s problems, each prefixed `G1-E bundle: `). This is the pre-run check: a bad or unregistered bundle stops the run
    before any measurement, with a reason and never with an exception."""
    return [f"G1-E bundle: {problem}" for problem in read_g1e(g1e_dir, manifest_sha256, pairs, host).problems]


def registration(g1e_dir: Path, pairs: Callable[[Path, str], object] | None = None, host: Callable[[Path, str], dict] | None = None) -> dict:
    """What the manager registers once a G1-E bundle is sealed: the SHA-256 of its `bundle-manifest.json`, with the ledger line to append and the environment variable the G3 run reads it from. The bundle is
    checked first, against that very digest, with the checks `decide` and the pre-run check use (`read_g1e`: loads under the digest, the record's status is the one its verdicts give, the record is one a
    report could match, the per-pair data reads through `bundle.g1e_pairs`, or `pairs`, and the host class through `bundle.g1e_host`, or `host`): a bundle any of them would refuse cannot be registered
    (`FrozenRecordError` with every reason). The digest is computed here, at seal time, and then travels outside the bundle."""
    if pairs is None:      # the real readers; a caller that names its own `pairs` (a test) names its own `host` too, or none
        from tests.cli_regression.support.g3 import bundle as g3bundle      # lazily: the G3 bundle module is heavy and `decide` imports it anyway
        pairs, host = g3bundle.g1e_pairs, host or g3bundle.g1e_host
    g1e_dir = Path(g1e_dir)
    digest = g1e_bundle.sha256_bytes((g1e_dir / g1e_bundle.MANIFEST_NAME).read_bytes())
    read = read_g1e(g1e_dir, digest, pairs, host)
    if read.problems:
        raise FrozenRecordError("; ".join(read.problems))
    record, manifest = read.frozen
    return {"schema": REGISTRATION_SCHEMA, "bundleManifestSha256": digest, "environment": REGISTERED_ENV, "status": record.get("status"), "upstreamMerge": record["upstreamMerge"],
            "listedFiles": len(record["changedFiles"]), "tracedFiles": len(manifest), "hostClass": read.host,
            "ledgerLine": f"<date> manager registers G1-E bundle manifest SHA-256 {digest} ({REGISTERED_ENV}); status {record.get('status')}; sealed by the G1-E seal step, not derived by G3 from the bundle it reads"}


def run(root: Path, g1e_dir: Path, out: Path, *, manifest_sha256: str | None, python: str | None = None, trace: Callable[..., list[str]] = bi.trace_python_arm,
        identity: Callable[..., dict] = bi.identity_record) -> dict:
    """Trace the Python arm of `root`, take the listing against the G1-E reference and write the report. `manifest_sha256` is the registered G1-E bundle digest. Raises (the step fails and the run stops) when the
    tool cannot run unchanged or the bundle is not the registered one."""
    g1e_record, g1e_manifest = load_g1e(g1e_dir, manifest_sha256)
    files = trace(Path(root), python or sys.executable)
    record = identity(Path(root), files, g1e_record["e3Set"])
    report = build_report(record, g1e_record, g1e_manifest)
    Path(out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return report
