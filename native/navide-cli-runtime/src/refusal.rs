//! How the control plane refuses a request.
//!
//! A refusal carries its wire code and stage as values (E1 10.4). Nothing derives a code by reading a message, so
//! a reworded message can never change what Python is told.
use std::borrow::Cow;
use std::fmt;
use std::io;

/// The `code` of an `ERROR` frame.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Code {
    BadRequest,
    StaleSession,
    UnknownSession,
    CreateCancelled,
    /// Spelled as the E1 10.4 table spells it.
    ObserverNotIntegrated,
    OwnershipJournalFailed,
    CleanupFailed,
    SpawnFailed,
    IoFailed,
}

impl Code {
    pub fn wire(self) -> &'static str {
        match self {
            Self::BadRequest => "BAD_REQUEST",
            Self::StaleSession => "STALE_SESSION",
            Self::UnknownSession => "UNKNOWN_SESSION",
            Self::CreateCancelled => "CREATE_CANCELLED",
            Self::ObserverNotIntegrated => "observer not integrated",
            Self::OwnershipJournalFailed => "OWNERSHIP_JOURNAL_FAILED",
            Self::CleanupFailed => "CLEANUP_FAILED",
            Self::SpawnFailed => "SPAWN_FAILED",
            Self::IoFailed => "IO_FAILED",
        }
    }
}

/// The `stage` of an `ERROR` frame. A refusal without one is staged by the request it answers.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Stage {
    HandoffSize,
    LaunchShape,
    Option,
    Compose,
    Validate,
    Composition,
    Cwd,
    Spawn,
    Io,
    Admit,
}

impl Stage {
    pub fn wire(self) -> &'static str {
        match self {
            Self::HandoffSize => "handoff_size",
            Self::LaunchShape => "launch_shape",
            Self::Option => "option",
            Self::Compose => "compose",
            Self::Validate => "validate",
            Self::Composition => "composition",
            Self::Cwd => "cwd",
            Self::Spawn => "spawn",
            Self::Io => "io",
            Self::Admit => "admit",
        }
    }
}

/// A typed refusal. It travels inside an `io::Error` (so `?` keeps working across the platform code) and is
/// recovered by `classify`.
#[derive(Debug, PartialEq, Eq)]
pub struct Refusal {
    pub code: Code,
    pub stage: Option<Stage>,
    message: Cow<'static, str>,
}

impl Refusal {
    pub fn new(code: Code, message: impl Into<Cow<'static, str>>) -> Self {
        Self {
            code,
            stage: None,
            message: message.into(),
        }
    }
    pub fn staged(mut self, stage: Stage) -> Self {
        self.stage = Some(stage);
        self
    }
    pub fn bad(message: impl Into<Cow<'static, str>>) -> Self {
        Self::new(Code::BadRequest, message)
    }
    /// A code whose message is its own name (`STALE_SESSION`, `CREATE_CANCELLED`, ...).
    pub fn named(code: Code) -> Self {
        Self::new(code, code.wire())
    }
    pub fn message(&self) -> &str {
        &self.message
    }
}

impl fmt::Display for Refusal {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str(&self.message)
    }
}
impl std::error::Error for Refusal {}
impl From<Refusal> for io::Error {
    fn from(refusal: Refusal) -> Self {
        io::Error::other(refusal)
    }
}

/// An `io::Error` that carries `refusal`.
pub fn bad_request(stage: Stage, message: impl Into<Cow<'static, str>>) -> io::Error {
    Refusal::bad(message).staged(stage).into()
}
pub fn fault(code: Code, message: impl Into<Cow<'static, str>>) -> io::Error {
    Refusal::new(code, message).into()
}

/// The wire code and stage of a failure. An error that carries no refusal is the platform's own: a failed spawn
/// while creating, a failed I/O otherwise.
pub fn classify(error: &io::Error, creating: bool) -> (Code, Stage) {
    let default_stage = if creating { Stage::Spawn } else { Stage::Io };
    match error.get_ref().and_then(|inner| inner.downcast_ref::<Refusal>()) {
        Some(refusal) => (refusal.code, refusal.stage.unwrap_or(default_stage)),
        None if creating => (Code::SpawnFailed, default_stage),
        None => (Code::IoFailed, default_stage),
    }
}

/// Whether `error` is the refusal `code`.
pub fn is(error: &io::Error, code: Code) -> bool {
    error
        .get_ref()
        .and_then(|inner| inner.downcast_ref::<Refusal>())
        .is_some_and(|refusal| refusal.code == code)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_refusal_keeps_its_code_and_stage_through_an_io_error() {
        let error: io::Error = Refusal::bad("invalid env key").staged(Stage::Validate).into();
        assert_eq!(classify(&error, true), (Code::BadRequest, Stage::Validate));
        assert_eq!(error.to_string(), "invalid env key");
    }

    #[test]
    fn a_refusal_without_a_stage_is_staged_by_the_request() {
        let error: io::Error = Refusal::bad("invalid cols").into();
        assert_eq!(classify(&error, true), (Code::BadRequest, Stage::Spawn));
        assert_eq!(classify(&error, false), (Code::BadRequest, Stage::Io));
    }

    #[test]
    fn a_platform_error_is_a_failed_spawn_while_creating_and_a_failed_io_otherwise() {
        let error = io::Error::other("pipe closed");
        assert_eq!(classify(&error, true), (Code::SpawnFailed, Stage::Spawn));
        assert_eq!(classify(&error, false), (Code::IoFailed, Stage::Io));
    }

    #[test]
    fn a_reworded_message_cannot_change_the_code() {
        // The old classifier read the text: this message would have been `BAD_REQUEST`.
        let error = io::Error::other("invalid because the platform said so");
        assert_eq!(classify(&error, false).0, Code::IoFailed);
        let named: io::Error = Refusal::named(Code::StaleSession).into();
        assert_eq!(named.to_string(), "STALE_SESSION");
        assert_eq!(Code::StaleSession.wire(), "STALE_SESSION");
        assert!(is(&named, Code::StaleSession));
    }
}
