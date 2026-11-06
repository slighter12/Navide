"""NAVIDE_LOG_RESCAN_S must never stop start-up, busy-loop the sweep, or switch the safety net off."""
from __future__ import annotations

import logging

import pytest

from agent_team_backend.log_readers.watcher import DEFAULT_RESCAN_S, rescan_interval_from_env


@pytest.mark.parametrize("raw", ["abc", "", "0", "-1", "nan", "inf", "-inf", "0.01", "31", "1e9"])
def test_an_unusable_value_falls_back_to_the_default(raw: str):
    assert rescan_interval_from_env(raw) == DEFAULT_RESCAN_S == 30.0


@pytest.mark.parametrize(("raw", "expected"), [("1", 1.0), ("0.1", 0.1), ("30", 30.0), (" 2.5 ", 2.5)])
def test_a_bounded_finite_value_is_used(raw: str, expected: float):
    assert rescan_interval_from_env(raw) == expected


def test_an_unset_variable_is_the_default():
    assert rescan_interval_from_env(None) == DEFAULT_RESCAN_S


@pytest.mark.parametrize("raw", ["abc", "", "0", "nan", "inf", "31"])
def test_an_unusable_value_logs_one_warning_naming_the_variable(raw: str, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger="agent_team_backend.log_readers.watcher"):
        rescan_interval_from_env(raw)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "NAVIDE_LOG_RESCAN_S" in warnings[0].getMessage()


@pytest.mark.parametrize("raw", [None, "1", "30"])
def test_an_unset_or_valid_value_logs_nothing(raw: str | None, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger="agent_team_backend.log_readers.watcher"):
        rescan_interval_from_env(raw)
    assert caplog.records == []
