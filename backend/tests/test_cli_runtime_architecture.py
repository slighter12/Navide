"""The staged runtime's machine is read from its header and a foreign one is refused by name (G2 ticket 08)."""
from __future__ import annotations

import platform

import pytest

from agent_team_backend import cli_runtime

HOST = cli_runtime._HOST_MACHINES.get(platform.machine().lower())


def elf(machine: int) -> bytes:
    return b"\x7fELF\x02\x01\x01" + bytes(11) + machine.to_bytes(2, "little") + bytes(64)


def macho(cpu: int) -> bytes:
    return b"\xcf\xfa\xed\xfe" + cpu.to_bytes(4, "little") + bytes(64)


def pe(machine: int) -> bytes:
    return b"MZ" + bytes(0x3A) + (0x80).to_bytes(4, "little") + bytes(0x40) + b"PE\0\0" + machine.to_bytes(2, "little") + bytes(64)


@pytest.mark.parametrize(("content", "expected"), [
    (elf(62), "x64"), (elf(183), "arm64"), (elf(2), "elf-machine-2"),
    (macho(0x01000007), "x64"), (macho(0x0100000C), "arm64"), (macho(0x01000012), "macho-cpu-0x1000012"),
    (pe(0x8664), "x64"), (pe(0xAA64), "arm64"), (pe(0x200), "pe-machine-0x200"),
    (b"#!/bin/sh\nexit 0\n", None),
])
def test_the_machine_is_read_from_the_header(tmp_path, content, expected):
    path = tmp_path / "navide-cli-runtime"
    path.write_bytes(content)
    assert cli_runtime.binary_architecture(path) == expected


@pytest.mark.skipif(HOST is None, reason="host machine is not x64 or arm64")
def test_a_foreign_binary_is_refused_naming_the_architecture_and_a_native_or_script_one_is_not(tmp_path):
    foreign = tmp_path / "foreign"
    foreign.write_bytes(elf(2))
    with pytest.raises(RuntimeError, match=r"START_FAILED wrong architecture: the runtime is built for elf-machine-2"):
        cli_runtime.refuse_foreign_binary(foreign)
    native = tmp_path / "native"
    native.write_bytes(elf({"x64": 62, "arm64": 183}[HOST]))
    cli_runtime.refuse_foreign_binary(native)
    script = tmp_path / "script"
    script.write_bytes(b"#!/bin/sh\nexit 0\n")
    cli_runtime.refuse_foreign_binary(script)
