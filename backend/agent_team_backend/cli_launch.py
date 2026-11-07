"""Which launch shape a subject may use, the structured `launch` object of a wire-2 CREATE, and the
pre-CREATE refusals around them.

G2 ticket 04, protocol delta sections 2, 3.1 and 4. Python decides whether and which flags a launch
carries; the Rust runtime renders the words and spells the declared options from the generated facts.

A command is a `Command`: a string that carries the words it was composed from. The renderer, the injectors
and the rewriters make the words; nothing here reads a command line back into words.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from . import osplat
from .cli_vendors import _vendor_facts as facts

ATTEST_BYTES = 128
#: The flags the product's wrapped shape (`shellCommandArgv`) puts between the shell and the line.
WRAPPED_FLAGS = ("-lc", "-ilc")


class LaunchRefused(RuntimeError):
    """A launch refused before CREATE, with a bounded code and the stage that refused it."""

    def __init__(self, code: str, stage: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.stage = stage


def _composition_of(agent_key: str) -> str:
    vendor = facts.FACTS.get(agent_key)
    if vendor is not None:
        return vendor.composition
    if agent_key in facts.SUBJECT_COMPOSITION:
        return facts.SUBJECT_COMPOSITION[agent_key]
    raise LaunchRefused("COMPOSITION_UNSUPPORTED", "composition", f"unknown subject {agent_key!r}")


def admit_composition(agent_key: str, *, platform: str, managed_credential_path: bool) -> str:
    """`"rust"` when the structured shapes are used, `"python"` for the pre-composed `argv` shape."""
    declared = _composition_of(agent_key)
    if platform == "windows" and managed_credential_path:
        # The `credential` wrapper is POSIX shell text; Windows has none, so Python composes the argv.
        return "python"
    # A managed credential path no longer needs Python text on POSIX: the `credential` shape carries the
    # wrapper. Windows otherwise follows the declaration; only its word grammar differs.
    return declared


def rust_composed_subjects() -> tuple[str, ...]:
    """Every subject whose generated declaration says `composition = rust`."""
    vendors = tuple(key for key, vendor in facts.FACTS.items() if vendor.composition == "rust")
    aliases = tuple(key for key, composition in facts.SUBJECT_COMPOSITION.items() if composition == "rust")
    return vendors + aliases


def _cut(path: str) -> tuple[str, str]:
    data = path.encode()
    head = len(data) if len(data) <= ATTEST_BYTES else ATTEST_BYTES
    while head and head < len(data) and (data[head] & 0xC0) == 0x80:
        head -= 1
    start = max(len(data) - ATTEST_BYTES, 0)
    while start < len(data) and (data[start] & 0xC0) == 0x80:
        start += 1
    return data[:head].decode(), data[start:].decode()


def verify_argv0_attestation(argv0: object, *, wrapped_shell: str | None) -> str:
    """Check the Rust-attested `{len, head, tail}` of the executed program (delta section 4) and return
    `head`, the bounded diagnostic the owned registry stores. `wrapped_shell` is the program path Python
    sent when it knows it (compared in full up to the bounded cut), `None` when it cannot know.
    Raises `LaunchRefused("ATTESTATION_INVALID", "argv0")`."""

    def invalid(why: str):
        raise LaunchRefused("ATTESTATION_INVALID", "argv0", why)

    if not isinstance(argv0, dict) or set(argv0) != {"len", "head", "tail"}:
        invalid("argv0 must be exactly {len, head, tail}")
    length, head, tail = argv0["len"], argv0["head"], argv0["tail"]
    if type(length) is not int or length <= 0 or not isinstance(head, str) or not isinstance(tail, str):
        invalid("argv0 has a malformed field")
    if "\0" in head or "\0" in tail:
        invalid("argv0 contains NUL")
    try:
        head_bytes, tail_bytes = head.encode(), tail.encode()
    except UnicodeEncodeError:
        invalid("argv0 is not valid text")
    if len(head_bytes) > ATTEST_BYTES or len(tail_bytes) > ATTEST_BYTES:
        invalid("argv0 is over the bound")
    if length <= ATTEST_BYTES:
        if head != tail or len(head_bytes) != length:
            invalid("a short program path must equal its head and tail")
    elif not head_bytes or not tail_bytes or len(head_bytes) > length or len(tail_bytes) > length:
        invalid("argv0 head or tail does not fit the length")
    if wrapped_shell is not None:
        expected_head, expected_tail = _cut(wrapped_shell)
        if length != len(wrapped_shell.encode()) or head != expected_head or tail != expected_tail:
            invalid("argv0 is not the program that was sent")
    return head


# ---------------------------------------------------------------------------------------------
# Words (delta 2.3)
#
# A word is one of four kinds, each an object with one key:
#   {"lit": text}   text the shell reads as it stands (the user's own command line, or its remainder);
#   {"exe": path}   the program, quoted as one word;
#   {"arg": text}   one plain argument, quoted as one word behind a space;
#   {"opt": name, "value": text?}  an option the subject declares (model, effort, skip_permissions, resume,
#                   session_id, login); the sidecar spells it from the generated facts.
# An `opt` that a composer made in Python or the renderer also carries `text`, its spelling with the leading
# space, so that the text view of the command exists without the facts. The sidecar never reads `text`.
# ---------------------------------------------------------------------------------------------


def _refused(why: str) -> LaunchRefused:
    return LaunchRefused("COMPOSITION_UNREPRESENTABLE", "words", why)


def _checked(word: object) -> dict:
    """The word as a plain dict, or a refusal: exactly one kind, with text where text belongs."""
    if not isinstance(word, dict):
        raise _refused("a word is an object")
    kinds = [kind for kind in ("lit", "exe", "arg", "opt") if kind in word]
    if len(kinds) != 1 or set(word) - {kinds[0], "value", "text"}:
        raise _refused("a word is exactly one of lit, exe, arg or opt")
    kind = kinds[0]
    if not isinstance(word[kind], str):
        raise _refused(f"{kind} must be text")
    if kind == "opt":
        if not isinstance(word.get("text"), str):
            raise _refused("an opt carries its spelling as text")
        if "value" in word and not isinstance(word["value"], str):
            raise _refused("an opt value must be text")
    elif set(word) != {kind}:
        raise _refused(f"{kind} takes no other key")
    return dict(word)


def _text_of(word: dict, quote: Callable[[str], str]) -> str:
    if "lit" in word:
        return word["lit"]
    if "exe" in word:
        return quote(word["exe"])
    if "arg" in word:
        return " " + quote(word["arg"])
    return word["text"]


class Command(str):
    """A command line and the words it was composed from.

    The text is derived from the words, so the two cannot disagree. A command that is only text (a user's
    own command line, a plugin's result) is one `lit` word: the sidecar renders it as it stands, so the child
    runs what the text says."""

    _words: tuple[dict, ...]
    _quote: Callable[[str], str]

    @classmethod
    def from_words(cls, words: Iterable[object], *, quote: Callable[[str], str] | None = None) -> "Command":
        quote = quote or osplat.paths.quote_arg
        checked = tuple(_checked(word) for word in words)
        command = str.__new__(cls, "".join(_text_of(word, quote) for word in checked))
        command._words = checked
        command._quote = quote
        return command

    @classmethod
    def literal(cls, text: str) -> "Command":
        return cls.from_words([{"lit": str(text)}])

    @property
    def words(self) -> tuple[dict, ...]:
        return getattr(self, "_words", None) or ({"lit": str(self)},)

    def appended(self, *args: str) -> "Command":
        """This command with each argument as one more `arg` word."""
        return Command.from_words([*self.words, *({"arg": arg} for arg in args)],
                                  quote=getattr(self, "_quote", None))

    def wire_words(self) -> list[dict]:
        """The words as the sidecar reads them: an `opt` without the spelling its text view needed."""
        return [{key: value for key, value in word.items() if key != "text"} for word in self.words]


def _as_command(text: object) -> Command:
    return text if isinstance(text, Command) else Command.literal(str(text))


def append_args(command: Any, *args: str) -> Any:
    """`command` with `args` appended as plain arguments to the real command line.

    The product's wrapped shape is `[shell, flag, line]`: the line is the last element and the only one that
    changes. The result keeps the shape of `command`."""
    if isinstance(command, list):
        if not command:
            raise _refused("the command is empty")
        return [*command[:-1], _as_command(command[-1]).appended(*args)]
    return _as_command(command).appended(*args)


_FIRST_TOKEN = re.compile(r"^\s*(?:'[^']*'|\"[^\"]*\"|\S+)")


def _head_and_rest(command: Command) -> tuple[dict, str, tuple[dict, ...]] | None:
    """The first word, the first token of a literal head, and the words after the head."""
    first, rest = command.words[0], command.words[1:]
    if "lit" in first:
        match = _FIRST_TOKEN.match(first["lit"])
        return (first, first["lit"], rest) if match else None
    return (first, "", rest) if "exe" in first else None


def with_executable(command: Any, executable: str) -> Any:
    """`command` with its program replaced by `executable`, the words after it unchanged.

    A program that the composer wrote as an `exe` word is replaced as that word. A user's own command line is
    a literal, and its first token is swapped inside it, the way the shell would read that token."""
    if isinstance(command, list):
        return [*command[:-1], with_executable(command[-1], executable)] if command else command
    built = _as_command(command)
    parts = _head_and_rest(built)
    if parts is None:
        return command
    first, lit, rest = parts
    if "exe" in first:
        head = {"exe": executable}
    else:
        match = _FIRST_TOKEN.match(lit)
        quoted = (built._quote if hasattr(built, "_quote") else osplat.paths.quote_arg)(executable)
        head = {"lit": f"{lit[:match.start()]}{quoted}{lit[match.end():]}"}
    return Command.from_words([head, *rest], quote=getattr(built, "_quote", None))


def with_resume_id(command: Any, old_id: str, new_id: str) -> Any:
    """`command` with the session id `old_id` swapped for `new_id`, in every word that holds it.

    The one routine for repointing a resume: a `resume` option has its value swapped, an argument or a literal has
    the id swapped inside its text. That is the named compatibility rule of the baseline (a text replace of the
    id, wherever it stands in the line), so a command that is only text is repointed the way it always was.
    A command that does not hold the id comes back as it was."""
    if isinstance(command, list):
        return [*command[:-1], with_resume_id(command[-1], old_id, new_id)] if command else command
    if command is None:
        return ""
    built = _as_command(command)
    if not old_id or old_id not in str(built):
        return command
    words = [{key: value.replace(old_id, new_id) if isinstance(value, str) else value for key, value in word.items()}
             for word in built.words]
    return Command.from_words(words, quote=getattr(built, "_quote", None))


def with_login(command: Any, login_args: str) -> Any:
    """`command` reduced to its program plus the CLI's own sign-in trigger (`login_args`, as declared).

    Every other word is dropped: a sign-in subcommand takes none of the launch flags."""
    if isinstance(command, list):
        return [*command[:-1], with_login(command[-1], login_args)] if command else command
    built = _as_command(command)
    parts = _head_and_rest(built)
    if parts is None:
        return command
    first, lit, _ = parts
    if "lit" in first:
        match = _FIRST_TOKEN.match(lit)
        first = {"lit": match.group(0).strip()}
    words: list[dict] = [first]
    if login_args:
        words.append({"opt": "login", "text": f" {login_args}"})
    return Command.from_words(words, quote=getattr(built, "_quote", None))


def attach_words(command: Any, words: object) -> Any:
    """`command` carrying `words`, the structure its composer sent beside the text.

    The text must be what the words compose to: a renderer whose two outputs disagree is refused before
    CREATE instead of running one thing on one runtime and another on the other. `words` of `None` leaves the
    command as the text it is."""
    if words is None:
        return command
    if not isinstance(words, list):
        raise _refused("words must be a list")
    built = Command.from_words(words)
    line = command[-1] if isinstance(command, list) and command else command
    if not isinstance(line, str) or str(line) != str(built):
        raise _refused("the words do not compose to the command text")
    return [*command[:-1], built] if isinstance(command, list) else built


def build_launch(command: Any, *, credential: dict | None = None) -> dict:
    """The structured launch for `command`, a string or a list whose last element is the command line.

    `credential` is a `credential_launch.wrap_plan`: the launch is then wrapped in the `credential` shape
    that the Rust runtime renders around it (delta 10.2)."""
    launch = _launch_of(command)
    if credential is None:
        return launch
    outer = {"shape": "credential", "helper": list(credential["helper"]), "unset": list(credential["unset"]),
             "inner": launch}
    if credential.get("entrypoint") is not None:
        outer["entrypoint"] = credential["entrypoint"]
    return outer


def _launch_of(command: Any) -> dict:
    if isinstance(command, str):
        return {"shape": "direct", "form": "string", "words": _as_command(command).wire_words()}
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise LaunchRefused("COMPOSITION_UNREPRESENTABLE", "command", "the command is not a string or a list of strings")
    if not command:
        raise LaunchRefused("COMPOSITION_UNREPRESENTABLE", "command", "the command is empty")
    words = _as_command(command[-1]).wire_words()
    if len(command) == 3 and command[1] in WRAPPED_FLAGS:
        return {"shape": "wrapped", "shell": command[0], "flags": [command[1]], "words": words}
    # A list's last element is its command line; the sidecar appends the words to an empty last element.
    return {"shape": "direct", "form": "list", "elements": [*command[:-1], ""], "words": words}


def argv_launch(argv: list[str]) -> dict:
    """The pre-composed shape, kept for subjects whose declared composition is `python`."""
    return {"shape": "argv", "argv": list(argv)}


def check_text(*values: object) -> None:
    """A NUL or a string that cannot be encoded is refused before dispatch (delta 2.7)."""
    for value in values:
        if isinstance(value, str):
            if "\0" in value:
                raise LaunchRefused("CREATE_INVALID_TEXT", "pre-dispatch", "text contains NUL")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise LaunchRefused("CREATE_INVALID_TEXT", "pre-dispatch", "text is not valid Unicode") from None
        elif isinstance(value, (list, tuple)):
            check_text(*value)
        elif isinstance(value, dict):
            check_text(*value.keys(), *value.values())
