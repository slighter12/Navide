//! Launch composition (G2 delta to protocol v8b, revision 2): turns the structured `launch` object
//! of a wire-2 CREATE into the program and arguments of the child, and decides which shapes a
//! subject may use.
//!
//! Every handwritten piece here has a documented interface reason (delta section 7): the POSIX
//! word quoting, the `shlex.split` grammar and the wrapper assembly. No crate is used for them.
#![allow(dead_code)]
use serde_json::Value;

#[path = "vendor_facts.rs"]
mod vendor_facts;

/// What the sidecar will spawn, and who composed it (the `argv_source` receipt field).
#[derive(Debug, PartialEq, Eq)]
pub struct Composed {
    pub program: String,
    pub args: Vec<String>,
    pub argv_source: &'static str,
    /// Bounded Rust attestation of the executed program (delta section 4).
    pub argv0: Argv0,
    /// `Some("credential")` when the managed-credential outer wrapper was applied (delta 10.2).
    pub outer: Option<&'static str>,
}

/// `len` is the full byte length of the program path; `head`/`tail` are its longest prefix/suffix of
/// at most 128 bytes that fall on character boundaries.
#[derive(Debug, PartialEq, Eq)]
pub struct Argv0 {
    pub len: usize,
    pub head: String,
    pub tail: String,
}

#[derive(Debug, PartialEq, Eq)]
pub enum ComposeError {
    /// Unknown, missing or doubled `shape`, a legacy `argv` key, or a malformed field.
    Shape(String),
    /// An `opt` the subject's generated facts do not declare as integrated.
    Option(String),
    /// A string that does not split, or a composition with no program.
    Compose(String),
    /// NUL in a program, word, environment entry or directory.
    Validate(String),
    /// The shape is not admitted for this subject on this platform.
    Composition(String),
    /// A `credential` launch the managed-credential wrapper would not wrap (delta 10.2).
    Credential(String),
}

const ATTEST_BYTES: usize = 128;

fn shape(message: &str) -> ComposeError {
    ComposeError::Shape(message.to_string())
}

fn text<'a>(value: &'a Value, what: &str) -> Result<&'a str, ComposeError> {
    let text = value.as_str().ok_or_else(|| shape(&format!("{what} must be a string")))?;
    if text.contains('\0') {
        return Err(ComposeError::Validate(format!("{what} contains NUL")));
    }
    Ok(text)
}

/// POSIX `shlex.quote`: the one explicit shell interface of the wrapped line (delta 2.5).
fn quote(arg: &str) -> String {
    let safe = |c: char| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c);
    if arg.is_empty() {
        "''".to_string()
    } else if arg.chars().all(safe) {
        arg.to_string()
    } else {
        format!("'{}'", arg.replace('\'', "'\"'\"'"))
    }
}

/// POSIX `shlex.split` (no comments, whitespace splits): the grammar of Python's `parse_command`.
fn split(line: &str) -> Result<Vec<String>, ComposeError> {
    let mut words = Vec::new();
    let mut current = String::new();
    let mut in_word = false;
    let mut chars = line.chars();
    while let Some(c) = chars.next() {
        match c {
            ' ' | '\t' | '\r' | '\n' => {
                if in_word {
                    words.push(std::mem::take(&mut current));
                    in_word = false;
                }
            }
            '\\' => {
                current.push(chars.next().ok_or_else(|| {
                    ComposeError::Compose("no escaped character".to_string())
                })?);
                in_word = true;
            }
            '\'' => {
                in_word = true;
                loop {
                    match chars.next() {
                        Some('\'') => break,
                        Some(inner) => current.push(inner),
                        None => return Err(ComposeError::Compose("no closing quotation".to_string())),
                    }
                }
            }
            '"' => {
                in_word = true;
                loop {
                    match chars.next() {
                        Some('"') => break,
                        Some('\\') => match chars.next() {
                            Some(escaped @ ('"' | '\\')) => current.push(escaped),
                            Some(other) => {
                                current.push('\\');
                                current.push(other);
                            }
                            None => {
                                return Err(ComposeError::Compose("no closing quotation".to_string()))
                            }
                        },
                        Some(inner) => current.push(inner),
                        None => return Err(ComposeError::Compose("no closing quotation".to_string())),
                    }
                }
            }
            other => {
                current.push(other);
                in_word = true;
            }
        }
    }
    if in_word {
        words.push(current);
    }
    Ok(words)
}

/// `subprocess.list2cmdline` for one element: the rule the Python runtime renders a Windows command line
/// with (`osplat/_windows.py` `spawn`: `subprocess.list2cmdline(tail)`). Quoted when empty or holding a
/// space or tab; a quote is `\"`, backslashes are doubled only before a quote and, in a quoted element,
/// at the end.
fn quote_windows(arg: &str) -> String {
    let need = arg.is_empty() || arg.contains([' ', '\t']);
    let mut out = String::new();
    if need {
        out.push('"');
    }
    let mut slashes = 0;
    for c in arg.chars() {
        match c {
            '\\' => slashes += 1,
            '"' => {
                out.push_str(&"\\".repeat(slashes * 2));
                slashes = 0;
                out.push_str("\\\"");
            }
            _ => {
                out.push_str(&"\\".repeat(slashes));
                slashes = 0;
                out.push(c);
            }
        }
    }
    out.push_str(&"\\".repeat(slashes));
    if need {
        out.push_str(&"\\".repeat(slashes));
        out.push('"');
    }
    out
}

/// `CommandLineToArgvW`, the grammar of `osplat/_windows.py` `_split_command_line` (which asks the OS).
/// The first word is read without escapes (a quote only brackets it); later words use `2n` backslashes
/// plus a quote -> `n` backslashes and a toggle, `2n+1` -> `n` backslashes and a literal quote, and a
/// doubled quote inside quotes is a literal quote. Whitespace is space and tab. An unmatched quote runs
/// to the end, as the OS does, so this never fails.
fn split_windows(line: &str) -> Vec<String> {
    let chars: Vec<char> = line.chars().collect();
    let size = chars.len();
    let blank = |c: char| c == ' ' || c == '\t';
    let (mut words, mut at) = (Vec::new(), 0);
    loop {
        while at < size && blank(chars[at]) {
            at += 1;
        }
        if at >= size {
            return words;
        }
        let mut value = String::new();
        if words.is_empty() {
            if chars[at] == '"' {
                let end = chars[at + 1..].iter().position(|&c| c == '"').map_or(size, |found| at + 1 + found);
                value.extend(&chars[at + 1..end]);
                at = (end + 1).min(size);
            } else {
                while at < size && !blank(chars[at]) {
                    value.push(chars[at]);
                    at += 1;
                }
            }
            words.push(value);
            continue;
        }
        let mut in_quote = false;
        while at < size {
            match chars[at] {
                '\\' => {
                    let mut run = 0;
                    while at < size && chars[at] == '\\' {
                        run += 1;
                        at += 1;
                    }
                    if at < size && chars[at] == '"' {
                        value.push_str(&"\\".repeat(run / 2));
                        if run % 2 == 1 {
                            value.push('"');
                            at += 1;
                        }
                    } else {
                        value.push_str(&"\\".repeat(run));
                    }
                }
                '"' => {
                    if in_quote && at + 1 < size && chars[at + 1] == '"' {
                        value.push('"');
                        at += 2;
                    } else {
                        in_quote = !in_quote;
                        at += 1;
                    }
                }
                c if blank(c) && !in_quote => break,
                c => {
                    value.push(c);
                    at += 1;
                }
            }
        }
        words.push(value);
    }
}

/// How the words of a launch are written and read back: one per platform family.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Grammar {
    Posix,
    Windows,
}

impl Grammar {
    fn quote(self, arg: &str) -> String {
        match self {
            Grammar::Posix => quote(arg),
            Grammar::Windows => quote_windows(arg),
        }
    }

    fn split(self, line: &str) -> Result<Vec<String>, ComposeError> {
        match self {
            Grammar::Posix => split(line),
            Grammar::Windows => Ok(split_windows(line)),
        }
    }
}

fn declared(
    agent_key: &str,
    option: &str,
) -> Result<&'static vendor_facts::VendorFacts, ComposeError> {
    let refuse = |why: &str| ComposeError::Option(format!("{option}: {why}"));
    let facts = vendor_facts::find(agent_key).ok_or_else(|| refuse("the subject declares no options"))?;
    let status = match option {
        "model" => facts.capabilities.model,
        "effort" => facts.capabilities.effort,
        "skip_permissions" => facts.capabilities.skip_permissions,
        "resume" => facts.capabilities.resume_by_id,
        "session_id" => facts.capabilities.session_id_at_launch,
        "login" => facts.capabilities.login,
        _ => return Err(refuse("unknown option")),
    };
    if status != vendor_facts::Status::Integrated {
        return Err(refuse("not integrated for this subject"));
    }
    Ok(facts)
}

/// The text of one `opt` word (with its leading space), spelled from the generated facts.
fn spell(agent_key: &str, option: &str, value: Option<&str>, grammar: Grammar) -> Result<String, ComposeError> {
    let quote = |text: &str| grammar.quote(text);
    let facts = declared(agent_key, option)?;
    let need = || value.ok_or_else(|| ComposeError::Option(format!("{option}: a value is required")));
    let missing = || ComposeError::Option(format!("{option}: no flag declared"));
    Ok(match option {
        "model" => format!(" {} {}", facts.model_flag.ok_or_else(missing)?, quote(need()?)),
        "effort" => {
            let value = need()?;
            if !facts.known_efforts.is_some_and(|known| known.contains(&value)) {
                return Err(ComposeError::Option(format!("effort: {value:?} is not a declared effort")));
            }
            format!(" {} {}", facts.effort_flag.ok_or_else(missing)?, quote(value))
        }
        "skip_permissions" => format!(" {}", facts.permission_flag.ok_or_else(missing)?),
        "resume" => format!(" {}", facts.resume_args.ok_or_else(missing)?.replace("{id}", &quote(need()?))),
        "session_id" => format!(" {} {}", facts.session_id_flag.ok_or_else(missing)?, quote(need()?)),
        "login" => format!(" {}", facts.login_args.ok_or_else(missing)?),
        _ => unreachable!("declared() rejects unknown options"),
    })
}

/// Render the ordered words of a launch to shell text (delta 2.3).
fn render(agent_key: &str, words: &Value, grammar: Grammar) -> Result<String, ComposeError> {
    let mut out = String::new();
    for word in words.as_array().ok_or_else(|| shape("words must be an array"))? {
        let object = word.as_object().ok_or_else(|| shape("a word must be an object"))?;
        if let Some(lit) = object.get("lit") {
            out.push_str(text(lit, "lit")?);
        } else if let Some(exe) = object.get("exe") {
            out.push_str(&grammar.quote(text(exe, "exe")?));
        } else if let Some(arg) = object.get("arg") {
            out.push(' ');
            out.push_str(&grammar.quote(text(arg, "arg")?));
        } else if let Some(option) = object.get("opt") {
            let value = match object.get("value") {
                Some(value) => Some(text(value, "opt value")?),
                None => None,
            };
            out.push_str(&spell(agent_key, text(option, "opt")?, value, grammar)?);
        } else {
            return Err(shape("a word is lit, exe, arg or opt"));
        }
    }
    Ok(out)
}

/// The longest prefix of at most `ATTEST_BYTES` bytes that ends on a character boundary.
fn head_of(path: &str) -> &str {
    let mut cut = path.len().min(ATTEST_BYTES);
    while !path.is_char_boundary(cut) {
        cut -= 1;
    }
    &path[..cut]
}

/// The longest suffix of at most `ATTEST_BYTES` bytes that starts on a character boundary.
fn tail_of(path: &str) -> &str {
    let mut cut = path.len().saturating_sub(ATTEST_BYTES);
    while !path.is_char_boundary(cut) {
        cut += 1;
    }
    &path[cut..]
}

/// The bounded attestation of `program` (delta section 4).
pub fn attest(program: &str) -> Argv0 {
    Argv0 { len: program.len(), head: head_of(program).to_string(), tail: tail_of(program).to_string() }
}

fn finish(argv: Vec<String>) -> Result<Composed, ComposeError> {
    let mut parts = argv.into_iter();
    let program = parts.next().filter(|program| !program.is_empty());
    let program = program.ok_or_else(|| ComposeError::Compose("no program".to_string()))?;
    let argv0 = attest(&program);
    Ok(Composed { program, args: parts.collect(), argv_source: "rust", argv0, outer: None })
}

fn credential_refused(why: &str) -> ComposeError {
    ComposeError::Credential(why.to_string())
}

/// Characters `simple_command_args` refuses in a literal invocation (base.py).
const SHELL_METACHARACTERS: &str = ";&|<>`$%#()\n\r";
/// Characters the list form of `wrap_command` also declines (credential_launch.py).
const GLOB_CHARACTERS: &str = "~*?[]{}!";

fn is_variable_name(name: &str) -> bool {
    !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// `shlex.join`.
fn join(words: &[String]) -> String {
    words.iter().map(|word| quote(word)).collect::<Vec<_>>().join(" ")
}

fn base_name(path: &str) -> &str {
    path.trim_end_matches('/').rsplit('/').next().unwrap_or("")
}

fn stem(path: &str) -> &str {
    let name = base_name(path);
    match name.rfind('.') {
        Some(at) if at > 0 => &name[..at],
        _ => name,
    }
}

/// The managed-credential outer wrapper (delta 10.2): the same text `credential_launch.wrap_command`
/// builds, from typed data. Every predicate of `wrap_command` is re-checked here.
fn compose_credential(agent_key: &str, object: &serde_json::Map<String, Value>) -> Result<Composed, ComposeError> {
    let strings = |key: &str| -> Result<Vec<String>, ComposeError> {
        let array = object.get(key).and_then(Value::as_array).ok_or_else(|| shape(&format!("credential needs {key}")))?;
        array.iter().map(|item| text(item, key).map(String::from)).collect()
    };
    let helper = strings("helper")?;
    let unset = strings("unset")?;
    let entrypoint = match object.get("entrypoint") {
        Some(value) => Some(text(value, "entrypoint")?.to_string()),
        None => None,
    };
    let inner = object.get("inner").ok_or_else(|| shape("credential needs an inner launch"))?;
    let inner_object = inner.as_object().ok_or_else(|| shape("inner must be an object"))?;
    let inner_shape = inner_object.get("shape").and_then(Value::as_str);
    if !matches!(inner_shape, Some("wrapped" | "direct")) {
        return Err(shape("the inner launch is wrapped or direct"));
    }
    let composed = compose(agent_key, inner)?;
    if helper.is_empty() || helper[0].is_empty() {
        return Err(credential_refused("the helper has no program"));
    }
    if unset.is_empty() || !unset.iter().all(|name| is_variable_name(name)) {
        return Err(credential_refused("unset names are plain variable names"));
    }
    if vendor_facts::find(agent_key).is_none() {
        return Err(credential_refused("this subject has no managed credential wrapper"));
    }
    let words = inner_object.get("words").cloned().unwrap_or(Value::Array(vec![]));
    let line = render(agent_key, &words, Grammar::Posix)?;
    if line.chars().any(|c| SHELL_METACHARACTERS.contains(c)) {
        return Err(credential_refused("the line holds a shell metacharacter"));
    }
    let (program, args, argv0);
    if inner_shape == Some("wrapped") {
        if entrypoint.is_some() {
            return Err(credential_refused("an entrypoint belongs to the direct form"));
        }
        let flags = inner_object.get("flags").and_then(Value::as_array).map(Vec::as_slice).unwrap_or_default();
        let sole = match flags {
            [flag] => flag.as_str().filter(|flag| matches!(*flag, "-c" | "-lc" | "-ilc")),
            _ => None,
        };
        let shell = base_name(&composed.program);
        if sole.is_none() || !matches!(shell, "bash" | "zsh") {
            return Err(credential_refused("the shell and flag are not a wrappable pair"));
        }
        if line.chars().any(|c| GLOB_CHARACTERS.contains(c)) {
            return Err(credential_refused("the line holds a glob character"));
        }
        let parts = split(&line)?;
        let first = parts.first().ok_or_else(|| credential_refused("the line has no program"))?;
        if base_name(first) != agent_key {
            return Err(credential_refused("the line does not start the subject"));
        }
        let name = quote(first);
        let prefix = join(&helper);
        let rest = join(&parts[1..]);
        let (kind, resolved) = if shell == "zsh" {
            (
                format!("[[ $(builtin whence -w -- {name}) == {} ]]", quote(&format!("{first}: command"))),
                format!("$(builtin whence -- {name})"),
            )
        } else {
            (format!("[ \"$(type -t -- {name})\" = file ]"), format!("$(command -v -- {name})"))
        };
        let cleanup = format!("unset {}", unset.join(" "));
        let script = format!(
            "if {kind}; then \
             if {prefix} --shell-check -- \"{resolved}\" {rest}; then {cleanup}; exec {line}; \
             else _navide_credential_status=$?; {cleanup}; \
             if [ \"$_navide_credential_status\" -eq 76 ]; then {line}; \
             else exit \"$_navide_credential_status\"; fi; fi; \
             else {prefix} --probe && {{ {cleanup}; {line}; }}; fi"
        );
        program = composed.program;
        let mut all = composed.args;
        all.pop();
        all.push(script);
        args = all;
        argv0 = composed.argv0;
    } else {
        if inner_object.get("form").and_then(Value::as_str) != Some("string") {
            return Err(credential_refused("the direct form wraps a string command"));
        }
        let entrypoint = entrypoint.filter(|entry| !entry.is_empty()).ok_or_else(|| credential_refused("the direct form needs its entrypoint"))?;
        if stem(&composed.program) != agent_key {
            return Err(credential_refused("the command does not start the subject"));
        }
        program = helper[0].clone();
        let mut all: Vec<String> = helper[1..].to_vec();
        all.extend(["--entrypoint".to_string(), entrypoint.clone(), "--".to_string(), entrypoint]);
        all.extend(composed.args);
        args = all;
        argv0 = attest(&program);
    }
    if program.is_empty() {
        return Err(ComposeError::Compose("no program".to_string()));
    }
    Ok(Composed { program, args, argv_source: "rust", argv0, outer: Some("credential") })
}

/// Compose a structured launch for `agent_key` with the POSIX grammar.
pub fn compose(agent_key: &str, launch: &Value) -> Result<Composed, ComposeError> {
    compose_in(agent_key, launch, Grammar::Posix)
}

/// Compose a structured launch for `agent_key` with the Windows grammar (`direct` shapes only: the
/// `wrapped` and `credential` shapes are POSIX shell text and are refused by `admit`).
pub fn compose_windows(agent_key: &str, launch: &Value) -> Result<Composed, ComposeError> {
    compose_in(agent_key, launch, Grammar::Windows)
}

fn compose_in(agent_key: &str, launch: &Value, grammar: Grammar) -> Result<Composed, ComposeError> {
    let object = launch.as_object().ok_or_else(|| shape("launch must be an object"))?;
    let words = object.get("words").cloned().unwrap_or(Value::Array(vec![]));
    match object.get("shape").and_then(Value::as_str) {
        Some("wrapped" | "credential") if grammar == Grammar::Windows => {
            Err(ComposeError::Composition("the wrapped and credential shapes are POSIX shell text".to_string()))
        }
        Some("wrapped") => {
            let shell = text(object.get("shell").ok_or_else(|| shape("wrapped needs a shell"))?, "shell")?;
            let mut argv = vec![shell.to_string()];
            for flag in object.get("flags").and_then(Value::as_array).ok_or_else(|| shape("wrapped needs flags"))? {
                argv.push(text(flag, "flag")?.to_string());
            }
            argv.push(render(agent_key, &words, grammar)?);
            finish(argv)
        }
        Some("direct") => match object.get("form").and_then(Value::as_str) {
            Some("string") => finish(grammar.split(&render(agent_key, &words, grammar)?)?),
            Some("list") => {
                let elements = object.get("elements").and_then(Value::as_array);
                let mut argv = Vec::new();
                for element in elements.ok_or_else(|| shape("direct list needs elements"))? {
                    argv.push(text(element, "element")?.to_string());
                }
                let appended = render(agent_key, &words, grammar)?;
                argv.last_mut().ok_or_else(|| ComposeError::Compose("no program".to_string()))?.push_str(&appended);
                finish(argv)
            }
            _ => Err(shape("direct needs a form of string or list")),
        },
        Some("credential") => compose_credential(agent_key, object),
        Some("argv") => Err(shape("argv is not composed by the sidecar")),
        _ => Err(shape("unknown or missing shape")),
    }
}

/// May `agent_key` use `shape` on this platform? Windows follows the same declaration as POSIX, but
/// its structured shape is `direct` only: `wrapped` and `credential` are POSIX shell text. A
/// Rust-composed subject on POSIX never takes `argv`.
pub fn admit(agent_key: &str, shape: &str, windows: bool) -> Result<(), ComposeError> {
    let composition = match vendor_facts::find(agent_key) {
        Some(facts) => facts.composition,
        None if vendor_facts::SUBJECT_ALIASES.contains(&agent_key) => vendor_facts::SUBJECT_COMPOSITION,
        // Harness build only: a subject the declarations do not know (the graceful-shutdown test
        // declaration of adapters.rs) is composed in Rust, so fault tests can use test subjects.
        #[cfg(feature = "fault-hooks")]
        None => vendor_facts::SUBJECT_COMPOSITION,
        #[cfg(not(feature = "fault-hooks"))]
        None => return Err(ComposeError::Composition(format!("unknown subject {agent_key:?}"))),
    };
    let allowed = match composition {
        // Windows: the managed-credential helper form has no Rust wrapper yet (it is composed from a
        // resolved shim by `credential_launch.wrap_command`), so that launch keeps the `argv` shape.
        vendor_facts::Composition::Rust if windows => matches!(shape, "direct" | "argv"),
        vendor_facts::Composition::Rust => {
            matches!(shape, "wrapped" | "direct") || (shape == "credential" && vendor_facts::find(agent_key).is_some())
        }
        vendor_facts::Composition::Python => shape == "argv",
    };
    if allowed {
        Ok(())
    } else {
        Err(ComposeError::Composition(format!("shape {shape:?} is not admitted for {agent_key:?} here")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const QUOTE_VECTORS: &str = include_str!("../../../backend/tests/data/g2_quote_vectors.json");

    fn composed(agent_key: &str, launch: Value) -> Composed {
        compose(agent_key, &launch).unwrap_or_else(|error| panic!("{error:?}"))
    }

    fn wrapped(words: Value) -> Value {
        json!({"shape": "wrapped", "shell": "/bin/bash", "flags": ["-ilc"], "words": words})
    }

    #[test]
    fn wrapped_renders_lit_verbatim_and_args_as_quoted_words() {
        let result = composed(
            "claude",
            wrapped(json!([
                {"lit": "claude"},
                {"arg": "--mcp-config"}, {"arg": "/p a/c.json"},
                {"lit": "  --keep  'two  spaces' \\ x"},
            ])),
        );
        assert_eq!(result.program, "/bin/bash");
        assert_eq!(
            result.args,
            ["-ilc", "claude --mcp-config '/p a/c.json'  --keep  'two  spaces' \\ x"]
        );
        assert_eq!(result.argv_source, "rust");
        assert_eq!(
            result.argv0,
            Argv0 { len: 9, head: "/bin/bash".into(), tail: "/bin/bash".into() }
        );
    }

    #[test]
    fn quoting_matches_the_shared_shlex_quote_vectors() {
        let file: Value = serde_json::from_str(QUOTE_VECTORS).unwrap();
        for vector in file["vectors"].as_array().unwrap() {
            let input = vector["input"].as_str().unwrap();
            let result = composed("claude", wrapped(json!([{"lit": "x"}, {"arg": input}])));
            let expected = format!("x {}", vector["quoted"].as_str().unwrap());
            assert_eq!(result.args[1], expected, "input {input:?}");
        }
    }

    const WINDOWS_VECTORS: &str = include_str!("../../../backend/tests/data/g2_windows_vectors.json");

    #[test]
    fn windows_quoting_matches_the_shared_list2cmdline_vectors() {
        let file: Value = serde_json::from_str(WINDOWS_VECTORS).unwrap();
        for vector in file["quote"].as_array().unwrap() {
            let input = vector["input"].as_str().unwrap();
            assert_eq!(quote_windows(input), vector["quoted"].as_str().unwrap(), "input {input:?}");
        }
    }

    #[test]
    fn windows_splitting_matches_the_shared_command_line_vectors() {
        let file: Value = serde_json::from_str(WINDOWS_VECTORS).unwrap();
        for vector in file["split"].as_array().unwrap() {
            let line = vector["line"].as_str().unwrap();
            let expected: Vec<String> =
                vector["words"].as_array().unwrap().iter().map(|w| w.as_str().unwrap().to_string()).collect();
            assert_eq!(split_windows(line), expected, "line {line:?}");
        }
    }

    #[test]
    fn a_windows_direct_string_with_nested_quotes_and_a_spaced_path_composes_to_the_same_argv() {
        let program = "C:\\Program Files\\x\\prog.cmd";
        let words = json!([
            {"lit": quote_windows(program)},
            {"opt": "model", "value": "say \"hi\" \\"},
            {"arg": "C:\\a b\\\\"},
            {"arg": "x\\\"y"}
        ]);
        let launch = json!({"shape": "direct", "form": "string", "words": words});
        let result = compose_windows("claude", &launch).unwrap();
        assert_eq!(result.program, program);
        assert_eq!(result.args, ["--model", "say \"hi\" \\", "C:\\a b\\\\", "x\\\"y"]);
        assert_eq!(result.argv_source, "rust");
        assert!(matches!(compose_windows("claude", &wrapped(json!([]))), Err(ComposeError::Composition(_))));
    }

    #[test]
    fn a_windows_direct_list_appends_its_suffix_to_the_last_element() {
        let launch = json!({"shape": "direct", "form": "list",
            "elements": ["pwsh.exe", "-NoLogo", "-Command", "& 'C:\\x\\prog.cmd'"], "words": [{"arg": "a b"}]});
        let result = compose_windows("claude", &launch).unwrap();
        assert_eq!(result.program, "pwsh.exe");
        assert_eq!(result.args, ["-NoLogo", "-Command", "& 'C:\\x\\prog.cmd' \"a b\""]);
    }

    #[test]
    fn splitting_matches_the_shared_shlex_split_vectors() {
        let file: Value =
            serde_json::from_str(include_str!("../../../backend/tests/data/g2_split_vectors.json")).unwrap();
        for vector in file["vectors"].as_array().unwrap() {
            let input = vector["input"].as_str().unwrap();
            let result = split(input);
            if vector.get("error").is_some() {
                assert!(result.is_err(), "input {input:?}");
            } else {
                let expected: Vec<String> =
                    vector["words"].as_array().unwrap().iter().map(|w| w.as_str().unwrap().to_string()).collect();
                assert_eq!(result.unwrap(), expected, "input {input:?}");
            }
        }
    }

    #[test]
    fn declared_options_are_spelled_from_the_facts_in_place() {
        let result = composed(
            "claude",
            wrapped(json!([
                {"lit": "claude"},
                {"opt": "model", "value": "fixture-model"},
                {"opt": "effort", "value": "high"},
                {"opt": "skip_permissions"},
                {"opt": "resume", "value": "abc-123"},
                {"opt": "session_id", "value": "sid 1"},
                {"lit": " --keep"},
            ])),
        );
        assert_eq!(
            result.args[1],
            "claude --model fixture-model --effort high --dangerously-skip-permissions \
             --resume abc-123 --session-id 'sid 1' --keep"
        );
    }

    /// The tokenizer keeps `--effort extreme` literal (delta 2.3), so the launch that works today
    /// still reaches the CLI; an `opt` outside `known_efforts` is refused, never rendered.
    #[test]
    fn a_handwritten_effort_stays_literal_and_an_unknown_effort_opt_is_refused() {
        let result = composed(
            "claude",
            wrapped(json!([{"lit": "claude --effort extreme --dangerously-skip-permissions"}])),
        );
        assert_eq!(result.args[1], "claude --effort extreme --dangerously-skip-permissions");
        let refused = compose(
            "claude",
            &wrapped(json!([{"lit": "claude"}, {"opt": "effort", "value": "extreme"}])),
        )
        .unwrap_err();
        assert!(matches!(refused, ComposeError::Option(_)));
    }

    #[test]
    fn the_attestation_is_the_bounded_cut_of_a_path_over_256_bytes() {
        let ascii = format!("/{}/bash", "a".repeat(299));
        let result = composed(
            "claude",
            json!({"shape": "wrapped", "shell": ascii, "flags": ["-lc"], "words": [{"lit": "x"}]}),
        );
        assert_eq!(result.program, ascii);
        assert_eq!(result.argv0.len, ascii.len());
        assert_eq!(result.argv0.head, &ascii[..128]);
        assert_eq!(result.argv0.tail, &ascii[ascii.len() - 128..]);
        // 'é' is two bytes: the cuts fall on character boundaries.
        let multibyte = format!("/{}/bash", "é".repeat(150));
        let result = composed(
            "claude",
            json!({"shape": "wrapped", "shell": multibyte, "flags": ["-lc"], "words": [{"lit": "x"}]}),
        );
        assert_eq!(result.argv0.len, multibyte.len());
        assert_eq!(result.argv0.head, format!("/{}", "é".repeat(63)));
        assert_eq!(result.argv0.tail, format!("{}/bash", "é".repeat(61)));
    }

    #[test]
    fn login_option_is_spelled_from_login_args_and_exe_replaces_the_head() {
        let result = composed(
            "claude",
            wrapped(json!([{"exe": "/opt/my claude/bin/claude"}, {"opt": "login"}])),
        );
        assert_eq!(result.args[1], "'/opt/my claude/bin/claude' auth login");
    }

    #[test]
    fn direct_string_splits_with_the_shlex_grammar() {
        let result = composed(
            "claude",
            json!({"shape": "direct", "form": "string", "words": [
                {"lit": "claude --name \"a b\" 'c d'"}, {"arg": "--model"}, {"arg": "x y"}]}),
        );
        assert_eq!(result.program, "claude");
        assert_eq!(result.args, ["--name", "a b", "c d", "--model", "x y"]);
        assert_eq!(result.argv0.len, 6);
        assert_eq!(result.argv0.head, "claude");
    }

    /// Known behaviour carried over (delta 2.2): the rendered words land in the last element.
    #[test]
    fn direct_list_keeps_the_flag_append_to_the_last_element() {
        let result = composed(
            "claude",
            json!({"shape": "direct", "form": "list", "elements": ["claude"],
                   "words": [{"arg": "--model"}, {"arg": "x"}]}),
        );
        assert_eq!(result.program, "claude --model x");
        assert!(result.args.is_empty());
    }

    #[test]
    fn malformed_launches_are_refused_with_the_documented_stage() {
        let bad = |launch: Value| compose("claude", &launch).unwrap_err();
        assert!(matches!(bad(json!({"shape": "teleport"})), ComposeError::Shape(_)));
        assert!(matches!(bad(json!({})), ComposeError::Shape(_)));
        assert!(matches!(bad(json!({"argv": ["x"]})), ComposeError::Shape(_)));
        assert!(matches!(
            bad(json!({"shape": "direct", "form": "string", "words": [{"lit": "echo 'open"}]})),
            ComposeError::Compose(_)
        ));
        assert!(matches!(
            bad(json!({"shape": "direct", "form": "string", "words": [{"lit": "   "}]})),
            ComposeError::Compose(_)
        ));
        assert!(matches!(
            bad(wrapped(json!([{"lit": "x"}, {"opt": "teleport", "value": "1"}]))),
            ComposeError::Option(_)
        ));
        assert!(matches!(
            bad(wrapped(json!([{"lit": "x"}, {"arg": "a\u{0}b"}]))),
            ComposeError::Validate(_)
        ));
    }

    #[test]
    fn the_ordinary_shell_has_no_vendor_options() {
        for key in ["", "terminal"] {
            let error = compose(key, &wrapped(json!([{"lit": "x"}, {"opt": "model", "value": "m"}])))
                .unwrap_err();
            assert!(matches!(error, ComposeError::Option(_)), "{key:?}");
        }
    }

    #[test]
    fn admission_follows_the_platform_scoped_composition_table() {
        for subject in ["claude", "", "terminal"] {
            assert_eq!(admit(subject, "wrapped", false), Ok(()), "{subject:?} wrapped on POSIX");
            assert_eq!(admit(subject, "direct", false), Ok(()), "{subject:?} direct on POSIX");
            assert!(matches!(admit(subject, "argv", false), Err(ComposeError::Composition(_))));
            assert_eq!(admit(subject, "direct", true), Ok(()), "{subject:?} direct on Windows");
            assert_eq!(admit(subject, "argv", true), Ok(()), "{subject:?} argv on Windows (managed credential)");
            assert!(matches!(admit(subject, "wrapped", true), Err(ComposeError::Composition(_))));
        }
        // Ticket 05 composes every vendor in Rust: none takes the pre-composed shape on POSIX any more.
        assert!(matches!(admit("aider", "argv", false), Err(ComposeError::Composition(_))));
        assert_eq!(admit("aider", "wrapped", false), Ok(()));
    }

    // ---- G2 ticket 05, delta addendum 10: every vendor composes in Rust; the credential outer form.

    const CREDENTIAL_VECTORS: &str = include_str!("../../../backend/tests/data/g2_credential_wrap_vectors.json");

    fn strings(value: &Value) -> Vec<String> {
        value.as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_string()).collect()
    }

    fn credential(file: &Value, shell: &str, flags: &Value, line: &str) -> Value {
        json!({"shape": "credential", "helper": file["helper"], "unset": file["unset"],
               "inner": {"shape": "wrapped", "shell": shell, "flags": flags, "words": [{"lit": line}]}})
    }

    #[test]
    fn the_credential_script_form_is_the_python_oracle_byte_for_byte() {
        let file: Value = serde_json::from_str(CREDENTIAL_VECTORS).unwrap();
        for case in file["script_cases"].as_array().unwrap() {
            let launch = credential(&file, case["shell"].as_str().unwrap(), &case["flags"], case["line"].as_str().unwrap());
            let result = composed(case["agent_key"].as_str().unwrap(), launch);
            let expected = strings(&case["expected"]);
            assert_eq!(result.program, expected[0], "{}", case["id"]);
            assert_eq!(result.args, expected[1..], "{}", case["id"]);
            assert_eq!(result.argv_source, "rust");
            assert_eq!(result.argv0, attest(&expected[0]), "the attested program is the shell");
        }
    }

    #[test]
    fn the_credential_entrypoint_form_puts_the_resolved_program_after_the_helper() {
        let file: Value = serde_json::from_str(CREDENTIAL_VECTORS).unwrap();
        let launch = json!({"shape": "credential", "helper": file["helper"], "unset": file["unset"],
            "entrypoint": "/x/bin/kilo",
            "inner": {"shape": "direct", "form": "string",
                      "words": [{"lit": "kilo"}, {"arg": "--port"}, {"arg": "4000"}, {"arg": "a b"}]}});
        let result = composed("kilo", launch);
        let helper = strings(&file["helper"]);
        assert_eq!(result.program, helper[0]);
        let mut expected: Vec<String> = helper[1..].to_vec();
        expected.extend(["--entrypoint", "/x/bin/kilo", "--", "/x/bin/kilo", "--port", "4000", "a b"].map(String::from));
        assert_eq!(result.args, expected);
        assert_eq!(result.argv0, attest(&helper[0]), "the attested program is the helper interpreter");
    }

    #[test]
    fn the_credential_form_refuses_what_python_would_not_wrap() {
        let file: Value = serde_json::from_str(CREDENTIAL_VECTORS).unwrap();
        // A valid launch is composed first, so these refusals are not just "unknown shape".
        let good = &file["script_cases"][0];
        composed("kilo", credential(&file, good["shell"].as_str().unwrap(), &good["flags"], good["line"].as_str().unwrap()));
        for case in file["refused_cases"].as_array().unwrap() {
            let launch = credential(&file, case["shell"].as_str().unwrap(), &case["flags"], case["line"].as_str().unwrap());
            assert!(compose("kilo", &launch).is_err(), "{}", case["id"]);
        }
        let line = good["line"].as_str().unwrap();
        let shell = good["shell"].as_str().unwrap();
        // The flag must be the SOLE flag: `-l -c` fails on count alone, apart from the `-x` set vector.
        let two = credential(&file, shell, &json!(["-l", "-c"]), line);
        assert!(matches!(compose("kilo", &two), Err(ComposeError::Credential(_))), "two flags");
        assert!(compose("", &credential(&file, shell, &good["flags"], line)).is_err(), "the ordinary shell has no wrapper");
        assert!(compose("claude", &credential(&file, shell, &good["flags"], line)).is_err(), "the line is not claude's");
        let mut no_helper = credential(&file, shell, &good["flags"], line);
        no_helper["helper"] = json!([]);
        assert!(compose("kilo", &no_helper).is_err());
        let mut stray = credential(&file, shell, &good["flags"], line);
        stray["entrypoint"] = json!("/x/bin/kilo");
        assert!(compose("kilo", &stray).is_err(), "an entrypoint belongs to the direct form only");
        let missing = json!({"shape": "credential", "helper": file["helper"], "unset": file["unset"],
            "inner": {"shape": "direct", "form": "string", "words": [{"lit": "kilo"}]}});
        assert!(compose("kilo", &missing).is_err(), "the direct form needs its entrypoint");
        // NUL in every field the credential shape adds, nested inner included.
        let entry = json!({"shape": "credential", "helper": file["helper"], "unset": file["unset"],
            "entrypoint": "/x/bin/kilo",
            "inner": {"shape": "direct", "form": "string", "words": [{"lit": "kilo"}]}});
        composed("kilo", entry.clone());
        for (path, replacement) in [
            ("helper", json!(["/x/py", "a\u{0}b"])),
            ("unset", json!(["A\u{0}B", "C"])),
            ("entrypoint", json!("/x/bin/ki\u{0}lo")),
        ] {
            let mut nul = entry.clone();
            nul[path] = replacement;
            assert!(matches!(compose("kilo", &nul), Err(ComposeError::Validate(_))), "{path}");
        }
        let mut nul = entry.clone();
        nul["inner"]["words"] = json!([{"lit": "kilo"}, {"arg": "a\u{0}b"}]);
        assert!(matches!(compose("kilo", &nul), Err(ComposeError::Validate(_))), "inner word");
        let mut nul = credential(&file, shell, &good["flags"], line);
        nul["inner"]["shell"] = json!("/bin/ba\u{0}sh");
        assert!(matches!(compose("kilo", &nul), Err(ComposeError::Validate(_))), "inner shell");
    }

    #[test]
    fn a_direct_string_with_glob_or_tilde_characters_is_composed_not_refused() {
        // Only the list/script form declines `~*?[]{}!` (credential_launch.py 42); the entrypoint form of a
        // resolved direct string accepts them, as wrap_command does. Metacharacters and a foreign program
        // are refused in both.
        let file: Value = serde_json::from_str(CREDENTIAL_VECTORS).unwrap();
        let helper = strings(&file["helper"]);
        for case in file["string_cases"].as_array().unwrap() {
            let command = case["command"].as_str().unwrap();
            let launch = json!({"shape": "credential", "helper": file["helper"], "unset": file["unset"],
                "entrypoint": "/x/bin/kilo",
                "inner": {"shape": "direct", "form": "string", "words": [{"lit": command}]}});
            let result = composed(case["agent_key"].as_str().unwrap(), launch);
            let expected: Vec<String> =
                strings(&case["expected"]).iter().map(|item| item.replace("{PROGRAM}", "/x/bin/kilo")).collect();
            assert_eq!(result.program, helper[0], "{}", case["id"]);
            assert_eq!(result.args, expected[1..], "{}", case["id"]);
        }
        for case in file["string_refused_cases"].as_array().unwrap() {
            let launch = json!({"shape": "credential", "helper": file["helper"], "unset": file["unset"],
                "entrypoint": "/x/bin/kilo",
                "inner": {"shape": "direct", "form": "string",
                          "words": [{"lit": case["command"].as_str().unwrap()}]}});
            assert!(compose("kilo", &launch).is_err(), "{}", case["id"]);
        }
    }

    #[test]
    fn every_generated_vendor_composes_in_rust_on_posix_and_takes_only_direct_on_windows() {
        assert!(!vendor_facts::VENDORS.is_empty());
        for facts in vendor_facts::VENDORS.iter() {
            for shape in ["wrapped", "direct", "credential"] {
                assert_eq!(admit(facts.key, shape, false), Ok(()), "{} {shape} on POSIX", facts.key);
            }
            assert_eq!(admit(facts.key, "direct", true), Ok(()), "{} direct on Windows", facts.key);
            for shape in ["wrapped", "credential"] {
                assert!(matches!(admit(facts.key, shape, true), Err(ComposeError::Composition(_))), "{} {shape} on Windows", facts.key);
            }
            assert!(matches!(admit(facts.key, "argv", false), Err(ComposeError::Composition(_))), "{} argv on POSIX", facts.key);
        }
        for alias in ["", "terminal"] {
            assert!(matches!(admit(alias, "credential", false), Err(ComposeError::Composition(_))), "{alias:?} has no wrapper");
        }
    }
}
