"""``pytest -p tests.cli_runtime_on``: run a suite with the Rust CLI runtime switch on.

Every registered vendor's reader class has its three record calls routed the
way ``cli_runtime.wrap`` routes a production reader, and the switch is turned
on, so the readers tests build directly (``CodexLogReader()``) answer through
the ``navide-cli-runtime`` process too. Vendors the runtime does not observe,
and any call it cannot answer, stay on Python exactly as in production.

The few tests that meter the Python reader's own file reads are skipped, with
the reason shown (``PYTHON_READ_METERS``).

Needs a release build: ``cargo build --release`` in native/navide-cli-runtime.
"""

from __future__ import annotations

from typing import Any

import pytest


def _route(cls: type, vendor: str) -> None:
    from agent_team_backend import cli_runtime

    for name, route in (
        ("parse_incremental", cli_runtime._usage),
        ("parse_activity", cli_runtime._activity),
        ("turns_for_session", cli_runtime._turns),
    ):
        original = cls.__dict__.get(name) or getattr(cls, name)

        def routed(self: Any, *args: Any, _original=original, _route=route, **kwargs: Any) -> Any:
            return _route(vendor, lambda *a, **k: _original(self, *a, **k), *args, **kwargs)

        setattr(cls, name, routed)


#: Tests that meter the Python reader's own file reads (a counting
#: ``Path.open``). With the switch on, that code does not run, so the meter has
#: nothing to measure; the reads they pin are tested on the Rust side
#: (``observe/jsonl.rs``: ``a_stale_resume_point_reads_from_the_head``).
PYTHON_READ_METERS = (
    "test_claude_activity_resume.py::test_append_reads_only_the_new_bytes",
    "test_claude_activity_resume.py::test_truncated_file_is_read_from_the_head",
    "test_claude_activity_resume.py::test_changed_mark_is_read_from_the_head",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if item.nodeid.endswith(PYTHON_READ_METERS):
            item.add_marker(pytest.mark.skip(reason="meters Python's file reads, which the Rust path replaces"))


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
    from agent_team_backend import cli_runtime
    from agent_team_backend.cli_vendors.registry import VENDORS

    if cli_runtime.binary() is None:
        raise pytest.UsageError("cli_runtime_on needs navide-cli-runtime built (cargo build --release)")
    seen: set[type] = set()
    for spec in VENDORS.values():
        if spec.make_log_reader is None:
            continue
        cls = type(spec.make_log_reader())
        if cls not in seen:
            seen.add(cls)
            _route(cls, str(cls.vendor))
    cli_runtime.apply(True)
