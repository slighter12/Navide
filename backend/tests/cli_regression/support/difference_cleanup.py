"""Finite metadata-only cleanup; retained births, never foreign argv extraction."""
import json
import os
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(BACKEND))
sys.path.append(str(BACKEND / '.venv/lib/python3.12/site-packages'))
import psutil
from fixture_exec import stable_bytes
from usage_evidence_snapshot import usage_evidence
from tests.cli_regression.support.process_argv_observation import admit_bootstrap_owner


def capture(root, pid, parent, rows):
    if parent != os.getppid():
        raise PermissionError('unregistered cleanup launcher')
    try:
        admit_bootstrap_owner(root, pid, parent)
        process = psutil.Process(pid)
        known = {(row['pid'], row['created']): row for row in rows}
        for child in [process, *process.children(recursive=True)]:
            identity = {'pid': child.pid, 'created': child.create_time(), 'parent': child.ppid()}
            known[(identity['pid'], identity['created'])] = identity
        return list(known.values())
    except psutil.NoSuchProcess:
        return rows  # Gone is distinct from unavailable or invalid admission.


def main():
    mode, supplied_root, pid, parent = sys.argv[1:]
    root, pid, parent = Path(supplied_root), int(pid), int(parent)
    record = root / 'controller-ownership.json'
    rows = json.loads(stable_bytes(record)) if record.exists() else []
    if mode == 'capture':
        rows = capture(root, pid, parent, rows)
        record.write_text(json.dumps(rows))
        print(json.dumps({'known': len(rows), 'mode': mode}))
    elif mode == 'cleanup':
        owned = []
        for row in rows:
            try:
                process = psutil.Process(row['pid'])
                if process.create_time() == row['created']:
                    owned.append(process)
            except psutil.NoSuchProcess:
                pass
        for process in reversed(owned):
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(owned, timeout=3)
        for process in alive:
            if process.is_running():
                process.kill()
        _, alive = psutil.wait_procs(alive, timeout=3)
        result = {'fallback_used': True, 'known': len(rows),
                  'survivors': [{'pid': p.pid, 'created': p.create_time()} for p in alive]}
        (root / 'controller-cleanup.json').write_text(json.dumps(result))
        print(json.dumps(result))
        if alive:
            raise SystemExit(1)
    elif mode == 'usage-evidence':
        # Read only the admitted driver's OWN synthetic observation files.
        # Do not call a parser, drain ingestion, refresh totals, or signal a child.
        if parent != os.getppid():
            raise PermissionError('unregistered observation launcher')
        admit_bootstrap_owner(root, pid, parent)
        print(json.dumps(usage_evidence(root)))
    else:
        raise PermissionError('unknown fixed cleanup control')


if __name__ == '__main__':
    main()
