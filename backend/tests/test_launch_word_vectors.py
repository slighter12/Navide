"""The shared launch-word vectors, read in Python.

`backend/tests/data/launch_word_vectors.json` is read by three suites: this one, vitest (`src/shared/launchWords.vectors.test.ts`)
and cargo test (`native/navide-cli-runtime/src/compose.rs`). Each language spells an option from the generated option table and
reads a resume id from the declared facts; the vectors pin that the three agree, for both quoting grammars.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from agent_team_backend import cli_launch
from agent_team_backend.cli_vendors import _vendor_facts as facts
from agent_team_backend.cli_vendors.registry import VENDORS

VECTORS = json.loads((Path(__file__).parent / "data" / "launch_word_vectors.json").read_text())
SPELLED = VECTORS["words_to_text"]["vectors"]
REFUSED = VECTORS["words_to_text"]["refused"]
RESUME = VECTORS["resume_ids"]["vectors"]

#: The two grammars a launch is quoted in: the POSIX shell's and the Windows command line's (`list2cmdline`).
GRAMMARS = {"posix": shlex.quote, "windows": lambda text: subprocess.list2cmdline([text])}


def _composed(vendor: str, words: list[dict], quote) -> str:
    """The words with each option spelled by the backend's spelling, composed to text."""
    spelled = [dict(word, text=cli_launch.spell_option(vendor, word["opt"], word.get("value"), quote)) if "opt" in word else word
               for word in words]
    return str(cli_launch.Command.from_words(spelled, quote=quote))


@pytest.mark.parametrize("grammar", sorted(GRAMMARS))
@pytest.mark.parametrize("vector", SPELLED, ids=[vector["name"] for vector in SPELLED])
def test_the_words_compose_to_the_shared_text(vector, grammar):
    assert _composed(vector["vendor"], vector["words"], GRAMMARS[grammar]) == vector[grammar]


@pytest.mark.parametrize("vector", REFUSED, ids=[vector["name"] for vector in REFUSED])
def test_an_option_the_facts_do_not_spell_is_refused(vector):
    option = next(word for word in vector["words"] if "opt" in word)
    with pytest.raises(cli_launch.LaunchRefused):
        cli_launch.spell_option(vector["vendor"], option["opt"], option.get("value"), GRAMMARS["posix"])


@pytest.mark.parametrize("vector", [v for v in RESUME if "python" in v.get("readers", ["python"])],
                         ids=lambda vector: f"{vector['vendor']}: {vector['line']}")
def test_the_resume_reader_reads_the_shared_id(vector):
    if vector.get("grammar") == "posix" and sys.platform == "win32":
        pytest.skip("this line depends on the POSIX tokenizer; the Windows grammar reads it differently")
    reader = VENDORS[vector["vendor"]].resume_id_from_command
    assert (reader(vector["line"]) if reader else "") == vector["id"]


def test_every_vector_names_a_declared_vendor_or_the_plain_terminal():
    named = {vector["vendor"] for vector in (*SPELLED, *REFUSED, *RESUME)}
    assert named - set(facts.FACTS) == {"terminal"}


def test_the_option_table_names_a_declared_field_and_capability_of_every_vendor():
    for option, rule in facts.OPTIONS.items():
        assert rule.field in facts.VendorFacts._fields, option
        assert rule.capability in facts.CapabilityStatus._fields, option
        assert rule.values is None or rule.values in facts.VendorFacts._fields, option
        assert not rule.template or rule.takes_value, f"{option}: a template is filled by a value"
