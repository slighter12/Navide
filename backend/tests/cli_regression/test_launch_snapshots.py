"""Per-vendor launch snapshots: what the child process really receives.

Each registered vendor, plus the plain terminal, is launched through the
authenticated WebSocket, the real Python handlers and a native PTY, in the two
POSIX shapes the application submits (direct argv, login-shell wrapped). A
synthetic recorder stands in for the CLI and reports its own argv, cwd and
environment. Volatile values (temp roots, ports, ids, tokens) are normalised so
regenerating from the same commit is byte-identical.

Membership comes from the registry, never from a hard-coded count. When
NAVIDE_G2_SNAPSHOT_DIR is set the snapshots are also written there, one file
per vendor and shape, under a per-platform directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import uuid

import pytest

from agent_team_backend.cli_vendors.registry import VENDORS
from .support.backend_process import BackendProcess
from .support.cli_shim import base_python_executable, install_cli_shim

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell forms only; Windows is captured separately")

REPO_ROOT = Path(__file__).resolve().parents[3]
PROBE = Path(__file__).parent / "support" / "launch_snapshot_probe.py"
BOOT_DIR = Path(__file__).parent / "support" / "passwd_home_boot"
SHELL_FORMS = ("direct", "posix-login-shell")
SUBJECTS = [*sorted(VENDORS), "terminal"]
_SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|API_?KEY|PRIVATE|LAUNCH", re.IGNORECASE)
_PORT_FLAG = re.compile(r"(--port[ =])\d+")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONG_HEX = re.compile(r"(?<![0-9a-zA-Z])[0-9a-fA-F]{16,}(?![0-9a-zA-Z])")
_PORT = re.compile(r"(127\.0\.0\.1|localhost):\d+")
_QUERY_TOKEN = re.compile(r"([?&](?:t|token|key)=)[A-Za-z0-9_\-.]{12,}")

# The leak validator below is written independently of the normaliser so that it
# can catch the normaliser's mistakes. A path root counts only at a component
# boundary: not after a word character, `>` (a `<LABEL>` prefix), `.` or `-`,
# and not when more name characters follow it.
_RAW_ROOTS = re.compile(
    r"(?<![\w>.\-])/(?:Users|root|tmp|var/folders|private/var|home/[^/\s\"']+)(?=/|$|[^\w.\-])"
)
_SECRET_SHAPES = {
    "bearer token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=\-]{16,}", re.IGNORECASE),
    "api key": re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}|\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}|\bAIza[0-9A-Za-z_\-]{30,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
}
_RAW_PORTS = re.compile(r"(?:127\.0\.0\.1|localhost|\[::1\]|0\.0\.0\.0):\d+|--port[ =]\d+")


def _snapshot_leaks(value, where: str = "$") -> list[str]:
    """Where a captured snapshot still holds a raw path root, secret shape or port.

    Reports the kind and location only, never the matched text.
    """
    if isinstance(value, dict):
        return [leak for key, item in value.items() for leak in _snapshot_leaks(item, f"{where}.{key}")]
    if isinstance(value, list):
        found = [leak for index, item in enumerate(value) for leak in _snapshot_leaks(item, f"{where}[{index}]")]
        for index, (flag, number) in enumerate(zip(value, value[1:])):
            if flag == "--port" and isinstance(number, str) and number.isdigit():
                found.append(f"raw port at {where}[{index + 1}]")
        return found
    if not isinstance(value, str):
        return []
    found = []
    if _RAW_ROOTS.search(value):
        found.append(f"raw path root at {where}")
    if _RAW_PORTS.search(value):
        found.append(f"raw port at {where}")
    found.extend(f"{kind} at {where}" for kind, pattern in _SECRET_SHAPES.items() if pattern.search(value))
    return found


def _binary_name(subject: str) -> str:
    if subject == "terminal":
        return "terminal-probe"
    return VENDORS[subject].install_dep.check_cmd[0]


def _real_home() -> Path:
    """The account home the way the product reads it: the passwd entry, not $HOME."""
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return Path.home()


def _user_name() -> str:
    return _real_home().name


def _variants(path: Path | str) -> set[str]:
    """A path as spelled and as resolved (macOS /var is /private/var), without a trailing slash."""
    spelled = str(path).rstrip("/")
    return {spelled, str(Path(spelled).resolve())} - {""}


def _temp_roots() -> set[str]:
    roots = _variants(tempfile.gettempdir())
    if os.environ.get("TMPDIR"):
        roots |= _variants(os.environ["TMPDIR"])
    return roots


def _normaliser(root: Path):
    # The product resolves the real home from the passwd entry and ignores the
    # fixture's HOME (mcp_server/pane_home.real_home), so account-home paths
    # reach the child even from an isolated launch; they are redacted here.
    table: dict[str, str] = {}
    for label, paths in (
        ("<ROOT>", _variants(root)),
        ("<VENV_BIN>", _variants(Path(sys.executable).parent)),
        ("<PYTHON_BIN>", _variants(Path(base_python_executable()).parent)),
        ("<REPO>", _variants(REPO_ROOT)),
        ("<TMP>", _temp_roots()),
        ("<HOME>", _variants(_real_home())),
    ):
        for path in paths:
            table[path] = label
    replacements = sorted(table.items(), key=lambda item: -len(item[0]))
    user = re.compile(rf"(?<=/){re.escape(_user_name())}(?=/|$)") if _user_name() else None

    def normalise(text: str) -> str:
        for needle, label in replacements:
            text = re.sub(rf"{re.escape(needle)}(?=/|$|[^0-9A-Za-z_.\-])", label, text)
        if user:
            text = user.sub("<USER>", text)
        text = _UUID.sub("<uuid>", text)
        text = _LONG_HEX.sub("<hex>", text)
        text = _PORT.sub(r"\1:<port>", text)
        text = _PORT_FLAG.sub(r"\1<port>", text)
        return _QUERY_TOKEN.sub(r"\1<token>", text)

    return normalise


def _normalise_argv(normalise, argv: list[str]) -> list[str]:
    """Normalise each element; a bare number after `--port` is a random port."""
    result = []
    for index, part in enumerate(argv):
        if index and argv[index - 1] == "--port" and part.isdigit():
            result.append("<port>")
        else:
            result.append(normalise(part))
    return result


async def _observe(subject: str, form: str, root: Path) -> dict:
    workspace = root / "pane-cwd"
    workspace.mkdir()
    destination = root / "launch.json"
    shim = install_cli_shim(
        root, _binary_name(subject),
        [base_python_executable(), "-u", str(PROBE), subject, str(destination)],
    )
    # The application submits a command line (a string), never a one-element
    # list: wiring appends flags to the command text, which only a string takes.
    command = shlex.quote(str(shim)) if form == "direct" else ["/bin/bash", "-lc", shlex.quote(str(shim))]
    backend = BackendProcess(root)
    # The product reads the account home from the passwd entry, which $HOME does
    # not change. The bootstrap directory makes that entry the fixture home in
    # the backend process, before any application import.
    backend.env["NAVIDE_SNAPSHOT_PASSWD_HOME"] = str(root / "home")
    backend.env["PYTHONPATH"] = os.pathsep.join([str(BOOT_DIR), backend.env["PYTHONPATH"]])
    normalise = _normaliser(root)
    refused = None
    shutdown_finding = None
    try:
        async with backend, backend.connect() as socket:
            payload = {"pane_id": f"snapshot-{subject}", "agent_key": subject, "cwd": str(workspace),
                       "command": command, "cols": 100, "rows": 30, "create_generation": str(uuid.uuid4()),
                       "env": {"REGRESSION_MARKER": "ordinary-setting"}}
            response = await backend.request(socket, "terminal.create", payload, ok=False)
            if not response.get("ok"):
                # Injected flags are appended to the last command element, so a
                # direct argv for an injecting vendor is refused, not launched.
                refused = {"code": response["error"]["code"],
                           "message": normalise(response["error"]["message"])}
            else:
                await backend.expect_output(socket, response["payload"]["terminal_session_id"],
                                            "LAUNCH_SNAPSHOT_READY")
    except AssertionError as err:
        # An inherited defect is recorded, not fixed: some vendors leave a PTY
        # child behind after the backend's shutdown (the harness reaps it).
        if "PTY children survived backend shutdown" not in str(err):
            raise
        shutdown_finding = "PTY children survived backend shutdown"
    snapshot = {
        "subject": subject,
        "shell_form": form,
        "platform": sys.platform,
        "submitted_command": normalise(command) if isinstance(command, str) else [normalise(part) for part in command],
    }
    if shutdown_finding:
        snapshot["shutdown_finding"] = shutdown_finding
    if refused:
        return {**snapshot, "refused": refused}
    observed = json.loads(destination.read_text(encoding="utf-8"))
    env = {
        key: "<redacted>" if _SECRET_NAME.search(key) else normalise(value)
        for key, value in sorted(observed["env"].items())
    }
    return {
        **snapshot,
        "child_argv": _normalise_argv(normalise, observed["argv"]),
        "child_cwd": normalise(observed["cwd"]),
        "child_env": env,
    }


@pytest.mark.parametrize("form", SHELL_FORMS)
@pytest.mark.parametrize("subject", SUBJECTS)
async def test_launch_snapshot(subject, form, tmp_path):
    snapshot = await _observe(subject, form, tmp_path)
    refused = snapshot.get("refused")
    assert refused is None, f"{subject}/{form} was refused ({refused['code']}: {refused['message']}), so no child was observed"
    for key in ("child_argv", "child_cwd", "child_env"):
        assert key in snapshot, f"{subject}/{form} has no {key}"
    assert snapshot["child_cwd"] == "<ROOT>/pane-cwd"
    assert snapshot["child_env"]["REGRESSION_MARKER"] == "ordinary-setting"
    rendered = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    # Pane and shim homes must be built under the fixture home, never the account home.
    assert "<HOME>" not in rendered, "a path under the physical account home reached the child"
    pane_homes = [text for text in (*snapshot["child_argv"], *snapshot["child_env"].values()) if ".navide-panes" in text]
    assert all(text.startswith("<ROOT>/home/.navide-panes/") for text in pane_homes), pane_homes
    assert not _snapshot_leaks(snapshot), _snapshot_leaks(snapshot)
    assert str(tmp_path) not in rendered, "an unnormalised temp path leaked into the snapshot"
    leaks = [leak for leak in (*_variants(_real_home()), *_temp_roots()) if len(leak) > 1]
    assert not [leak for leak in leaks if leak in rendered], "a real home or OS temp path leaked into the snapshot"
    assert not re.search(rf"(?<=/){re.escape(_user_name())}(?=/|\W|$)", rendered), "the user name leaked into the snapshot"
    target = os.environ.get("NAVIDE_G2_SNAPSHOT_DIR")
    if target:
        directory = Path(target) / sys.platform
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{subject}.{form}.json").write_text(rendered, encoding="utf-8")


def test_leak_validator_rejects_planted_leaks():
    planted = {
        "child_argv": ["tool", "--port", "8123", "--url=http://127.0.0.1:9000/x"],
        "child_cwd": "/Users/someone/work",
        "child_env": {
            "A": "/var/folders/ab/cd/T/x", "B": "/private/var/tmp/x", "C": "/tmp/x", "D": "/home/alice/.cfg", "E": "/root/.x",
            "F": "Authorization: Bearer abcdefghijklmnopqrstuvwx",
            "G": "sk-abcdefghijklmnopqrstu", "H": "-----BEGIN RSA PRIVATE KEY-----",
            "I": "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.c2lnbmF0dXJl", "J": "localhost:5555",
        },
    }
    leaks = _snapshot_leaks(planted)
    for expected in ("raw port at $.child_argv[2]", "raw port at $.child_argv[3]", "raw path root at $.child_cwd",
                     *(f"raw path root at $.child_env.{key}" for key in "ABCDE"),
                     "bearer token at $.child_env.F", "api key at $.child_env.G", "private key at $.child_env.H",
                     "jwt at $.child_env.I", "raw port at $.child_env.J"):
        assert expected in leaks, (expected, leaks)
    assert all("alice" not in leak and "abcdefghijkl" not in leak for leak in leaks), "a leak report must not echo values"


def test_leak_validator_accepts_labelled_and_look_alike_paths():
    benign = {
        "child_argv": ["<VENV_BIN>/python", "--port", "<port>", "--url=http://127.0.0.1:<port>/x"],
        "child_cwd": "<ROOT>/pane-cwd",
        "child_env": {
            "HOME": "<ROOT>/home/.navide-panes/grok/p", "TMPDIR": "<TMP>/", "A": "<HOME>/.navide-panes/x",
            "B": "/Users2/x", "C": "/tmpfoo/bar", "D": "/rootless/x", "E": "<ROOT>/tmp/x", "F": "/usr/local/tmp/x",
            "G": "/homework/x", "H": "Bearer <redacted>",
        },
    }
    assert _snapshot_leaks(benign) == []
