"""A `termios` for Windows consoles, so the FROZEN measured peer (`g1e_peer.py`, SHA-256 pinned) runs unchanged under ConPTY (design section 1 row 10).

`g1e_peer.LedgerPeer.make_raw` clears the POSIX input, output and local flags with `termios.tcgetattr/tcsetattr`, re-reads them and refuses to run if OPOST, ONLCR or ECHO is still set. Windows has no
`termios`. This module offers the same functions and constants over the console modes of the standard input and output handles:

    lflag ECHO   <-> input  ENABLE_ECHO_INPUT       lflag ICANON <-> input ENABLE_LINE_INPUT       lflag ISIG <-> input ENABLE_PROCESSED_INPUT
    oflag OPOST  <-> output ENABLE_PROCESSED_OUTPUT  oflag ONLCR  <-> output without DISABLE_NEWLINE_AUTO_RETURN
    ECHONL, IEXTEN and the input flags (ICRNL, IXON, ...) have no console mode: they read back clear.

The peer process gets it from `sitecustomize` when `ACR_G3_HOSTED_TERMIOS=1` on `nt` and no `termios` exists. Whether ConPTY then delivers the peer's bytes to the reader unchanged (the byte-exact ledger) is NOT
shown by this module: only the rehearsal can show it. The console is injected (`Console`) so the mapping is tested without Windows.
"""
from __future__ import annotations

import sys
import types
from typing import Protocol

STD_INPUT, STD_OUTPUT = -10, -11
ENABLE_PROCESSED_INPUT, ENABLE_LINE_INPUT, ENABLE_ECHO_INPUT = 0x1, 0x2, 0x4
ENABLE_PROCESSED_OUTPUT, DISABLE_NEWLINE_AUTO_RETURN = 0x1, 0x8

# Synthetic POSIX-shaped constants: only their consistency with `tcgetattr/tcsetattr` matters.
IGNBRK, BRKINT, PARMRK, ISTRIP, INLCR, IGNCR, ICRNL, IXON = 1, 2, 4, 8, 16, 32, 64, 128
OPOST, ONLCR = 1, 4
ECHO, ECHONL, ICANON, ISIG, IEXTEN = 8, 64, 2, 1, 32768
CSIZE, PARENB, CS8 = 48, 256, 48
VMIN, VTIME, NCCS = 6, 5, 20
TCSANOW = 0


class Console(Protocol):
    def get_mode(self, handle: int) -> int: ...
    def set_mode(self, handle: int, mode: int) -> None: ...


class Win32Console:
    """The real console, through ctypes `kernel32` (Windows only)."""
    def __init__(self) -> None:
        import ctypes
        self._ctypes = ctypes
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    def _handle(self, std: int):
        handle = self._kernel.GetStdHandle(std)
        if handle in (0, -1, None):
            raise OSError(f"no console handle for {std}")
        return handle

    def get_mode(self, std: int) -> int:
        mode = self._ctypes.c_uint32()
        if not self._kernel.GetConsoleMode(self._handle(std), self._ctypes.byref(mode)):
            raise OSError(f"GetConsoleMode failed for {std}")
        return mode.value

    def set_mode(self, std: int, mode: int) -> None:
        if not self._kernel.SetConsoleMode(self._handle(std), mode):
            raise OSError(f"SetConsoleMode failed for {std}")


class Termios:
    """The `termios` surface `g1e_peer` uses, over a `Console`."""
    error = OSError

    def __init__(self, console: Console | None = None) -> None:
        self._console = console
        for name, value in globals().items():
            if name.isupper() and isinstance(value, int):
                setattr(self, name, value)

    @property
    def console(self) -> Console:
        if self._console is None:
            self._console = Win32Console()
        return self._console

    def tcgetattr(self, fd: int) -> list:
        mode_in, mode_out = self.console.get_mode(STD_INPUT), self.console.get_mode(STD_OUTPUT)
        lflag = (ECHO if mode_in & ENABLE_ECHO_INPUT else 0) | (ICANON if mode_in & ENABLE_LINE_INPUT else 0) | (ISIG if mode_in & ENABLE_PROCESSED_INPUT else 0)
        oflag = (OPOST if mode_out & ENABLE_PROCESSED_OUTPUT else 0) | (ONLCR if not mode_out & DISABLE_NEWLINE_AUTO_RETURN else 0)
        return [0, oflag, CS8, lflag, 0, 0, [0] * NCCS]

    def tcsetattr(self, fd: int, when: int, attributes: list) -> None:
        _, oflag, _, lflag, *_ = attributes
        mode_in, mode_out = self.console.get_mode(STD_INPUT), self.console.get_mode(STD_OUTPUT)
        mode_in &= ~(ENABLE_ECHO_INPUT | ENABLE_LINE_INPUT | ENABLE_PROCESSED_INPUT)
        mode_in |= (ENABLE_ECHO_INPUT if lflag & ECHO else 0) | (ENABLE_LINE_INPUT if lflag & ICANON else 0) | (ENABLE_PROCESSED_INPUT if lflag & ISIG else 0)
        mode_out = (mode_out | ENABLE_PROCESSED_OUTPUT) if oflag & OPOST else (mode_out & ~ENABLE_PROCESSED_OUTPUT)
        mode_out = (mode_out & ~DISABLE_NEWLINE_AUTO_RETURN) if oflag & ONLCR else (mode_out | DISABLE_NEWLINE_AUTO_RETURN)
        self.console.set_mode(STD_INPUT, mode_in)
        self.console.set_mode(STD_OUTPUT, mode_out)


def module(console: Console | None = None) -> types.ModuleType:
    """A `termios` module object whose functions use `console`."""
    impl = Termios(console)
    out = types.ModuleType("termios")
    for name in dir(impl):
        if not name.startswith("_") and name != "console":
            setattr(out, name, getattr(impl, name))
    return out


def install(console: Console | None = None, *, modules: dict | None = None) -> bool:
    """Registers the shim as `termios` unless a real one is importable. Returns True when it installed."""
    registry = sys.modules if modules is None else modules
    if "termios" in registry:
        return False
    try:
        __import__("termios")
        return False
    except ImportError:
        registry["termios"] = module(console)
        return True
