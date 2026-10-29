"""Information-only record of the process group and the session a hosted macOS step runs in (rh9, macOS rehearsal 2). It decides nothing, changes no rule and never fails a job.

    python -B hosted_process_layout.py OUT_JSON

Rehearsal 2's `Rust tests` step failed three frozen tests with EPERM: they walk the process group or the session of the test process, the product reads every member with `proc_pidinfo` (which answers EPERM for
another user's process) and fails closed. The diagnosis inferred, but did not observe, that the runner's step group or session holds a process of another user. This runs right before `Rust tests`, in a step of the
same kind (a bash step of the same runner), and records every member of the group and of the session it was started in: `pid, ppid, pgid, sess, uid, comm` (the command name only: no arguments, no path, no
environment), and whether the member's uid differs from ours.

`ps -o sess=` prints 0 on macOS for every process, so the session of each process is read with `getsid(pid)` (answered for any process, as the diagnosis showed); a pid that cannot be read is recorded as `null`. The
member rows come from `ps -Aco pid=,ppid=,pgid=,uid=,comm=` (`-c`: the command name alone). Standard library only. Every sensor failure is a field of the document; the exit code is 0 whatever happens.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

SCHEMA = "acr-g3-process-layout/1"
PS_TIMEOUT_S = 20.0
NAME_CHARS = 40
MAX_MEMBERS = 200
TOKEN = re.compile(r"(ghs|ghp|gho|ghu|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")


class PsText(str):
    """The text `ps` printed, with the pid of the `ps` child that printed it (`child_pid`, None when unknown)."""

    child_pid: int | None = None


def default_ps(timeout: float = PS_TIMEOUT_S) -> str:
    """`pid ppid pgid uid comm` per process, no header (`-c`: the command name alone, not its path or arguments). The child is started with `Popen` so its pid is known: on macOS `/bin/ps` is setuid
    root, so its own row shows another effective uid and must not be taken for a member of the group."""
    child = subprocess.Popen(["ps", "-Aco", "pid=,ppid=,pgid=,uid=,comm="], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
    try:
        stdout, _ = child.communicate(timeout=timeout)
    except BaseException:
        child.kill()
        child.communicate()
        raise
    text = PsText(stdout)
    text.child_pid = child.pid
    return text


def clean_name(raw: str) -> str:
    """The last path component, printable, capped, token shapes replaced."""
    name = re.sub(r"[^\x20-\x7e]", "?", raw.strip().replace("\\", "/").rsplit("/", 1)[-1])[:NAME_CHARS]
    return TOKEN.sub("[redacted]", name)


def parse(ps_text: str) -> list[dict]:
    """Rows of `ps` output as `{"pid", "ppid", "pgid", "uid", "comm"}`; a row that does not parse is skipped."""
    rows = []
    for line in ps_text.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) != 5:
            continue
        try:
            pid, ppid, pgid, uid = (int(value) for value in parts[:4])
        except ValueError:
            continue
        rows.append({"pid": pid, "ppid": ppid, "pgid": pgid, "uid": uid, "comm": clean_name(parts[4])})
    return rows


def session_of(pid: int, getsid: Callable[[int], int]) -> int | None:
    """`getsid(pid)`, or None when the process is gone or cannot be read."""
    try:
        return int(getsid(pid))
    except OSError:
        return None


def layout(rows: list[dict], *, pid: int, pgid: int, sid: int, uid: int, getsid: Callable[[int], int]) -> dict:
    """The members of our process group or session, each with its session, and the counts a reader needs."""
    members = []
    unreadable = 0
    for row in rows:
        session = session_of(row["pid"], getsid)
        if session is None:
            unreadable += 1
        in_group, in_session = row["pgid"] == pgid, session == sid
        if in_group or in_session:
            members.append({"pid": row["pid"], "ppid": row["ppid"], "pgid": row["pgid"], "sess": session, "uid": row["uid"], "comm": row["comm"], "inGroup": in_group, "inSession": in_session,
                            "self": row["pid"] == pid, "otherUid": row["uid"] != uid})
    members.sort(key=lambda member: member["pid"])
    return {"self": {"pid": pid, "pgid": pgid, "sess": sid, "uid": uid}, "processesListed": len(rows), "sessionsUnreadable": unreadable, "members": members[:MAX_MEMBERS], "membersTotal": len(members),
            "membersWithOtherUid": sum(1 for member in members if member["otherUid"]), "groupMembers": sum(1 for member in members if member["inGroup"]), "sessionMembers": sum(1 for member in members if member["inSession"])}


def run(destination: str | Path, *, ps: Callable[[float], str] = default_ps, getsid: Callable[[int], int] | None = None, getpgrp: Callable[[], int] | None = None, getuid: Callable[[], int] | None = None,
        getpid: Callable[[], int] = os.getpid, show: Callable[[str], None] = print) -> dict:
    """Writes `destination` (one complete JSON document) and prints a table of the members; returns the document. The POSIX calls are looked up when the record runs, not when the module is imported
    (Windows has no `os.getsid`, and every module of the pack must import there)."""
    getsid, getpgrp, getuid = getsid or getattr(os, "getsid", None), getpgrp or getattr(os, "getpgrp", None), getuid or getattr(os, "getuid", None)
    document: dict = {"schema": SCHEMA, "notEvidence": True}
    errors: list[str] = []
    rows: list[dict] = []
    excluded = False
    try:
        text = ps(PS_TIMEOUT_S)
        listed = parse(text)
        ps_pid = getattr(text, "child_pid", None)
        # Drop exactly the row of the recorder's own `ps` child (that pid, nothing else): on macOS `ps` is setuid root, so its row shows another uid and is no member of the group.
        rows = [row for row in listed if ps_pid is None or row["pid"] != ps_pid]
        excluded = len(rows) != len(listed)
    except Exception as error:  # noqa: BLE001 - information only: the failure is a field
        errors.append(f"ps: {type(error).__name__}")
    document["excludedRecorderPs"] = excluded
    try:
        document.update(layout(rows, pid=getpid(), pgid=getpgrp(), sid=getsid(0), uid=getuid(), getsid=getsid))
    except Exception as error:  # noqa: BLE001
        errors.append(f"layout: {type(error).__name__}")
    if errors:
        document["error"] = errors
    try:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        show(f"process layout: could not write {type(error).__name__}")
    own = document.get("self")
    if own:
        show(f"process layout: self pid={own['pid']} pgid={own['pgid']} sess={own['sess']} uid={own['uid']}; {document['groupMembers']} in the group, {document['sessionMembers']} in the session, "
             f"{document['membersWithOtherUid']} with another uid, {document['sessionsUnreadable']} of {document['processesListed']} sessions unreadable, recorder's own ps row excluded: {document['excludedRecorderPs']}")
        show("process layout: pid ppid pgid sess uid comm")
        for member in document["members"]:
            show(f"process layout: {member['pid']} {member['ppid']} {member['pgid']} {member['sess']} {member['uid']} {member['comm']}" + (" (another uid)" if member["otherUid"] else ""))
    for error in errors:
        show(f"process layout: {error}")
    return document


def main(argv: list[str], **injected) -> int:
    """Always 0: the record is information only."""
    try:
        if len(argv) == 1:
            run(argv[0], **injected)
        else:
            print("usage: hosted_process_layout.py OUT_JSON", file=sys.stderr)
    except BaseException as error:  # noqa: BLE001 - nothing here may fail the job
        print(f"process layout: stopped by {type(error).__name__}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
