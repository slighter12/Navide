"""Python's half of the JSON dialect the Rust runtime sorts.

``native/navide-cli-runtime/src/pyjson.rs`` decides, for a text its own parser
rejects, whether main's ``json.loads`` may read it (then the call is Python's)
or rejects it too (then the text is skipped as main skips it). Its cases live
in ``native/navide-cli-runtime/tests/json_dialect.json``; the crate holds the
runtime's answer to them, and this file holds the claims they make about
Python to Python itself, so neither side can drift alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CASES = json.loads(
    (Path(__file__).resolve().parents[2] / "native" / "navide-cli-runtime" / "tests" / "json_dialect.json").read_text(
        encoding="utf-8"
    )
)


def _python_accepts(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:  # JSONDecodeError
        return False
    return True


@pytest.mark.parametrize("case", CASES, ids=lambda case: repr(case["text"])[:48])
def test_python_does_what_the_case_says(case: dict) -> None:
    assert _python_accepts(case["text"]) == case["python_accepts"]


def test_no_text_python_reads_is_ever_sorted_as_rejected() -> None:
    """The one direction that loses records: a text Python reads that the runtime skips."""
    lost = [case["text"] for case in CASES if case["python_accepts"] and case["expect"] == "rejected"]
    assert lost == []


def test_the_cases_cover_each_extension_python_has() -> None:
    python_only = [case["text"] for case in CASES if case["expect"] == "python" and case["python_accepts"]]
    joined = "\n".join(python_only)
    for needle in ("\\ud83d", "NaN", "Infinity", "1e999"):
        assert needle in joined, needle
    assert any(len(text) > 300 for text in python_only), "an integer of more than 300 digits"


def test_nesting_is_python_only_between_128_levels_and_its_own_limit() -> None:
    """The depth cases are built, not typed: `pyjson`'s unit test builds the same texts."""
    nested = lambda depth: '{"n":' + "[" * depth + "]" * depth + "}"  # noqa: E731
    assert _python_accepts(nested(100))
    assert _python_accepts(nested(200))
    # Where it gives up depends on the interpreter's version and the stack it
    # is called from: `pyjson` hands everything past 900 levels over either way.
    with pytest.raises(RecursionError):
        json.loads(nested(200_000))
    assert not _python_accepts("[" * 200 + "]" * 199)
