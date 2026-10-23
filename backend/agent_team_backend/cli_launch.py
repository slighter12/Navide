"""Which launch shape a subject may use, and the pre-CREATE refusals around it.

G2 ticket 04, protocol delta sections 3.1 and 5. Phase A: the interface only; nothing is
implemented yet, so the admission tests fail on behaviour.
"""
from __future__ import annotations


class LaunchRefused(RuntimeError):
    """A launch refused before CREATE, with a bounded code and the stage that refused it."""

    def __init__(self, code: str, stage: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.stage = stage


def admit_composition(agent_key: str, *, platform: str, managed_credential_path: bool) -> str:
    """`"rust"` when the structured shapes are used, `"python"` for the pre-composed `argv` shape."""
    raise NotImplementedError("composition admission is not implemented")


def rust_composed_subjects() -> tuple[str, ...]:
    """Every subject whose generated declaration says `composition = rust`."""
    raise NotImplementedError("composition facts are not generated yet")
