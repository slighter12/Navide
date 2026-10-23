"""Which launch shape a subject may use, the structured `launch` object of a wire-2 CREATE, and the
pre-CREATE refusals around them.

G2 ticket 04, protocol delta sections 2, 3.1 and 4. Python decides whether and which flags a launch
carries; the Rust runtime renders the words and spells the declared options from the generated facts.
"""
from __future__ import annotations

import shlex

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
    if platform == "windows":
        return "python"  # ticket 04 transition, removed by ticket 07
    if declared == "rust" and managed_credential_path:
        # The credential wrapper (`wrap_command`) is Python-composed text around the launch.
        raise LaunchRefused("COMPOSITION_UNSUPPORTED", "credential_wrapper",
                            "a managed credential path is not composed by the Rust runtime yet")
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
# ---------------------------------------------------------------------------------------------

_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_@%+=:,./-")


def quote(arg: str) -> str:
    """The `shlex.quote` rule, the one explicit shell interface of the wrapped line (delta 2.5)."""
    if not arg:
        return "''"
    if all(ch in _SAFE for ch in arg):
        return arg
    return "'" + arg.replace("'", "'\"'\"'") + "'"


def scan(text: str) -> list[tuple[int, int, str]]:
    """`shlex.split` (POSIX, no comments) with the span of every word. Raises `ValueError` when the
    text does not split."""
    words: list[tuple[int, int, str]] = []
    size, at = len(text), 0
    while at < size:
        if text[at] in " \t\r\n":
            at += 1
            continue
        start, value = at, []
        while at < size and text[at] not in " \t\r\n":
            ch = text[at]
            if ch == "\\":
                if at + 1 >= size:
                    raise ValueError("no escaped character")
                value.append(text[at + 1])
                at += 2
            elif ch == "'":
                end = text.find("'", at + 1)
                if end < 0:
                    raise ValueError("no closing quotation")
                value.append(text[at + 1:end])
                at = end + 1
            elif ch == '"':
                at += 1
                while True:
                    if at >= size:
                        raise ValueError("no closing quotation")
                    if text[at] == '"':
                        at += 1
                        break
                    if text[at] == "\\":
                        if at + 1 >= size:
                            raise ValueError("no closing quotation")
                        nxt = text[at + 1]
                        value.append(nxt if nxt in '"\\' else "\\" + nxt)
                        at += 2
                    else:
                        value.append(text[at])
                        at += 1
            else:
                value.append(ch)
                at += 1
        words.append((start, at, "".join(value)))
    return words


def render(words: list[dict], agent_key: str) -> str:
    """The text the Rust runtime builds from `words`; used for presence checks and previews only, never
    as the child's command."""
    out = []
    for word in words:
        if "lit" in word:
            out.append(word["lit"])
        elif "exe" in word:
            out.append(quote(word["exe"]))
        elif "arg" in word:
            out.append(" " + quote(word["arg"]))
        else:
            out.append(_spell(agent_key, word["opt"], word.get("value")))
    return "".join(out)


def _declared(agent_key: str, option: str):
    vendor = facts.FACTS.get(agent_key)
    if vendor is None:
        return None
    status = {"model": vendor.capabilities.model, "effort": vendor.capabilities.effort,
              "skip_permissions": vendor.capabilities.skip_permissions,
              "resume": vendor.capabilities.resume_by_id, "session_id": vendor.capabilities.session_id_at_launch,
              "login": vendor.capabilities.login}.get(option)
    return vendor if status == "integrated" else None


def _spell(agent_key: str, option: str, value: str | None) -> str:
    vendor = _declared(agent_key, option)
    assert vendor is not None, option
    if option == "model":
        return f" {vendor.model_flag} {quote(value)}"
    if option == "effort":
        return f" {vendor.effort_flag} {quote(value)}"
    if option == "skip_permissions":
        return f" {vendor.permission_flag}"
    if option == "resume":
        return " " + vendor.resume_args.replace("{id}", quote(value))
    if option == "session_id":
        return f" {vendor.session_id_flag} {quote(value)}"
    return f" {vendor.login_args}"


def _extractable(agent_key: str) -> list[tuple[str, str, bool]]:
    """(option id, leading flag, takes a value) for every option Rust can spell for this subject."""
    out: list[tuple[str, str, bool]] = []
    for option, flag, valued in (("model", "model_flag", True), ("effort", "effort_flag", True),
                                 ("session_id", "session_id_flag", True),
                                 ("skip_permissions", "permission_flag", False)):
        vendor = _declared(agent_key, option)
        if vendor is not None and getattr(vendor, flag):
            out.append((option, getattr(vendor, flag), valued))
    vendor = _declared(agent_key, "resume")
    if vendor is not None and vendor.resume_args and vendor.resume_args.endswith("{id}"):
        head = vendor.resume_args[:-4].strip()
        if head and len(head.split()) == 1:
            out.append(("resume", head, True))
    return out


def tokenize(agent_key: str, text: str) -> list[dict]:
    """Split a submitted command into maximal `lit` chunks and `opt` words.

    Only what Rust can render back without changing the launch is extracted; everything else stays
    literal. A flag that occurs more than once, a value outside a closed set, a non-canonical spelling
    or a separator other than plain spaces keeps its occurrence literal: no flag is added or lost."""
    try:
        spans = scan(text)
    except ValueError:
        return [{"lit": text}]
    options = _extractable(agent_key)
    vendor = facts.FACTS.get(agent_key)
    extracted: list[tuple[int, int, dict]] = []
    for option, flag, valued in options:
        hits = [i for i, (_, _, value) in enumerate(spans) if value == flag]
        if len(hits) != 1 or hits[0] == 0:
            continue
        i = hits[0]
        start, end, _ = spans[i]
        if text[start:end] != flag or not _plain_gap(text, spans[i - 1][1], start):
            continue
        if not valued:
            extracted.append((start, end, {"opt": option}))
            continue
        if i + 1 >= len(spans):
            continue
        v_start, v_end, value = spans[i + 1]
        if text[v_start:v_end] != quote(value) or not _plain_gap(text, end, v_start) or value.startswith("-"):
            continue
        if option == "effort" and (vendor is None or not vendor.known_efforts or value not in vendor.known_efforts):
            continue
        extracted.append((start, v_end, {"opt": option, "value": value}))
    extracted.sort(key=lambda item: item[0])
    out: list[dict] = []
    cursor = 0
    for start, end, word in extracted:
        lead = start
        while lead > cursor and text[lead - 1] == " ":
            lead -= 1
        if lead > cursor:
            out.append({"lit": text[cursor:lead]})
        out.append(word)
        cursor = end
    if cursor < len(text):
        out.append({"lit": text[cursor:]})
    return out


def _plain_gap(text: str, left: int, right: int) -> bool:
    gap = text[left:right]
    return bool(gap) and set(gap) == {" "}


def _login_words(agent_key: str, text: str) -> list[dict] | None:
    vendor = _declared(agent_key, "login")
    if vendor is None or not vendor.login_args:
        return None
    try:
        spans = scan(text)
        wanted = shlex.split(vendor.login_args)
    except ValueError:
        return None
    if len(spans) != len(wanted) + 1 or [value for _, _, value in spans[1:]] != wanted:
        return None
    if not _plain_gap(text, spans[0][1], spans[1][0]) or text[spans[-1][1]:].strip():
        return None
    return [{"lit": text[:spans[0][1]]}, {"opt": "login"}]


def _suffix_words(suffix: str, stage: str) -> list[dict]:
    """The text an injector appended, as plain `arg` words. Anything that is not exactly a run of
    canonically quoted plain strings cannot be handed to the shell by Rust unchanged."""
    if not suffix:
        return []
    try:
        spans = scan(suffix)
    except ValueError:
        raise LaunchRefused("COMPOSITION_UNREPRESENTABLE", stage, "the appended text does not split") from None
    words = [{"arg": value} for _, _, value in spans]
    if render(words, "") != suffix:
        raise LaunchRefused("COMPOSITION_UNREPRESENTABLE", stage,
                            "the appended text is not a run of plain quoted strings")
    return words


def _command_text(command: str | list[str]) -> tuple[str, str | list[str]]:
    """(form, text of the part the words describe). `wrapped` is the product shape `[shell, flag, line]`."""
    if isinstance(command, str):
        return "string", command
    if len(command) == 3 and command[1] in WRAPPED_FLAGS:
        return "wrapped", command[2]
    if not command:
        raise LaunchRefused("COMPOSITION_UNREPRESENTABLE", "command", "the command is empty")
    return "list", command[-1]


def build_launch(agent_key: str, base: str | list[str], final: str | list[str], *, login: bool = False) -> dict:
    """The structured launch for `final`, the command after every injector, given `base`, the command
    just before the injectors ran. The submitted part is tokenised; what the injectors appended
    becomes plain `arg` words. A result that is not `base + suffix` is refused before CREATE."""
    form, base_text = _command_text(base)
    final_form, final_text = _command_text(final)
    if form != final_form or not isinstance(final_text, str) or not isinstance(base_text, str):
        raise LaunchRefused("COMPOSITION_UNREPRESENTABLE", "injector", "an injector changed the command's shape")
    if form == "wrapped":
        if final[0] != base[0] or final[1] != base[1]:
            raise LaunchRefused("COMPOSITION_UNREPRESENTABLE", "injector", "an injector changed the shell")
    elif form == "list" and list(final[:-1]) != list(base[:-1]):
        raise LaunchRefused("COMPOSITION_UNREPRESENTABLE", "injector", "an injector changed the list prefix")
    if not final_text.startswith(base_text):
        raise LaunchRefused("COMPOSITION_UNREPRESENTABLE", "injector", "an injector edited the submitted command")
    suffix = _suffix_words(final_text[len(base_text):], "injector")
    if form == "list":
        return {"shape": "direct", "form": "list", "elements": list(base), "words": suffix}
    words = (_login_words(agent_key, base_text) if login else None) or tokenize(agent_key, base_text)
    words = words + suffix
    if form == "wrapped":
        return {"shape": "wrapped", "shell": base[0], "flags": [base[1]], "words": words}
    return {"shape": "direct", "form": "string", "words": words}


def argv_launch(argv: list[str]) -> dict:
    """The pre-composed shape, kept for subjects Python still composes and for the Windows transition."""
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
