"""G2 tickets 04 and 05: which launch shape a subject may use, per platform, and what no longer gates it.

Delta sections 3.1 and 10. `agent_team_backend.cli_launch` is the Python side of the admission
predicate; Rust runs the same table on receipt.
"""
from __future__ import annotations

import pytest

from agent_team_backend import cli_launch, cli_runtime_adapters
from agent_team_backend.cli_vendors.registry import VENDORS

NATIVE_SUBJECTS = ("claude", "", "terminal")
#: Every registered vendor and both shell aliases: after ticket 05 each of them composes in Rust.
EVERY_SUBJECT = (*sorted(VENDORS), "", "terminal")


@pytest.mark.parametrize("subject", NATIVE_SUBJECTS)
def test_posix_composes_migrated_subjects_in_rust(subject):
    assert cli_launch.admit_composition(subject, platform="posix", managed_credential_path=False) == "rust"


@pytest.mark.parametrize("subject", EVERY_SUBJECT)
def test_ticket_05_every_registered_subject_composes_in_rust_on_posix(subject):
    assert cli_launch.admit_composition(subject, platform="posix", managed_credential_path=False) == "rust"


@pytest.mark.parametrize("subject", EVERY_SUBJECT)
def test_windows_composes_every_subject_in_rust_like_posix(subject):
    """Ticket 07 removed the Windows transition row: the declaration decides, on every platform."""
    assert cli_launch.admit_composition(subject, platform="windows", managed_credential_path=False) == "rust"


@pytest.mark.parametrize("subject", sorted(key for key, spec in VENDORS.items() if spec.live_file_from_context))
def test_a_managed_credential_launch_is_admitted_as_rust_on_posix_and_python_on_windows(subject):
    assert cli_launch.admit_composition(subject, platform="posix", managed_credential_path=True) == "rust"
    assert cli_launch.admit_composition(subject, platform="windows", managed_credential_path=True) == "python"


def test_an_unknown_subject_is_refused_before_create():
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.admit_composition("no-such-vendor", platform="posix", managed_credential_path=False)
    assert refused.value.code == "COMPOSITION_UNSUPPORTED"
    assert refused.value.stage == "composition"


def test_the_observer_roster_no_longer_gates_launch_admission():
    """`NATIVE_VENDORS` was the Python half of the old gate (a vendor with no Rust observer was refused)."""
    assert not hasattr(cli_runtime_adapters, "NATIVE_VENDORS")
