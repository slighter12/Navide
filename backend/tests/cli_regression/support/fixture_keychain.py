"""Sandbox-owned external OS store stand-in; never invokes security."""
import json
from pathlib import Path
import shlex
import threading


def runner(root: Path):
    store = root / "fixture-keychain.json"
    lock = threading.Lock()

    def run(args, input_text=None):
        if args == ["-i"]:
            args = shlex.split(input_text)
        # This is the existing vault's external runner seam, not a substitute
        # vault, switch, lock, credential plan, or persistence implementation.
        if args[0] not in {"find-generic-password", "add-generic-password", "delete-generic-password"}:
            raise AssertionError("unexpected external OS credential operation")
        service = args[args.index("-s") + 1]
        with lock:
            values = json.loads(store.read_text()) if store.exists() else {}
            if args[0] == "find-generic-password":
                return (0, "password: 0x" + values[service].encode().hex()) if service in values else (44, "")
            if args[0] == "add-generic-password":
                values[service] = args[args.index("-w") + 1]
            else:
                values.pop(service, None)
            store.write_text(json.dumps(values))
            store.chmod(0o600)
            with (root / "fixture-keychain-operations.jsonl").open("a") as stream:
                stream.write(json.dumps({"operation": args[0], "service": service}) + "\n")
            return 0, ""
    return run
