//! Resume: build the arguments that continue a session, and read back which
//! session a saved command resumes (main's `resume_id_from_command`).
use crate::decl::{Cli, Pick, ResumeParse, Tokens, ValueShape};

/// `base.command_text`: the last element of a shell-wrapped argv, or the string.
pub enum Command<'a> {
    Text(&'a str),
    Argv(&'a [String]),
}

impl Command<'_> {
    fn text(&self) -> &str {
        match self {
            Command::Text(t) => t,
            Command::Argv(argv) => argv.last().map_or("", String::as_str),
        }
    }
}

/// The resume arguments for `id` (after the program), or `None` when the CLI
/// cannot resume by id.
pub fn args(cli: &Cli, id: &str) -> Option<Vec<String>> {
    Some(cli.resume.args?.iter().map(|a| a.replace("{id}", id)).collect())
}

/// The session id the command resumes, "" when none.
pub fn id_from_command(cli: &Cli, command: &Command) -> String {
    let Some(parse) = &cli.resume.parse else { return String::new() };
    match parse.tokens {
        Tokens::Whitespace => {
            let tokens: Vec<&str> = command.text().split_whitespace().collect();
            scan(parse, &tokens, false)
        }
        Tokens::Literal => {
            let Some(tokens) = literal_args(command) else { return String::new() };
            let tokens: Vec<&str> = tokens.iter().map(String::as_str).collect();
            scan(parse, &tokens, true)
        }
    }
}

pub(crate) fn shaped(value: &str, shape: ValueShape) -> Option<String> {
    match shape {
        ValueShape::Any => (!value.is_empty()).then(|| value.to_owned()),
        ValueShape::NoDash => (!value.is_empty() && !value.starts_with('-')).then(|| value.to_owned()),
        ValueShape::Uuid => {
            let groups: Vec<&str> = value.split('-').collect();
            let lens = [8, 4, 4, 4, 12];
            let ok = groups.len() == 5
                && groups.iter().zip(lens).all(|(g, n)| g.len() == n && g.bytes().all(|b| b.is_ascii_hexdigit()));
            ok.then(|| value.to_ascii_lowercase())
        }
        ValueShape::Prefix(prefix) => (value.starts_with(prefix) && value.trim() == value).then(|| value.to_owned()),
    }
}

fn scan(parse: &ResumeParse, tokens: &[&str], literal: bool) -> String {
    let Some(first) = tokens.first() else { return String::new() };
    if !parse.programs.contains(first) {
        return String::new();
    }
    let mut from = 1;
    if !parse.subcommand.is_empty() {
        if !tokens.get(1).is_some_and(|t| parse.subcommand.contains(t)) {
            return String::new();
        }
        if parse.id_after_subcommand {
            return tokens.get(2).and_then(|v| shaped(v, parse.value)).unwrap_or_default();
        }
        from = 2;
    }
    let mut found: Vec<String> = Vec::new();
    let mut i = from;
    while i < tokens.len() {
        let token = tokens[i];
        if literal && token == "--" {
            break;
        }
        // `Some(None)`: this occurrence names a session, in the wrong shape.
        let candidate = if parse.flags.contains(&token) && i + 1 < tokens.len() {
            Some(shaped(tokens[i + 1], parse.value))
        } else {
            parse.joined.and_then(|shape| {
                parse.flags.iter().filter(|f| f.starts_with("--")).find_map(|f| {
                    let rest = token.strip_prefix(*f)?.strip_prefix('=')?;
                    Some(shaped(rest, shape))
                })
            })
        };
        if let Some(value) = candidate {
            if literal {
                return value.unwrap_or_default();
            }
            if let Some(value) = value {
                found.push(value);
                if parse.pick == Pick::First {
                    break;
                }
            }
        }
        i += 1;
    }
    match parse.pick {
        Pick::First => found.into_iter().next(),
        Pick::Last => found.pop(),
    }
    .unwrap_or_default()
}

/// `base.simple_command_args`: only a literal invocation, unwrapped from a
/// known shell's `-c`, with no shell metacharacters, split like `shlex`.
fn literal_args(command: &Command) -> Option<Vec<String>> {
    if let Command::Argv(argv) = command {
        if argv.len() < 3 {
            return None;
        }
        let shell = argv[0].replace('\\', "/").rsplit('/').next().unwrap_or_default().to_lowercase();
        let flags: Vec<String> = argv[1..argv.len() - 1].to_vec();
        let lower: Vec<String> = flags.iter().map(|f| f.to_lowercase()).collect();
        let is = |set: &[&[&str]], have: &[String]| set.iter().any(|s| s.len() == have.len() && s.iter().zip(have).all(|(a, b)| a == b));
        let posix = ["sh", "bash", "zsh", "dash", "ksh", "fish"].contains(&shell.as_str()) && is(&[&["-c"], &["-lc"], &["-ilc"]], &flags);
        let windows = ["cmd", "cmd.exe"].contains(&shell.as_str())
            && is(&[&["/c"], &["/k"], &["/d", "/s", "/c"], &["/d", "/c"]], &lower);
        let powershell = ["powershell", "powershell.exe", "pwsh", "pwsh.exe"].contains(&shell.as_str())
            && is(&[&["-command"], &["-nologo", "-noexit", "-command"]], &lower);
        if !(posix || windows || powershell) {
            return None;
        }
    }
    let text = command.text();
    if text.chars().any(|c| ";&|<>`$%#()\n\r".contains(c)) {
        return None;
    }
    shlex_split(text)
}

/// POSIX-mode `shlex.split` for text already free of shell metacharacters.
fn shlex_split(text: &str) -> Option<Vec<String>> {
    let mut out = Vec::new();
    let mut word = String::new();
    let mut in_word = false;
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        match c {
            '\'' => {
                in_word = true;
                loop {
                    match chars.next()? {
                        '\'' => break,
                        other => word.push(other),
                    }
                }
            }
            '"' => {
                in_word = true;
                loop {
                    match chars.next()? {
                        '"' => break,
                        '\\' => match chars.next()? {
                            escaped @ ('\\' | '"') => word.push(escaped),
                            other => {
                                word.push('\\');
                                word.push(other);
                            }
                        },
                        other => word.push(other),
                    }
                }
            }
            '\\' => {
                in_word = true;
                word.push(chars.next()?);
            }
            c if c.is_whitespace() => {
                if in_word {
                    out.push(std::mem::take(&mut word));
                    in_word = false;
                }
            }
            other => {
                in_word = true;
                word.push(other);
            }
        }
    }
    if in_word {
        out.push(word);
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::shlex_split;

    #[test]
    fn shlex_matches_posix_mode() {
        assert_eq!(shlex_split("a 'b c' \"d \\\" e\" f\\ g").unwrap(), ["a", "b c", "d \" e", "f g"]);
        assert!(shlex_split("a 'b").is_none());
        assert_eq!(shlex_split("  ").unwrap(), Vec::<String>::new());
    }
}
