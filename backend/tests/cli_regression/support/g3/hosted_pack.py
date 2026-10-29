r"""The harness pack tool (addendum F1, design section 5 option O2). Standard library only, so the job can run it from the checked-out side branch with any Python 3.

    hosted_pack.py candidates REPO [--out FILE]                   the files the rules pick, before the allowlist (what a reviewer reads to write the list)
    hosted_pack.py list    REPO [--out FILE]                      the harness file list (repo-relative, sorted) chosen by the explicit rules below and held to the allowlist
    hosted_pack.py archive REPO OUT_TAR [--list FILE] [--out FILE] writes the deterministic archive and prints/records its SHA-256
    hosted_pack.py digest  DIR [--out FILE]                       the same archive digest, recomputed from a checked-out tree (every file under DIR but `.git`; a symlink is refused)
    hosted_pack.py tree    REPO --dry-run [--list FILE] [--out FILE]   the tree SHA of the orphan commit, with no object and no ref written
    hosted_pack.py tree    REPO [--list FILE] [--out FILE]        writes the blobs, the tree and ONE parentless commit; prints the commit SHA. It updates no ref and never checks anything out

Every command that packs (list, archive, tree) refuses any file the committed allowlist (`g3/hosted_pack_allowlist.txt`, `--allowlist FILE` to name another) does not name, any allowlisted file that is absent
and any symlink: the side branch is public, so what it carries is a closed, reviewed list and not a directory rule.

The archive is a USTAR tar of the entries sorted by path, with fixed mtime, uid, gid, mode and no user or group names, so it carries no host name and the same bytes always give the same digest. The tree
is `* -text` (a virtual `.gitattributes`), so a Windows checkout with any `core.autocrlf` gives the same bytes. The approval excerpt is stored at the ledger path (`LEDGER_DEST`), where the unchanged
`approval_problems` reads the ledger. Git is used through plumbing only (`hash-object`, a temporary index named by `GIT_INDEX_FILE` in this tool's own subprocess environment, `update-index --index-info`,
`write-tree`, `commit-tree`): the repository's own index, HEAD, branches and working tree are never touched.

Privacy gate contract (rf4i K6). `entries` refuses the pack when `privacy_findings` returns anything. The gate is a heuristic, so this table is its whole promise: every spelling in it is caught, and anything the gate
cannot decode to a fixed point is refused (fail closed, row R11). `<n>` stands for any name that is not a generic one (`GENERIC_NAMES`). A spelling outside the table found clean in the pack is a note, not a defect; one
found in the pack is a leak whatever the gate says. The last column names the tests that pin the row (`file::function[case]`, or a whole parametrized function); `test_g3h_rf4i.py::test_the_contract_table_names_tests_that_exist` checks that each of them exists.

| Row | Spelling guaranteed to be caught | Kind | Tests |
|---|---|---|---|
| R01 | `/Users/<n>`, `/home/<n>`, `C:\Users\<n>`, `C:/Users/<n>`, any letter case, a doubled or JSON-escaped separator, with or without a separator after the name | home-path | `test_g3h_rf4i.py::test_contract_row[R01]`; `test_g3h_rf4g.py::test_the_gate_names_a_home_directory_path_in_every_spelling_by_file_kind_and_count`; `test_g3h_rf4h.py::test_the_gate_names_a_home_path_in_every_spelling_the_reviewers_listed[posix, no trailing separator, end of text]`; `test_g3h_rf4j.py::test_a_long_run_of_separators_is_scanned_in_linear_time` |
| R02 | a name with a space (`/Users/<first> <last>`), with or without a separator after it, also when its first word is generic (`runner <last>`) | home-path | `test_g3h_rf4i.py::test_contract_row[R02]`; `test_g3h_rf4h.py::test_the_gate_names_a_home_path_in_every_spelling_the_reviewers_listed[posix, a space in the name]` |
| R03 | no drive letter: `\Users\<n>`, UNC `\\host\C$\Users\<n>`, `\\wsl.localhost\<distro>\home\<n>` | home-path | `test_g3h_rf4i.py::test_contract_row[R03]` |
| R04 | `~<n>/` and `~<n>\` | home-path | `test_g3h_rf4i.py::test_contract_row[R04]`; `test_g3h_rf4h.py::test_the_gate_names_a_home_path_in_every_spelling_the_reviewers_listed[tilde user]` |
| R05 | JSON escapes (`\/`, `\\`, `\uXXXX`) and percent escapes (`%2F`, `%5C`, any case), each nested up to `MAX_UNESCAPE_ROUNDS` (8) layers, and the two mixed | home-path | `test_g3h_rf4i.py::test_contract_row[R05]`; `test_g3h_rf4i.py::test_escapes_are_undone_to_a_fixed_point_within_the_round_limit`; `test_g3h_rf4h.py::test_the_gate_names_a_home_path_in_every_spelling_the_reviewers_listed[percent, lower case (rf4g B1 probe)]` |
| R06 | the path as UTF-16, little or big endian, with a byte order mark or after an odd byte | home-path | `test_g3h_rf4i.py::test_contract_row[R06]`; `test_g3h_rf4h.py::test_the_gate_names_a_home_path_in_every_spelling_the_reviewers_listed[utf-16 after an odd byte]` |
| R07 | the path as a hex run of 8 or more digits (either case, either nibble alignment, of UTF-8 or UTF-16), or as hex pairs separated by spaces or colons | home-path | `test_g3h_rf4i.py::test_contract_row[R07]`; `test_g3h_rf4h.py::test_the_gate_names_a_home_path_in_every_spelling_the_reviewers_listed[hex]` |
| R08 | the path as a base64 run of 6 or more characters (standard or URL-safe, any alignment, padded or not, glued to other letters, of UTF-8, UTF-16 or a percent-encoded path), also wrapped across lines | home-path | `test_g3h_rf4i.py::test_contract_row[R08]`; `test_g3h_rf4i.py::test_a_base64_run_glued_to_other_letters_and_a_run_read_from_the_unescaped_text_are_decoded`; `test_g3h_rf4h.py::test_the_gate_names_a_home_path_in_every_spelling_the_reviewers_listed[base64]` |
| R09 | a NUL or a zero-width character (U+00AD, U+180E, U+200B to U+200F, U+2060 to U+2064, U+FEFF) between any two characters of the path or the name | home-path, user-name | `test_g3h_rf4i.py::test_contract_row[R09]`; `test_g3h_rf4j.py::test_every_invisible_character_hides_neither_a_path_nor_a_name` |
| R10 | the local user name, any letter case, plain, with a percent or `\uXXXX` escaped letter, as hex, as base64, as UTF-16, or as spaced hex | user-name | `test_g3h_rf4i.py::test_contract_row[R10]`; `test_g3h_rf4h.py::test_the_gate_names_the_local_user_name_in_every_encoding_the_reviewers_listed` |
| R11 | fail closed: text that still changes after `MAX_UNESCAPE_ROUNDS` (8) rounds of unescaping (nested escapes one layer past the limit, mixed nesting past it, or the same inside a hex or base64 run) is refused whatever it holds | decode-limit | `test_g3h_rf4i.py::test_contract_row[R11]`; `test_g3h_rf4i.py::test_escapes_are_undone_to_a_fixed_point_within_the_round_limit`; `test_g3h_rf4i.py::test_text_past_the_round_limit_refuses_the_pack_and_names_no_text` |
| R12 | NOT a leak, so it passes: a generic name (`runner`, `runneradmin`, the Windows short form `runner~1`, `public`, ...), a placeholder (`<name>`, `$USER`), a bare `~/`, and a generic name followed by sentence punctuation | none | `test_g3h_rf4i.py::test_contract_row[R12]`; `test_g3h_rf4h.py::test_the_gate_lets_generic_and_unrelated_content_through`; `test_g3h_rf4g.py::test_the_gate_lets_a_hosted_runner_home_a_placeholder_and_a_variable_through` |
| R13 | the gate sits behind `entries`: `entries` raises and `archive` exits 2 without writing the archive | home-path, user-name, decode-limit | `test_g3h_rf4h.py::test_entries_refuse_an_encoded_home_path_before_any_archive_is_written`; `test_g3h_rf4g.py::test_entries_refuse_a_pack_with_a_home_path_or_the_local_name_and_report_counts_only`; `test_g3h_rf4i.py::test_text_past_the_round_limit_refuses_the_pack_and_names_no_text`; `test_g3h_rf4j.py::test_a_user_name_refuses_the_archive_command_and_prints_no_text` |

Not in the table, so not promised: base32, rot13, gzip or zlib, HTML entities, `\xHH` and octal escapes, split strings, other Unicode normalisations, UTF-32, hex or base64 of a hex or base64 run, `C:\Documents and Settings\<n>`,
a path broken by a line break, and runs shorter than 8 hex digits or 6 base64 characters. The pre-push scan by an independent reader of the pack bytes stays the control for these.
"""
from __future__ import annotations

import binascii
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

GITATTRIBUTES = ".gitattributes"
GITATTRIBUTES_BYTES = b"* -text\n"
EXCERPT_SRC = "backend/tests/cli_regression/support/g3/hosted_inputs/approval-excerpt.md"
LEDGER_DEST = ".scratch/agent-cli-runtime-evaluation/progress.md"
SUPPORT = "backend/tests/cli_regression/support/"
ALLOWLIST_REL = SUPPORT + "g3/hosted_pack_allowlist.txt"
RELATIVE_PATH = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/-]*")
INCLUDE_PREFIXES = (SUPPORT + "g1e/", SUPPORT + "g3/")
INCLUDE_FILES = (SUPPORT + "g1e_peer.py", SUPPORT + "g1e_counters_gate.py", SUPPORT + "backend_entry.py", SUPPORT + "baseline_peer.py", SUPPORT + "reader_counters_v2.py", "backend/tests/test_g1e_peer.py", "backend/tests/test_g1e_reference.py", "tests/cli/g3-launch-plan-golden.json", "tests/cli/g3Matrix.test.ts", "tests/cli/g3hPackagedDiag.test.ts", "tests/cli/g3hPackagedWindows.test.ts", "tests/cli/g3hConptyProbe.test.ts",      # rh16 windows-4c: the not-evidence ConPTY probe is a vitest file the Windows rehearsal runs
                 "tests/fixtures/cli-regression/g1e-burst-golden.json", "tests/fixtures/cli-regression/g1e-ledger-golden.json", "tests/fixtures/cli-regression/g1e-schedule-golden.json",
                 "tests/fixtures/cli-regression/runtime-workload-g1e.json")
# The two evidence descriptors the reader-benchmark and limits-report tests gate on (rf4e G2). `.scratch/` is ignored by git here, so the listing cannot find them: `harness_files` adds them by name when
# they are on disk. The pack carries these two files only, not the fixtures they name.
EVIDENCE_FILES = (".scratch/agent-cli-runtime-evaluation/evidence/e3/reader-fixture-pins.json", ".scratch/agent-cli-runtime-evaluation/evidence/e3/reader-fixtures.json")
DESCRIPTOR_SRC = EVIDENCE_FILES[1]
# rf4g J1: the author's `reader-fixtures.json` names the fixture files by absolute path of the author's machine. The pack never carries those values: `entries` packs a generated copy at the same path,
# with the `path` and `golden` of each reader replaced by these placeholders (the runner stages its own descriptor over it before the reader cases run, rf4f H1). Digests and counts are kept.
PLACEHOLDER_ROOT = "runner-staged"
TS_PREFIXES = ("tests/cli/g1e", "tests/cli/g3")
EXCLUDE_PARTS = ("__pycache__", "/target/", "/node_modules/")
EXCLUDE_SUFFIXES = (".pyc", ".log")
FIXED_MTIME = 0
MODE = 0o644
IDENTITY = {"GIT_AUTHOR_NAME": "eval-harness", "GIT_AUTHOR_EMAIL": "eval-harness@example.invalid", "GIT_AUTHOR_DATE": "2026-10-29T00:00:00+0000",
            "GIT_COMMITTER_NAME": "eval-harness", "GIT_COMMITTER_EMAIL": "eval-harness@example.invalid", "GIT_COMMITTER_DATE": "2026-10-29T00:00:00+0000"}
COMMIT_MESSAGE = "eval-only harness for G3-r2-hosted. Never merge."

Git = Callable[..., bytes]


def run_git(repo: str | Path, *args: str, stdin: bytes | None = None, env: dict | None = None) -> bytes:
    done = subprocess.run(["git", "-C", str(repo), *args], input=stdin, capture_output=True, check=False, env={**os.environ, **(env or {})})
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {done.stderr.decode('utf-8', 'replace')[:200]}")
    return done.stdout


def wanted(path: str) -> bool:
    """The explicit rules: g1e/ and g3/ of the harness support, the peer and counters files, the two tracked support files the harness extends (backend_entry, baseline_peer), the two untracked G1-E test files that correctness rows run (`test_g1e_peer.py`, `test_g1e_reference.py`; the other test files of the rows are tracked at the freeze head), the TS runners (not their
    unit tests, except the runner `g3Matrix.test.ts` itself), the goldens and the workload fixture. Build output and caches are never included."""
    if any(part in "/" + path for part in EXCLUDE_PARTS) or path.endswith(EXCLUDE_SUFFIXES):
        return False
    if path in INCLUDE_FILES or path in EVIDENCE_FILES or path.startswith(INCLUDE_PREFIXES):
        return True
    return path.startswith(TS_PREFIXES) and path.endswith(".ts") and not path.endswith(".test.ts")


def harness_files(repo: str | Path, git: Git = run_git) -> list[str]:
    """Untracked files plus tracked files modified against HEAD, plus the named evidence descriptors that are on disk, filtered by `wanted`, sorted. The two git lists are NUL-separated."""
    untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")
    modified = git(repo, "diff", "--name-only", "HEAD", "-z").decode("utf-8").split("\0")
    named = [path for path in EVIDENCE_FILES if (Path(repo) / path).is_file()]
    return sorted({path for path in untracked + modified + named if path and wanted(path)})


def valid_relative(path: str) -> bool:
    return bool(RELATIVE_PATH.fullmatch(path)) and not path.startswith("/") and ".." not in path.split("/")


def load_allowlist(path: str | Path) -> list[str]:
    """The closed, reviewed list of repo-relative harness files: one path per line, sorted, unique, a final newline, no comments or blank lines."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"the allowlist {path} cannot be read: {error.__class__.__name__}") from error
    if not text.endswith("\n"):
        raise ValueError("the allowlist must end with a newline")
    files = text.split("\n")[:-1]
    for line in files:
        if not valid_relative(line):
            raise ValueError(f"the allowlist holds a line that is not a repo-relative path: {line[:60]!r}")
    if len(set(files)) != len(files):
        raise ValueError("the allowlist has a duplicate path")
    if files != sorted(files, key=lambda item: item.encode("utf-8")):
        raise ValueError("the allowlist is not sorted")
    return files


def checked_files(repo: str | Path, git: Git = run_git, *, listing: str | Path | None = None, allowlist: str | Path | None = None) -> list[str]:
    """The harness files to pack, after the allowlist: what the rules (or an explicit `listing`) pick must equal the committed allowlist exactly. A file the list does not name, a listed file that is
    absent and a symlink all stop the pack; nothing is published that a reviewer did not approve."""
    allowed = load_allowlist(allowlist or Path(repo) / ALLOWLIST_REL)
    picked = [line for line in Path(listing).read_text(encoding="utf-8").split("\n") if line] if listing else harness_files(repo, git)
    extra = sorted(set(picked) - set(allowed))
    if extra:
        raise ValueError(f"not in the allowlist: {extra[:5]}")
    missing = sorted(set(allowed) - set(picked))
    if missing:
        raise ValueError(f"allowlisted but absent: {missing[:5]}")
    for relative in picked:
        if (Path(repo) / relative).is_symlink():
            raise ValueError(f"a symlink cannot be packed: {relative}")
    return sorted(picked, key=lambda item: item.encode("utf-8"))


def placeholder_descriptor(raw: bytes) -> bytes:
    """The packed form of the reader descriptor (rf4g J1): the same readers, digests and counts, with `path` and `golden` replaced by relative placeholders, so no machine path leaves the author's tree."""
    try:
        document = json.loads(raw.decode("utf-8"))
    except ValueError as error:
        raise ValueError("the reader descriptor is not JSON") from error
    if not isinstance(document, dict) or not all(isinstance(entry, dict) for entry in document.values()):
        raise ValueError("the reader descriptor is not a mapping of readers")
    packed = {name: {**entry, "path": f"{PLACEHOLDER_ROOT}/{name}/fixture", "golden": f"{PLACEHOLDER_ROOT}/{name}-golden.json"} for name, entry in document.items()}
    return (json.dumps(packed, indent=1) + "\n").encode("utf-8")


# rf4g J2: the pack-wide privacy gate. A path under a user's home directory (`/Users/<name>/`, `/home/<name>/`, `C:\Users\<name>\`, with either slash and JSON-escaped backslashes) or the local user name
# in any packed file stops the pack. Findings carry the file, the kind and a count, never the matched text.
# rf4h: the check also runs on every form an entry can be decoded into (`decoded_forms`): the UTF-8 text, UTF-16 text, the text with its JSON (`\/`, `\\`, `\uXXXX`) and percent escapes undone, and the text
# inside hex and base64 runs. A home path needs no trailing separator and `~name/` counts. A bare `~/` names nobody and passes. Counts are the largest over the forms.
# rf4i: the contract table in the module docstring lists what is guaranteed. Escapes are undone to a fixed point; text that still changes after `MAX_UNESCAPE_ROUNDS` rounds fails closed (`DecodeLimit`). The whole
# candidate name, spaces included, is judged before the generic-name check (a continuation word must start with a letter, digit or `_` and ends the name at sentence punctuation). The separator before `Users` or `home`
# may be any `/` or `\` (no drive letter needed). NUL and zero-width characters are removed before matching.
_NAME = r"[^/\\\s\"'<>:*{}$]+"
# rf4j: the lookbehind lets a match start only at the first separator of a run, so a long run of `/` or `\` with no `Users` after it is scanned in linear time (it was quadratic).
HOME_PATH = re.compile(rf"(?<![\\/])[\\/]+(?:Users|home)[\\/]+(?P<name>{_NAME}(?: (?=\w){_NAME})*)", re.IGNORECASE)
TILDE_USER = re.compile(r"(?<![\w~/\\.-])~(?P<tilde>[A-Za-z_][\w.-]*)(?=[\\/])")
NAME_TRAILING = ".,;)]!?"
JSON_ESCAPE = re.compile(r"\\(?:([\\/])|u([0-9a-fA-F]{4}))")
HEX_RUN = re.compile(r"[0-9A-Fa-f]{8,}")
HEX_SEPARATOR = re.compile(r"(?<=[0-9A-Fa-f]{2})[ :]+(?=[0-9A-Fa-f]{2})")
BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{6,}")
BASE64_BREAK = re.compile(r"(?<=[A-Za-z0-9+/_=-])[ \t]*(?:\r?\n|\\r\\n|\\n)[ \t]*(?=[A-Za-z0-9+/_-])")
MAX_UNESCAPE_ROUNDS = 8
_URLSAFE = str.maketrans("-_", "+/")
_INVISIBLE = dict.fromkeys(map(ord, "\0­᠎​‌‍‎‏⁠⁡⁢⁣⁤﻿"))
GENERIC_NAMES = frozenset({"runner", "runneradmin", "runner~1", "user", "users", "name", "username", "me", "root", "default", "public", "shared", "example", "someone", "yourname", "foo", "bar", "test", "tester", "ubuntu", "vsts", "admin", "home"})
MIN_NAME_LENGTH = 4


def local_names() -> list[str]:
    """The local user name(s) the gate looks for: the login name the environment and the account database give, minus generic names a hosted runner uses (`runner`) and names too short to be a signal. The home
    directory's own last part is not used: a test environment may point HOME at a scratch directory with a generic name."""
    found = {os.environ.get("USER", ""), os.environ.get("LOGNAME", ""), os.environ.get("USERNAME", "")}
    try:
        import pwd
        found.add(pwd.getpwuid(os.getuid()).pw_name)
    except (ImportError, KeyError, AttributeError):
        pass      # no account database (Windows): the environment names are what there is
    return sorted(name.lower() for name in found if len(name) >= MIN_NAME_LENGTH and name.lower() not in GENERIC_NAMES)


class DecodeLimit(ValueError):
    """An entry still changes after `MAX_UNESCAPE_ROUNDS` rounds of unescaping: the gate cannot say what it holds, so it refuses (rf4i K1)."""


def _unescape_once(text: str) -> str:
    if "\\" not in text and "%" not in text:
        return text
    return unquote(JSON_ESCAPE.sub(lambda match: match.group(1) or chr(int(match.group(2), 16)), text))


def _unescape_chain(text: str) -> list[str]:
    """`text`, then the text with JSON escapes (`\\\\`, `\\/`, `\\uXXXX`) and percent escapes undone, again and again until nothing changes. Every round is shorter than the one before, so this ends; if another round would
    still change the text after `MAX_UNESCAPE_ROUNDS`, it raises `DecodeLimit` (fail closed)."""
    chain = [text]
    for _ in range(MAX_UNESCAPE_ROUNDS):
        undone = _unescape_once(chain[-1])
        if undone == chain[-1]:
            return chain
        chain.append(undone)
    if _unescape_once(chain[-1]) != chain[-1]:
        raise DecodeLimit("more escape layers than the gate reads")
    return chain


def _utf16_texts(data: bytes) -> list[str]:
    """The bytes read as UTF-16, little and big endian, from either byte offset; only when they hold a NUL byte, which UTF-16 text of an ASCII path always does."""
    if b"\0" not in data:
        return []
    texts = []
    for offset in (0, 1):
        chunk = data[offset:]
        chunk = chunk[:len(chunk) // 2 * 2]
        texts += [chunk.decode("utf-16-le", "replace"), chunk.decode("utf-16-be", "replace")]
    return texts


def _hex_pieces(text: str) -> list[bytes]:
    """The bytes of every hex run, read as written and with the spaces and colons between hex pairs removed (a `hexdump` or `xxd` style dump)."""
    pieces = []
    for source in dict.fromkeys((text, HEX_SEPARATOR.sub("", text))):
        for run in HEX_RUN.findall(source):
            for start in (0, 1):      # a run may begin in the middle of a byte
                body = run[start:]
                pieces.append(bytes.fromhex(body[:len(body) // 2 * 2]))
    return pieces


def _base64_pieces(text: str) -> list[bytes]:
    """The bytes of every base64 run, read as written and with the line breaks inside a wrapped block removed."""
    pieces = []
    for source in dict.fromkeys((text, BASE64_BREAK.sub("", text))):
        for run in BASE64_RUN.findall(source):
            run = run.translate(_URLSAFE)
            for start in range(4):      # a run may begin in the middle of a 4-character group
                body = run[start:]
                if len(body) % 4 == 1:
                    body = body[:-1]
                if len(body) >= 2:
                    pieces.append(binascii.a2b_base64(body + "=" * (-len(body) % 4)))
    return pieces


def _piece_texts(pieces: list[bytes]) -> list[str]:
    """Each kind of decoded piece joined into one text per reading (UTF-8, and UTF-16 when a piece holds a NUL byte): one form to check, not one per run."""
    if not pieces:
        return []
    texts = ["\n".join(piece.decode("utf-8", "replace") for piece in pieces)]
    for encoding in ("utf-16-le", "utf-16-be"):
        wide = [piece for piece in pieces if b"\0" in piece]
        if wide:
            texts.append("\n".join(piece[:len(piece) // 2 * 2].decode(encoding, "replace") for piece in wide))
    return texts


def decoded_forms(data: bytes) -> list[str]:
    """Every text an entry's bytes can be read as, without duplicates: the UTF-8 and UTF-16 readings, each with its JSON and percent escapes undone, and the decoded content of the hex and base64 runs of the UTF-8
    reading (as written, and with escapes undone). The privacy check runs on each form."""
    utf8 = _unescape_chain(data.decode("utf-8", "replace"))
    forms = list(utf8)
    for text in _utf16_texts(data):
        forms += _unescape_chain(text)
    for source in dict.fromkeys((utf8[0], utf8[-1])):
        for pieces in (_hex_pieces(source), _base64_pieces(source)):
            for text in _piece_texts(pieces):
                forms += _unescape_chain(text)
    return list(dict.fromkeys(forms))


def _candidate_name(raw: str) -> str:
    """The name a home path gives: its words up to the first one that ends in sentence punctuation, minus that punctuation. The whole of it, spaces included, is judged against `GENERIC_NAMES`."""
    words = []
    for word in raw.split(" "):
        words.append(word)
        if word[-1] in NAME_TRAILING:
            break
    return " ".join(words).rstrip(NAME_TRAILING).lower()


def _home_path_count(text: str) -> int:
    count = sum(1 for match in HOME_PATH.finditer(text) if _candidate_name(match.group("name")) not in GENERIC_NAMES)
    return count + sum(1 for match in TILDE_USER.finditer(text) if match.group("tilde").lower() not in GENERIC_NAMES)


def privacy_findings(items: list[tuple[str, bytes]], names: list[str] | None = None) -> list[dict]:
    """[{file, kind, count}] for every packed file with a home-directory path (`home-path`) or a local user name (`user-name`) in any form `decoded_forms` gives (NUL and zero-width characters removed first); a count is
    the largest over the forms. A file that needs more than `MAX_UNESCAPE_ROUNDS` rounds of unescaping is one finding of kind `decode-limit`. `names=None` reads the local ones (`local_names`)."""
    names = local_names() if names is None else [name.lower() for name in names if name]
    out = []
    for path, data in items:
        try:
            forms = [form.translate(_INVISIBLE) for form in decoded_forms(data)]
        except DecodeLimit:
            out.append({"file": path, "kind": "decode-limit", "count": 1})
            continue
        paths = max(_home_path_count(form) for form in forms)
        named = max(sum(form.lower().count(name) for name in names) for form in forms)
        for kind, count in (("home-path", paths), ("user-name", named)):
            if count:
                out.append({"file": path, "kind": kind, "count": count})
    return out


def _problem_lines(findings: list[dict]) -> list[str]:
    return [f"{finding['file']}: {finding['count']} {finding['kind']} match(es)" for finding in findings]


def privacy_problems(items: list[tuple[str, bytes]], names: list[str] | None = None) -> list[str]:
    return _problem_lines(privacy_findings(items, names))


def entries(repo: str | Path, files: list[str]) -> list[tuple[str, bytes]]:
    """(path in the archive, bytes) sorted by path: the listed files read byte-exactly (the reader descriptor as its placeholder form, `placeholder_descriptor`), the excerpt stored at the ledger path, and the
    virtual `.gitattributes`. The privacy gate (`privacy_problems`) runs on the result: a home-directory path, the local user name or text the gate cannot fully decode, in any entry, refuses the pack."""
    out: dict[str, bytes] = {GITATTRIBUTES: GITATTRIBUTES_BYTES}
    for relative in files:
        if relative.startswith("/") or ".." in relative.split("/") or "\\" in relative:
            raise ValueError(f"not a repo-relative path: {relative!r}")
        if (Path(repo) / relative).is_symlink():
            raise ValueError(f"a symlink cannot be packed: {relative}")
        dest = LEDGER_DEST if relative == EXCERPT_SRC else relative
        if dest in out:
            raise ValueError(f"duplicate path {dest}")
        data = (Path(repo) / relative).read_bytes()
        out[dest] = placeholder_descriptor(data) if relative == DESCRIPTOR_SRC else data
    items = sorted(out.items(), key=lambda item: item[0].encode("utf-8"))
    findings = privacy_findings(items)
    if findings:
        raise ValueError(f"privacy gate: {len({finding['file'] for finding in findings})} packed file(s) hold a home-directory path, the local user name or text the gate cannot fully decode: {'; '.join(_problem_lines(findings)[:8])}")
    return items


def read_tree(root: str | Path) -> list[tuple[str, bytes]]:
    """The same entries from a checked-out directory: every file under it except `.git`; a symlink is refused."""
    root = Path(root)
    found = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative.split("/")[0] == ".git":
            continue
        if path.is_symlink():
            raise ValueError(f"a symlink in the checkout: {relative}")
        if path.is_file():
            found[relative] = path.read_bytes()
    return sorted(found.items(), key=lambda item: item[0].encode("utf-8"))


def archive_bytes(items: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path, data in items:
            info = tarfile.TarInfo(path)
            info.size, info.mtime, info.mode, info.uid, info.gid, info.uname, info.gname, info.type = len(data), FIXED_MTIME, MODE, 0, 0, "", "", tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def archive_digest(items: list[tuple[str, bytes]]) -> str:
    return hashlib.sha256(archive_bytes(items)).hexdigest()


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()      # noqa: S324 - git's object id, not a security hash


def tree_sha(items: list[tuple[str, bytes]]) -> str:
    """The git tree id of `items` (all mode 100644), computed without git: a dry run needs no repository and writes no object. Directories sort as `name/`, as git orders them."""
    root: dict = {}
    for path, data in items:
        node = root
        *folders, name = path.split("/")
        for folder in folders:
            node = node.setdefault(folder, {})
        node[name] = data

    def build(node: dict) -> str:
        rows = []
        for name, value in node.items():
            if isinstance(value, bytes):
                rows.append((name.encode("utf-8"), b"100644", blob_sha(value)))
            else:
                rows.append((name.encode("utf-8") + b"/", b"40000", build(value)))
        body = b"".join(mode + b" " + key.rstrip(b"/") + b"\0" + bytes.fromhex(sha) for key, mode, sha in sorted(rows, key=lambda row: row[0]))
        return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()      # noqa: S324
    return build(root)


def build_tree(repo: str | Path, items: list[tuple[str, bytes]], *, write: bool, git: Git = run_git) -> tuple[str, str | None]:
    """(tree SHA, commit SHA or None). `write=False` computes the tree id in Python: no git process, no object, no ref. `write=True` stores the blobs, the tree and ONE parentless commit through
    plumbing (a temporary index named by `GIT_INDEX_FILE` in this tool's own subprocess environment) and refuses to go on if git's tree differs from the computed one. No ref is updated either way."""
    expected = tree_sha(items)
    if not write:
        return expected, None
    with tempfile.TemporaryDirectory(prefix="g3h-pack-") as scratch:
        index = {"GIT_INDEX_FILE": str(Path(scratch).resolve() / "index")}
        lines = []
        for path, data in items:
            blob = git(repo, "hash-object", "--no-filters", "-w", "--stdin", stdin=data).decode().strip()
            lines.append(f"100644 {blob}\t{path}\n")
        git(repo, "update-index", "--add", "--index-info", stdin="".join(lines).encode("utf-8"), env=index)
        tree = git(repo, "write-tree", env=index).decode().strip()
    if tree != expected:
        raise RuntimeError(f"git wrote tree {tree}, the computed tree is {expected}")
    return tree, git(repo, "commit-tree", tree, "-m", COMMIT_MESSAGE, env=IDENTITY).decode().strip()


def _option(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


def _emit(result: dict, args: list[str]) -> None:
    text = json.dumps(result, indent=1, sort_keys=True) + "\n"
    out = _option(args, "--out")
    if out:
        Path(out).write_text(text, encoding="utf-8")
    print(text, end="")


def _files(repo: str, args: list[str], git: Git) -> list[str]:
    """The files to pack: the rules' pick (or `--list`) held to the allowlist (`--allowlist`, default the committed one)."""
    return checked_files(repo, git, listing=_option(args, "--list"), allowlist=_option(args, "--allowlist"))


def _allowlist_record(repo: str, args: list[str]) -> dict:
    path = _option(args, "--allowlist") or Path(repo) / ALLOWLIST_REL
    return {"allowlistFiles": len(load_allowlist(path)), "allowlistSha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "matchesAllowlist": True}      # `_files` already refused a mismatch


def main(argv: list[str], git: Git = run_git) -> int:
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    try:
        if cmd == "candidates" and args:      # the rules' pick before the allowlist: what a reviewer reads to write or update the list
            _emit({"files": harness_files(os.path.abspath(args[0]), git)}, args)
            return 0
        if cmd == "list" and args:
            repo = os.path.abspath(args[0])
            _emit({"files": _files(repo, args, git), **_allowlist_record(repo, args)}, args)
            return 0
        if cmd == "archive" and len(args) >= 2:
            repo = os.path.abspath(args[0])
            files = _files(repo, args, git)
            items = entries(repo, files)
            data = archive_bytes(items)
            Path(args[1]).write_bytes(data)
            _emit({"archiveSha256": hashlib.sha256(data).hexdigest(), "entries": len(items), "bytes": len(data)}, args)
            return 0
        if cmd == "digest" and args:
            items = read_tree(os.path.abspath(args[0]))
            _emit({"archiveSha256": archive_digest(items), "entries": len(items)}, args)
            return 0
        if cmd == "tree" and args:
            repo = os.path.abspath(args[0])
            files = _files(repo, args, git)
            items = entries(repo, files)
            dry = "--dry-run" in args
            tree, commit = build_tree(repo, items, write=not dry, git=git)
            _emit({"dryRun": dry, "treeSha": tree, "commitSha": commit, "archiveSha256": archive_digest(items), "entries": len(items), "files": [path for path, _ in items], **_allowlist_record(repo, args)}, args)
            return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"{type(error).__name__}: {str(error)[:300]}", file=sys.stderr)
        return 2
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
