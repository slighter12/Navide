"""G2 ticket 04: the bounded Rust attestation of the executed program (delta section 4).

`{len, head, tail}`: `head` is the longest prefix of at most 128 bytes ending on a character
boundary, `tail` the longest suffix of at most 128 bytes starting on one. The oracle below is written
independently of the implementation.
"""
from __future__ import annotations

import pytest

from agent_team_backend import cli_launch

LIMIT = 128


def _head(path: str) -> str:
    data = path.encode()
    cut = min(len(data), LIMIT)
    while cut and (data[cut:cut + 1] and (data[cut] & 0xC0) == 0x80):
        cut -= 1
    return data[:cut].decode()


def _tail(path: str) -> str:
    data = path.encode()
    cut = max(len(data) - LIMIT, 0)
    while cut < len(data) and (data[cut] & 0xC0) == 0x80:
        cut += 1
    return data[cut:].decode()


def attest(path: str) -> dict:
    return {"len": len(path.encode()), "head": _head(path), "tail": _tail(path)}


LONG_ASCII = "/" + "a" * 299 + "/bash"
LONG_MULTIBYTE = "/" + "é" * 150 + "/bash"


@pytest.mark.parametrize("path", ["/bin/bash", LONG_ASCII, LONG_MULTIBYTE], ids=["short", "over-256-ascii", "multibyte-cut"])
def test_a_valid_wrapped_shell_of_any_length_is_accepted_and_the_head_is_the_diagnostic(path):
    assert len(path.encode()) > 256 or path == "/bin/bash"
    assert cli_launch.verify_argv0_attestation(attest(path), wrapped_shell=path) == _head(path)


def test_a_direct_shape_accepts_a_well_formed_attestation_it_cannot_compare():
    assert cli_launch.verify_argv0_attestation(attest(LONG_ASCII), wrapped_shell=None) == _head(LONG_ASCII)


def _bad(path: str, **change) -> dict:
    value = attest(path)
    for key, replacement in change.items():
        if replacement is None:
            value.pop(key)
        else:
            value[key] = replacement
    return value


MALFORMED = {
    "missing-len": _bad(LONG_ASCII, len=None),
    "missing-head": _bad(LONG_ASCII, head=None),
    "wrong-len": _bad(LONG_ASCII, len=len(LONG_ASCII.encode()) + 1),
    "zero-len": _bad("/bin/bash", len=0),
    "head-not-a-prefix": _bad(LONG_ASCII, head="/" + "b" * 127),
    "tail-not-a-suffix": _bad(LONG_ASCII, tail="b" * 128),
    "head-over-128-bytes": _bad(LONG_ASCII, head="/" + "a" * 200),
    "non-string-head": _bad(LONG_ASCII, head=5),
    "nul-in-head": _bad(LONG_ASCII, head="/a\x00" + "a" * 100),
    "short-path-head-differs-from-tail": _bad("/bin/bash", tail="bash"),
    "extra-key": {**attest(LONG_ASCII), "sha256": "00"},
}


@pytest.mark.parametrize("name", MALFORMED)
def test_a_malformed_attestation_is_refused_before_the_owned_root_is_registered(name):
    with pytest.raises(cli_launch.LaunchRefused) as refused:
        cli_launch.verify_argv0_attestation(MALFORMED[name], wrapped_shell=LONG_ASCII if "short" not in name else "/bin/bash")
    assert (refused.value.code, refused.value.stage) == ("ATTESTATION_INVALID", "argv0")


def test_a_non_object_attestation_is_refused():
    for value in (None, "/bin/bash", ["/bin/bash"], 7):
        with pytest.raises(cli_launch.LaunchRefused):
            cli_launch.verify_argv0_attestation(value, wrapped_shell=None)


def test_an_attestation_for_another_shell_is_refused_even_when_well_formed():
    with pytest.raises(cli_launch.LaunchRefused):
        cli_launch.verify_argv0_attestation(attest(LONG_ASCII), wrapped_shell="/" + "a" * 299 + "/zsh")
