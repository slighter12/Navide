"""CONTRIBUTOR TEMPLATE — copy to ``<your_key>.py`` and fill in.

Files starting with ``_`` are never imported by the registry, its tests or the packaged
backend. Steps to add a vendor (full guide: ``docs/adding-a-cli-vendor.md``):

1. Declare the CLI in ``shared/agent-cli/<key>.json`` (short lowercase key, e.g.
   ``mycli``): its command, the flags it takes (model, effort, permission bypass, resume,
   session id, login), where it keeps its data and sessions, its capabilities. Then run
   ``uv --project backend run python scripts/generate-agent-cli-facts.py``. The generated
   ``_vendor_facts.py`` is what this file reads; never edit it.
2. Copy this file to ``<key>.py`` and fill in SPEC below with what a declaration cannot
   say: credentials, quota, MCP and skills wiring, the install entry, a log reader. A
   ``VendorSpec`` derives everything the declaration states (``supports_model``,
   ``supports_effort``, ``known_efforts``, ``login_command_args``,
   ``resume_id_from_command``, ``shutdown``, ``session_path``, ``data_dir_env_vars``) when
   you leave the field alone — restating one is a copy that can drift.
3. Register the SPEC in ``registry.py`` (one import and one line, alphabetical).
4. Add ``log_readers/<key>.py`` (a re-export shim, or an empty placeholder when the CLI
   writes no log you read), and a log reader section below when it does.
5. Add the frontend spec in ``src/renderer/src/platform/plugin-shell/agents/<key>.ts`` and
   register it in ``agents/index.ts``.
6. Run the structural tests — they tell you what is missing or forbidden:
   ``uv --project backend run pytest backend/tests/test_cli_vendors_registry.py``

Import rules (CI-enforced): only ``base``, ``_protocols``, ``_vendor_facts``, the standard
library, and httpx. Never import another vendor module or any app/ws/vault
module.
"""

from ._vendor_facts import FACTS
from .base import VendorSpec, data_dirs_of

FACT = FACTS["_template"]  # shared/agent-cli/<key>.json, generated into _vendor_facts.py

SPEC = VendorSpec(
    key=FACT.key,             # the declaration's key — also the filename
    label="Human Name",       # display label, e.g. "MyCLI"
    # Where the CLI keeps its data (the variables of the declaration's `data_dirs` are the
    # ones allowed into the risk context). Omit when the declaration has none.
    data_dirs=data_dirs_of(FACT.data_dirs),
    # --- credentials: only if the CLI stores a login the vault can park ---
    # live_file=(".mycli", "auth.json"),
    # slot_file="auth.json",
    # login_home_secret_file=(".mycli", "auth.json"),
    # profile_home_secret_file=(".mycli", "auth.json"),
    # How the vault switches the account and what the switch must do to a
    # running pane; None = never offered for switching. Declare only what
    # the CLI's source or docs establish, and say so in ``evidence``.
    # account_switch=AccountSwitchSpec(
    #     auth_scope="mycli", method="restart", store="file",
    #     evidence="source", verified_version="1.2.3",
    #     todo="no A -> B -> A round-trip on two real accounts recorded"),
    # --- usage quota: async (home) -> snapshot dict ---
    # fetch_usage=fetch_usage,
    # Regex sources for THIS CLI's own "quota exhausted" text; never a
    # generic 429 / rate-limit / login message. Mirror the frontend spec's
    # ``quotaExhausted.pattern`` exactly (a test compares the two).
    # quota_exhausted_patterns=(r"usage limit reached",),
    # --- resume / session: declared in the JSON (`resume_args`, `session_root`, `session_path`); a hook only
    # where the disk decides (a session that cannot be named from the id alone) ---
    # session_exists=session_exists,
    # --- spawn environment ---
    # home_env_vars=("MYCLI_HOME",),
    # interrupt_key=b"\x03",
    # --- log reading ---
    # make_log_reader=lambda: MyCliLogReader(),
    # --- install wizard: detection + install/update commands (see base.Dep) ---
    # install_dep=Dep("mycli", "Human Name", "One-line description", "agent_cli",
    #     ["mycli", "--version"], r"(\d+\.\d+\.\d+)",
    #     install_cmd="npm install -g mycli", needs_terminal=True,
    #     requires_binaries=("npm",), optional=True, docs_url="https://mycli.dev"),
)
