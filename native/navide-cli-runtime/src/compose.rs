//! Launch composition (G2 delta to protocol v8b, section 2): turns the structured `launch` object of
//! a wire-2 CREATE into the program and arguments of the child.
//!
//! Phase A: the interface and its failing seam tests only. `compose` is not implemented yet.
#![allow(dead_code)]
use serde_json::Value;

/// What the sidecar will spawn, and who composed it (the `argv_source` receipt field).
#[derive(Debug, PartialEq, Eq)]
pub struct Composed {
    pub program: String,
    pub args: Vec<String>,
    pub argv_source: &'static str,
}

#[derive(Debug, PartialEq, Eq)]
pub enum ComposeError {
    /// Unknown, missing or doubled `shape`, or a malformed field.
    Shape(String),
    /// `direct/text` that does not split (an unterminated quote).
    Parse(String),
    /// The composition produced no program.
    Empty,
    NotImplemented,
}

pub fn compose(_launch: &Value) -> Result<Composed, ComposeError> {
    Err(ComposeError::NotImplemented)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn composed(launch: Value) -> Composed {
        compose(&launch).unwrap_or_else(|error| panic!("{error:?}"))
    }

    #[test]
    fn wrapped_appends_each_option_as_quoted_words_to_the_line() {
        let result = composed(json!({
            "shape": "wrapped", "shell": "/bin/bash", "flags": ["-ilc"], "line": "claude",
            "injected": [{"id": "model", "args": ["--model", "fixture-model"]},
                         {"id": "effort", "args": ["--effort", "high"]}],
        }));
        assert_eq!(result.program, "/bin/bash");
        assert_eq!(result.args, ["-ilc", "claude --model fixture-model --effort high"]);
        assert_eq!(result.argv_source, "rust");
    }

    #[test]
    fn wrapped_quotes_nested_json_and_metacharacters_like_shlex_quote() {
        let settings = r#"{"a":"it's \"x\" ; | &"}"#;
        let result = composed(json!({
            "shape": "wrapped", "shell": "/bin/zsh", "flags": ["-lc"], "line": "claude --keep",
            "injected": [{"id": "settings", "args": ["--settings", settings, "", "café", "a b"]}],
        }));
        let expected = format!(
            "claude --keep --settings '{{\"a\":\"it'\"'\"'s \\\"x\\\" ; | &\"}}' '' 'café' 'a b'"
        );
        assert_eq!(result.args, ["-lc", expected.as_str()]);
    }

    #[test]
    fn wrapped_without_options_passes_the_line_untouched() {
        let result = composed(json!({
            "shape": "wrapped", "shell": "/bin/bash", "flags": ["-lc"], "line": "echo  'two  spaces' \\ x",
            "injected": [],
        }));
        assert_eq!(result.args, ["-lc", "echo  'two  spaces' \\ x"]);
    }

    #[test]
    fn direct_text_splits_with_the_shlex_grammar_after_the_append() {
        let result = composed(json!({
            "shape": "direct", "text": "claude --name \"a b\" 'c d'",
            "injected": [{"id": "model", "args": ["--model", "x y"]}],
        }));
        assert_eq!(result.program, "claude");
        assert_eq!(result.args, ["--name", "a b", "c d", "--model", "x y"]);
        assert_eq!(result.argv_source, "rust");
    }

    /// Known behaviour carried over (delta 2.6): flags land in the last element's text.
    #[test]
    fn direct_elements_keep_the_flag_append_to_the_last_element() {
        let result = composed(json!({
            "shape": "direct", "elements": ["claude"],
            "injected": [{"id": "model", "args": ["--model", "x"]}],
        }));
        assert_eq!(result.program, "claude --model x");
        assert!(result.args.is_empty());
    }

    #[test]
    fn malformed_launches_are_refused_with_a_reason_and_no_composition() {
        assert!(matches!(compose(&json!({"shape": "teleport"})), Err(ComposeError::Shape(_))));
        assert!(matches!(compose(&json!({})), Err(ComposeError::Shape(_))));
        assert!(matches!(
            compose(&json!({"shape": "direct", "text": "echo 'open"})),
            Err(ComposeError::Parse(_))
        ));
        assert_eq!(
            compose(&json!({"shape": "direct", "text": "   "})),
            Err(ComposeError::Empty)
        );
        assert!(matches!(
            compose(&json!({"shape": "wrapped", "shell": "/bin/bash", "flags": [1], "line": "x"})),
            Err(ComposeError::Shape(_))
        ));
    }
}
