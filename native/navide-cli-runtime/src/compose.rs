//! Launch composition (G2 delta to protocol v8b, revision 2): turns the structured `launch` object
//! of a wire-2 CREATE into the program and arguments of the child, and decides which shapes a
//! subject may use.
//!
//! Phase A: the interface and its failing seam tests only. Nothing is implemented yet.
#![allow(dead_code)]
use serde_json::Value;

/// What the sidecar will spawn, and who composed it (the `argv_source` receipt field).
#[derive(Debug, PartialEq, Eq)]
pub struct Composed {
    pub program: String,
    pub args: Vec<String>,
    pub argv_source: &'static str,
    /// Bounded Rust attestation of the executed program (delta section 4).
    pub argv0: Argv0,
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
    NotImplemented,
}

/// Compose a structured launch for `agent_key`.
pub fn compose(_agent_key: &str, _launch: &Value) -> Result<Composed, ComposeError> {
    Err(ComposeError::NotImplemented)
}

/// May `agent_key` use `shape` on this platform? `windows` selects the ticket 04 transition row.
pub fn admit(_agent_key: &str, _shape: &str, _windows: bool) -> Result<(), ComposeError> {
    Err(ComposeError::NotImplemented)
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
            assert_eq!(admit(subject, "argv", true), Ok(()), "{subject:?} argv on Windows");
            assert!(matches!(admit(subject, "wrapped", true), Err(ComposeError::Composition(_))));
        }
        // A vendor that still composes in Python takes only the pre-composed shape.
        assert_eq!(admit("aider", "argv", false), Ok(()));
        assert!(matches!(admit("aider", "wrapped", false), Err(ComposeError::Composition(_))));
    }
}
