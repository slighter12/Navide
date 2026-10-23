"""G2 ticket 04: which launch shape a subject may use, per platform, and the credential-wrapper rule.

Delta sections 3.1 and 5. `agent_team_backend.cli_launch` is the Python side of the admission
predicate; Rust runs the same table on receipt.
"""
from __future__ import annotations

import pytest

from agent_team_backend import cli_launch
from agent_team_backend.cli_vendors.registry import VENDORS

NATIVE_SUBJECTS = ("claude", "", "terminal")


@pytest.mark.parametrize("subject", NATIVE_SUBJECTS)
def test_posix_composes_migrated_subjects_in_rust(subject):
    assert cli_launch.admit_composition(subject, platform="posix", managed_credential_path=False) == "rust"


@pytest.mark.parametrize("subject", NATIVE_SUBJECTS)
def test_windows_ticket_04_transition_sends_the_precomposed_shape(subject):
    assert cli_launch.admit_composition(subject, platform="windows", managed_credential_path=False) == "python"


def test_a_vendor_that_still_composes_in_python_stays_python_everywhere():
    for platform in ("posix", "windows"):
        assert cli_launch.admit_composition("aider", platform=platform, managed_credential_path=False) == "python"


def test_a_managed_credential_launch_of_a_rust_subject_is_refused_before_create():
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.admit_composition("claude", platform="posix", managed_credential_path=True)
    assert refused.value.code == "COMPOSITION_UNSUPPORTED"
    assert refused.value.stage == "credential_wrapper"


def test_no_subject_composed_in_rust_has_a_managed_credential_store_today():
    """The ticket 04 exclusion of `wrap_command` holds only while this is true."""
    for subject in cli_launch.rust_composed_subjects():
        spec = VENDORS.get(subject)
        assert spec is None or spec.live_file_from_context is None, subject
