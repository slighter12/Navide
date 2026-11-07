// Generated from shared/agent-cli/*.json by scripts/generate-agent-cli-facts.py; do not edit.
//! The observer roster: which parser reads which vendor's logs, in which layout, answering which modes.
use super::{observe, Request};
use serde_json::Value;
use std::io;
use std::path::Path;

pub mod aider;
pub mod antigravity;
pub mod claude;
pub mod codex;
pub mod copilot;
pub mod cursor;
pub mod droid;
pub mod goose;
pub mod grok;
pub mod opencode;
pub mod kimi;
pub mod muse;
pub mod pi;
pub mod qwen;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Parser {
    Aider,
    Antigravity,
    Claude,
    Codex,
    Copilot,
    Cursor,
    Droid,
    Goose,
    Grok,
    Opencode,
    Kimi,
    Muse,
    Pi,
    Qwen,
}

/// The identity of a declared mark walk: its parser and the parser's own walk.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Walk {
    Antigravity(AntigravityWalk),
    Copilot(CopilotWalk),
    Cursor(CursorWalk),
    Goose(GooseWalk),
    Opencode(OpencodeWalk),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AntigravityWalk {
    Steps,
}

/// A mark of one of antigravity's walks, typed by the walk's own columns: its cells are reached by a column of that
/// walk and no other (a column of another walk or parser does not compile).
#[allow(dead_code)]
pub enum AntigravityMark<'a> {
    Steps(super::WalkMark<'a, AntigravityStepsColumn>),
}

impl std::ops::Deref for AntigravityMark<'_> {
    type Target = super::Mark;

    fn deref(&self) -> &super::Mark {
        match self {
            AntigravityMark::Steps(mark) => mark,
        }
    }
}

impl super::WalkId for AntigravityWalk {
    type Marked<'a> = AntigravityMark<'a>;

    fn of(walk: Walk) -> Option<Self> {
        match walk {
            Walk::Antigravity(own) => Some(own),
            #[allow(unreachable_patterns)]
            _ => None,
        }
    }

    fn mark<'a>(self, mark: &'a super::Mark) -> AntigravityMark<'a> {
        match self {
            AntigravityWalk::Steps => AntigravityMark::Steps(super::WalkMark::new(mark)),
        }
    }
}

/// The cells of antigravity's `steps` walk, in the order the core hands them to an adapter. A declared cell
/// the adapter does not read is still a cell of the walk (and a position of the ones after it).
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AntigravityStepsColumn {
    StepType,
    Status,
    Metadata,
}

impl super::ColumnId for AntigravityStepsColumn {
    fn position(self) -> usize {
        self as usize
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CopilotWalk {
    AssistantUsageEvents,
    Turns,
}

/// A mark of one of copilot's walks, typed by the walk's own columns: its cells are reached by a column of that
/// walk and no other (a column of another walk or parser does not compile).
#[allow(dead_code)]
pub enum CopilotMark<'a> {
    AssistantUsageEvents(super::WalkMark<'a, CopilotAssistantUsageEventsColumn>),
    Turns(super::WalkMark<'a, CopilotTurnsColumn>),
}

impl std::ops::Deref for CopilotMark<'_> {
    type Target = super::Mark;

    fn deref(&self) -> &super::Mark {
        match self {
            CopilotMark::AssistantUsageEvents(mark) => mark,
            CopilotMark::Turns(mark) => mark,
        }
    }
}

impl super::WalkId for CopilotWalk {
    type Marked<'a> = CopilotMark<'a>;

    fn of(walk: Walk) -> Option<Self> {
        match walk {
            Walk::Copilot(own) => Some(own),
            #[allow(unreachable_patterns)]
            _ => None,
        }
    }

    fn mark<'a>(self, mark: &'a super::Mark) -> CopilotMark<'a> {
        match self {
            CopilotWalk::AssistantUsageEvents => CopilotMark::AssistantUsageEvents(super::WalkMark::new(mark)),
            CopilotWalk::Turns => CopilotMark::Turns(super::WalkMark::new(mark)),
        }
    }
}

/// The cells of copilot's `assistant_usage_events` walk, in the order the core hands them to an adapter. A declared cell
/// the adapter does not read is still a cell of the walk (and a position of the ones after it).
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CopilotAssistantUsageEventsColumn {
    CreatedAt,
}

impl super::ColumnId for CopilotAssistantUsageEventsColumn {
    fn position(self) -> usize {
        self as usize
    }
}

/// The cells of copilot's `turns` walk, in the order the core hands them to an adapter. A declared cell
/// the adapter does not read is still a cell of the walk (and a position of the ones after it).
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CopilotTurnsColumn {
    UserMessage,
    AssistantResponse,
    Timestamp,
}

impl super::ColumnId for CopilotTurnsColumn {
    fn position(self) -> usize {
        self as usize
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CursorWalk {
    Blobs,
}

/// A mark of one of cursor's walks, typed by the walk's own columns: its cells are reached by a column of that
/// walk and no other (a column of another walk or parser does not compile).
#[allow(dead_code)]
pub enum CursorMark<'a> {
    Blobs(super::WalkMark<'a, super::NoColumn>),
}

impl std::ops::Deref for CursorMark<'_> {
    type Target = super::Mark;

    fn deref(&self) -> &super::Mark {
        match self {
            CursorMark::Blobs(mark) => mark,
        }
    }
}

impl super::WalkId for CursorWalk {
    type Marked<'a> = CursorMark<'a>;

    fn of(walk: Walk) -> Option<Self> {
        match walk {
            Walk::Cursor(own) => Some(own),
            #[allow(unreachable_patterns)]
            _ => None,
        }
    }

    fn mark<'a>(self, mark: &'a super::Mark) -> CursorMark<'a> {
        match self {
            CursorWalk::Blobs => CursorMark::Blobs(super::WalkMark::new(mark)),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GooseWalk {
    Messages,
}

/// A mark of one of goose's walks, typed by the walk's own columns: its cells are reached by a column of that
/// walk and no other (a column of another walk or parser does not compile).
#[allow(dead_code)]
pub enum GooseMark<'a> {
    Messages(super::WalkMark<'a, GooseMessagesColumn>),
}

impl std::ops::Deref for GooseMark<'_> {
    type Target = super::Mark;

    fn deref(&self) -> &super::Mark {
        match self {
            GooseMark::Messages(mark) => mark,
        }
    }
}

impl super::WalkId for GooseWalk {
    type Marked<'a> = GooseMark<'a>;

    fn of(walk: Walk) -> Option<Self> {
        match walk {
            Walk::Goose(own) => Some(own),
            #[allow(unreachable_patterns)]
            _ => None,
        }
    }

    fn mark<'a>(self, mark: &'a super::Mark) -> GooseMark<'a> {
        match self {
            GooseWalk::Messages => GooseMark::Messages(super::WalkMark::new(mark)),
        }
    }
}

/// The cells of goose's `messages` walk, in the order the core hands them to an adapter. A declared cell
/// the adapter does not read is still a cell of the walk (and a position of the ones after it).
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GooseMessagesColumn {
    Role,
    MetadataJson,
    SessionType,
}

impl super::ColumnId for GooseMessagesColumn {
    fn position(self) -> usize {
        self as usize
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OpencodeWalk {
    SessionMessage,
    Part,
}

/// A mark of one of opencode's walks, typed by the walk's own columns: its cells are reached by a column of that
/// walk and no other (a column of another walk or parser does not compile).
#[allow(dead_code)]
pub enum OpencodeMark<'a> {
    SessionMessage(super::WalkMark<'a, OpencodeSessionMessageColumn>),
    Part(super::WalkMark<'a, super::NoColumn>),
}

impl std::ops::Deref for OpencodeMark<'_> {
    type Target = super::Mark;

    fn deref(&self) -> &super::Mark {
        match self {
            OpencodeMark::SessionMessage(mark) => mark,
            OpencodeMark::Part(mark) => mark,
        }
    }
}

impl super::WalkId for OpencodeWalk {
    type Marked<'a> = OpencodeMark<'a>;

    fn of(walk: Walk) -> Option<Self> {
        match walk {
            Walk::Opencode(own) => Some(own),
            #[allow(unreachable_patterns)]
            _ => None,
        }
    }

    fn mark<'a>(self, mark: &'a super::Mark) -> OpencodeMark<'a> {
        match self {
            OpencodeWalk::SessionMessage => OpencodeMark::SessionMessage(super::WalkMark::new(mark)),
            OpencodeWalk::Part => OpencodeMark::Part(super::WalkMark::new(mark)),
        }
    }
}

/// The cells of opencode's `session_message` walk, in the order the core hands them to an adapter. A declared cell
/// the adapter does not read is still a cell of the walk (and a position of the ones after it).
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OpencodeSessionMessageColumn {
    Type,
    Seq,
}

impl super::ColumnId for OpencodeSessionMessageColumn {
    fn position(self) -> usize {
        self as usize
    }
}

// Declaration data: a source kind no vendor on the core declares yet (a sidecar) is still part of the vocabulary the
// declarations are checked against.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SourceKind {
    Jsonl,
    Sqlite,
    Text,
    Sidecar,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UsageMode {
    PerTurn,
    Cumulative,
    None,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Platform {
    Unix,
    Windows,
}

pub const CURRENT: Platform = if cfg!(unix) { Platform::Unix } else { Platform::Windows };

/// The request key a `has_session` lookup names its session in. Never an ownership key: the sidecar reads `session_id`,
/// `session_generation`, `pane_id` and `workspace_path` as who owns the observation.
pub const WANTED_SESSION: &str = "wanted_session";

/// What a request asks of an observer. A parser answers the ones its declaration lists and refuses the rest.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Usage,
    SessionUsage,
    Legacy,
    Activity,
    Turns,
    Identify,
    HasSession,
}

impl Mode {
    /// The one decode of the wire's mode text.
    pub fn parse(text: &str) -> Option<Self> {
        Some(match text {
            "usage" => Self::Usage,
            "session_usage" => Self::SessionUsage,
            "legacy" => Self::Legacy,
            "activity" => Self::Activity,
            "turns" => Self::Turns,
            "identify" => Self::Identify,
            "has_session" => Self::HasSession,
            _ => return None,
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Usage => "usage",
            Self::SessionUsage => "session_usage",
            Self::Legacy => "legacy",
            Self::Activity => "activity",
            Self::Turns => "turns",
            Self::Identify => "identify",
            Self::HasSession => "has_session",
        }
    }
}

/// How a log spells a number: `Lenient` takes a bool, a float or a numeric string; `Strict` an integer or an
/// integer string. A named compatibility difference between the baseline readers.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NumberRule {
    Lenient,
    Strict,
}

/// How a log spells a text field: `Any` renders any value as text; `String` takes strings only.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TextRule {
    Any,
    String,
}

/// Whether the cache portions a log splits out are part of the input an event reports (`Folded`, with the event naming
/// them beside it) or the log's input already excludes them and the event reports them next to it (`Separate`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CacheRule {
    Folded,
    Separate,
}

/// What a timestamp pointer holds: the text to report, or epoch seconds (or microseconds) to report as an ISO 8601 UTC time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimeRule {
    Text,
    EpochSeconds,
    EpochMicros,
}

/// How rows are joined to the parent (session) table: `Inner` drops a row with no parent, `Left` keeps it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum JoinPolicy {
    Inner,
    Left,
}

/// Where a first activity pass of a mark walk starts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MarkStart {
    /// Past the newest mark; an empty table leaves the walk unstarted.
    Newest,
    /// Past the newest mark, or 0 for an empty table.
    NewestOrZero,
    /// At the oldest mark: the table's history is delivered.
    Oldest,
}

/// What a mark walk does when its table has fewer marks than the last mark it delivered.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MarkShrink {
    Keep,
    /// Restart past the newest mark and deliver nothing this pass.
    Newest,
    /// Restart at the oldest mark.
    Oldest,
}

/// A record is selected when the value at `pointer` (RFC 6901) is the string `equals`.
#[derive(Clone, Copy, Debug)]
pub struct Select {
    pub pointer: &'static str,
    pub equals: &'static str,
}

/// A row is selected when the column holds the text `equals`.
#[derive(Clone, Copy, Debug)]
pub struct ColumnIs {
    pub column: &'static str,
    pub equals: &'static str,
}

/// A parent group is top-level when its `column` holds the text `equals`. `at` is the column's position in the parent's `columns`,
/// worked out when the facts are generated (a column the parent does not select is refused there), so the core never looks a
/// column up by its name.
#[derive(Clone, Copy, Debug)]
pub struct TopIs {
    pub column: &'static str,
    pub at: usize,
    pub equals: &'static str,
}

/// The unit of an epoch timestamp (a declaration picks one; the others are the vocabulary).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimeUnit {
    Seconds,
    Millis,
}

/// A pending row (or mark) the vendor never finishes is dropped once its own timestamp is `seconds` older than the newest
/// timestamp the pass read in the store (a store's own clock, never the machine's). A row reads the timestamp at `pointer`
/// in its payload; a mark is aged by the time its adapter reads from it (`Progress::stamp`, epoch seconds).
#[derive(Clone, Copy, Debug)]
pub struct PendingAge {
    pub pointer: Option<&'static str>,
    pub unit: TimeUnit,
    pub seconds: u64,
}

/// What a request carries for a store file that the store does not: the working directory and session the discovery
/// reader resolved for it, and the scan clock.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RequestField {
    Cwd,
    Session,
    BaseEpoch,
    CliVersion,
    NowMs,
    Namespace,
}

/// Where a found marker's session comes from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Binding {
    /// The group (and working directory) of the row that holds the marker.
    StoreRows,
    /// The session the request named for this store file: the store holds one session, and a raw scan finds no group.
    PathSession,
}

/// Where a JSON record keeps its usage: pointers only, no expressions. For a row store with no payload column the record
/// is the row's declared columns, each a key of the record.
#[derive(Clone, Copy, Debug)]
pub struct UsageFields {
    pub select: &'static [Select],
    pub object: Option<&'static str>,
    pub input: &'static [&'static str],
    pub output: &'static [&'static str],
    pub cache_read: &'static [&'static str],
    pub cache_creation: &'static [&'static str],
    pub key: &'static [&'static str],
    pub key_join: &'static str,
    pub model: Option<&'static str>,
    pub version: Option<&'static str>,
    pub timestamp: Option<&'static str>,
    pub number: NumberRule,
    pub text: TextRule,
    pub cache: CacheRule,
    pub time: TimeRule,
    /// A file name suffix: the usage is the totals of the JSON document `<log stem><suffix>` beside the log, not of the log's records.
    pub document: Option<&'static str>,
    /// The working directory a usage record names for itself (the request's, where the record names none).
    pub cwd: Option<&'static str>,
    /// The session a usage record names for itself (the request's, where the record names none).
    pub session: Option<&'static str>,
}

/// The table of rows a usage scan walks (a message or ledger table): id, key, owning group, and what a row holds: a JSON
/// payload, declared scalar `columns`, or both. `anchor` names the columns that tell a reused row id from the row the cursor
/// saw; `pending_until` is a payload pointer whose number is 0 while the vendor has not finished the row.
#[derive(Clone, Copy, Debug)]
pub struct Rows {
    pub table: &'static str,
    pub id: &'static str,
    pub key: &'static str,
    pub group: Option<&'static str>,
    pub payload: Option<&'static str>,
    pub columns: &'static [&'static str],
    pub anchor: &'static [&'static str],
    pub pending_until: Option<&'static str>,
    pub pending_max_age: Option<PendingAge>,
    /// A cursor of this layout is tagged `schema: <tag>`; a saved cursor without the tag was taken on the file's other
    /// schema, so the layout starts at the newest row instead of crediting the copies of rows it already saw.
    pub cursor_tag: Option<&'static str>,
    /// A usage event carries the id of its row as `raw: {<raw_id>: <id>}`, the field the baseline's reader fills (and reads back).
    pub raw_id: Option<&'static str>,
}

/// How a store spells a group's working directory.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DirForm {
    /// As stored.
    AsStored,
    /// A Windows path the store spells with forward slashes, in the platform's own spelling (a no-op off Windows).
    WindowsNative,
}

/// The table a row's group belongs to (a session table). `top` is an extra condition for a top-level group.
#[derive(Clone, Copy, Debug)]
pub struct Parent {
    pub table: &'static str,
    pub key: &'static str,
    pub dir: &'static str,
    pub up: Option<&'static str>,
    pub version: Option<&'static str>,
    pub columns: &'static [&'static str],
    pub join: JoinPolicy,
    pub top: Option<TopIs>,
    pub dir_form: DirForm,
}

/// What names a mark's dedup key beyond the walk's prefix.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KeyScope {
    /// Nothing: the prefix and the mark's key.
    None,
    /// The session the request names for the store file: the prefix, the session, `:` and the mark's key.
    Session,
}

/// Where a turn-ending mark finds its reply: the payload of the newest row of the mark's own group whose `matching.column`
/// holds `matching.equals`, among those with `order` below the mark's own cell at position `before`.
#[derive(Clone, Copy, Debug)]
pub struct ReplyFrom {
    pub order: &'static str,
    pub matching: ColumnIs,
    /// The position of the walk's column whose value bounds the reply (the walk's own cells come first).
    pub before: usize,
}

/// A table an activity scan walks: parts that belong to a row (`owner`) and a group, or the rows of one table
/// themselves (no owner, and no group when the table has no parent). `columns` are extra cells for the adapter. `page`
/// is how many new marks one pass reads, and `text_cap` the length a reply's text is cut to (head and tail). A pass answers
/// one observation document of at most 8 MiB, so a vendor declares the `page` its reach needs and tests its largest first
/// pass; a walk also stops at the answer ledger (`observe/answer.rs`, shared by every walk), whatever its page says.
#[derive(Clone, Copy, Debug)]
pub struct Marks {
    pub table: &'static str,
    pub id: &'static str,
    pub key: Option<&'static str>,
    pub group: Option<&'static str>,
    pub owner: Option<&'static str>,
    pub payload: Option<&'static str>,
    pub stamp: Option<&'static str>,
    pub columns: &'static [&'static str],
    pub prefix: &'static str,
    pub key_scope: KeyScope,
    pub page: usize,
    pub text_cap: Option<usize>,
    pub start: MarkStart,
    pub shrink: MarkShrink,
    pub pending_max_age: Option<PendingAge>,
    pub reply_from: Option<ReplyFrom>,
    /// This walk's typed identity (generated from the declaration, so the adapter is handed its parser's own walk variant and
    /// never matches a position or a table name).
    pub walk: Walk,
    /// An earlier walk of the store this one waits for: while that walk's page came back full (more of its marks remain)
    /// this walk reads nothing, so what it delivers never lands before the earlier walk's last.
    pub after: Option<Walk>,
}

/// Who says a mark is a user's: a pointer in the owner's payload, or a column of the mark.
#[derive(Clone, Copy, Debug)]
pub enum Role {
    Pointer(Select),
    Column(ColumnIs),
}

/// A raw scan bound: the first `rows` rows, the first `bytes` bytes of each payload.
#[derive(Clone, Copy, Debug)]
pub struct Within {
    pub rows: usize,
    pub bytes: usize,
}

/// A pane marker a user message carries; the lookup finds the group that holds it, in the payloads of the walk `on`.
#[derive(Clone, Copy, Debug)]
pub struct Marker {
    pub text: &'static str,
    pub on: &'static str,
    pub role: Option<Role>,
    pub order_by_stamp: bool,
    pub within: Option<Within>,
    pub binding: Binding,
    /// The marker text is searched in the string at this pointer of the mark's payload, not in the whole payload.
    pub pointer: Option<&'static str>,
}

/// A row store: table and column names (validated, quoted identifiers; the core builds fixed query shapes from them).
#[derive(Clone, Copy, Debug)]
pub struct Store {
    /// The table a usage cursor walks (and a mark's owner points into); none for a store read through its marks only.
    pub rows: Option<Rows>,
    pub parent: Option<Parent>,
    pub marks: &'static [Marks],
    pub row_prefix: &'static str,
    pub marker: Option<Marker>,
}

/// The compatibility rules only a JSONL log reads (each keeps one behaviour of a baseline reader).
#[derive(Clone, Copy, Debug)]
pub struct JsonlRules {
    /// Baseline: a saved cursor with no file identity was taken for a different file and read from the start.
    pub missing_identity_rotates: bool,
    /// Baseline: a full scan over invalid UTF-8 failed the pass instead of skipping the line.
    pub invalid_utf8_fails: bool,
    /// Baseline: a full scan over a record that is not an object failed the pass instead of skipping it.
    pub non_object_fails: bool,
    /// Baseline: an activity pass over a record that is not an object failed the pass (progress kept, events dropped) instead of skipping it.
    pub activity_non_object_fails: bool,
    /// Baseline: the checkpoint's cumulative totals are a per-component high-water mark: a reading below them emits nothing and leaves that saved total where it was.
    pub totals_never_decrease: bool,
    /// Baseline: the activity high-water mark outlived a replaced or rewritten file.
    pub water_survives_replacement: bool,
}

/// This layout yields to the store `file`, `up` directories above the log, when that store's parent table already
/// holds the session the log's directory is named for.
#[derive(Clone, Copy, Debug)]
pub struct Defers {
    pub file: &'static str,
    pub up: usize,
}

/// One log layout of a vendor: its source kind, usage mode, file name and the syntax facts of that source.
#[derive(Clone, Copy, Debug)]
pub struct Layout {
    // Declaration data the generator and the tests check; the dispatch is already chosen by them.
    #[allow(dead_code)]
    pub source: SourceKind,
    #[allow(dead_code)]
    pub usage: UsageMode,
    pub store_file: Option<&'static str>,
    /// The tables the store file must all hold for this layout to apply (two schemas in one file); empty for the plain one.
    pub applies_when: &'static [&'static str],
    /// The modes this layout answers: the parser's modes that the layout's source kind can answer.
    pub modes: &'static [Mode],
    pub request: &'static [RequestField],
    pub usage_fields: Option<UsageFields>,
    pub store: Option<Store>,
    pub rules: Option<JsonlRules>,
    pub defers_to: Option<Defers>,
}

#[derive(Clone, Copy, Debug)]
pub struct Messages {
    pub unsupported_mode: &'static str,
    pub non_object: Option<&'static str>,
}

/// Named compatibility rules: each keeps one behaviour of a baseline reader, defect or not. Python reads them.
// Some rules are read only by the Python reader (through the generated Python facts); they are carried here so the two
// stay one declaration.
#[allow(dead_code)]
#[derive(Clone, Copy, Debug)]
pub struct Compat {
    /// Baseline: the baseline reader treated a log that is not there yet as nothing to read.
    pub absent_file_is_empty: bool,
    /// Baseline: the baseline refused a turn breakdown the sidecar does not answer instead of splitting turns from the generic reads.
    pub no_python_turn_fallback: bool,
    /// Baseline: a store query that failed was reported as the answer's error (the reader warns once and a usage pass delivers nothing) instead of refusing the observation.
    pub store_error_reported: bool,
}

/// What the generic core needs beyond the layouts.
#[derive(Clone, Copy, Debug)]
pub struct Core {
    pub messages: Messages,
    pub compat: Compat,
}

#[derive(Clone, Copy, Debug)]
pub struct ObserverFacts {
    pub vendor: &'static str,
    pub parser: Parser,
    pub parser_id: &'static str,
    pub platforms: &'static [Platform],
    pub layouts: &'static [Layout],
    pub core: Core,
}

impl ObserverFacts {
    /// The layouts named for `path`'s file, in declaration order (a layout with no file name takes any path). A store file
    /// that may hold more than one schema has one layout per schema; which one applies is for the observation's own
    /// connection to say (`Source::layout`), so choosing among them never opens the store.
    pub fn candidates<'p>(&'static self, path: &'p Path) -> impl Iterator<Item = &'static Layout> + 'p {
        let name = path.file_name().and_then(|name| name.to_str());
        self.layouts.iter().filter(move |layout| layout.store_file.is_none_or(|file| Some(file) == name))
    }

    /// The slot of the first layout named for `path`'s file; the first layout when none is. A file's layouts share one
    /// source, usage and capability type (the generator refuses any that do not), so this picks the dispatch arm.
    pub fn slot(&self, path: &Path) -> usize {
        let name = path.file_name().and_then(|name| name.to_str());
        self.layouts.iter().position(|layout| layout.store_file.is_none_or(|file| Some(file) == name)).unwrap_or(0)
    }

    pub fn layout_for(&'static self, path: &Path) -> Option<&'static Layout> {
        self.layouts.get(self.slot(path))
    }
}

pub const OBSERVERS: &[ObserverFacts] = &[
    ObserverFacts {
        vendor: "aider",
        parser: Parser::Aider,
        parser_id: "rust-aider-md-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Text,
                usage: UsageMode::PerTurn,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity],
                request: &[RequestField::Cwd, RequestField::Namespace],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/kind", equals: "tokens" }], object: None, input: &["/input"], output: &["/output"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: None, version: None, timestamp: Some("/timestamp"), number: NumberRule::Lenient, text: TextRule::String, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: Some("/session") }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: true, invalid_utf8_fails: false, non_object_fails: false, activity_non_object_fails: false, totals_never_decrease: false, water_survives_replacement: true }),
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Aider observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: true, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "antigravity",
        parser: Parser::Antigravity,
        parser_id: "rust-antigravity-sqlite-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Sqlite,
                usage: UsageMode::None,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity],
                request: &[RequestField::Cwd, RequestField::Session],
                usage_fields: None,
                store: Some(Store { rows: None, parent: None, marks: &[Marks { table: "steps", id: "idx", key: Some("idx"), group: None, owner: None, payload: Some("step_payload"), stamp: None, columns: &["step_type", "status", "metadata"], prefix: "step:", key_scope: KeyScope::Session, page: 256, text_cap: None, start: MarkStart::Newest, shrink: MarkShrink::Keep, pending_max_age: Some(PendingAge { pointer: None, unit: TimeUnit::Seconds, seconds: 86400 }), reply_from: None, walk: Walk::Antigravity(AntigravityWalk::Steps), after: None }], row_prefix: "", marker: None }),
                rules: None,
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Antigravity observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: false, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "claude",
        parser: Parser::Claude,
        parser_id: "rust-claude-jsonl-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Jsonl,
                usage: UsageMode::PerTurn,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity, Mode::Turns],
                request: &[],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/type", equals: "assistant" }], object: Some("/message/usage"), input: &["/message/usage/input_tokens"], output: &["/message/usage/output_tokens"], cache_read: &["/message/usage/cache_read_input_tokens"], cache_creation: &["/message/usage/cache_creation_input_tokens"], key: &["/message/id", "/requestId"], key_join: "::", model: Some("/message/model"), version: Some("/version"), timestamp: Some("/timestamp"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: true, invalid_utf8_fails: true, non_object_fails: true, activity_non_object_fails: true, totals_never_decrease: false, water_survives_replacement: true }),
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "invalid observation mode", non_object: Some("Claude record is not an object") }, compat: Compat { absent_file_is_empty: true, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "codex",
        parser: Parser::Codex,
        parser_id: "rust-codex-jsonl-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Jsonl,
                usage: UsageMode::Cumulative,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity, Mode::Identify],
                request: &[],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/type", equals: "event_msg" }, Select { pointer: "/payload/type", equals: "token_count" }], object: Some("/payload/info/total_token_usage"), input: &["/payload/info/total_token_usage/input_tokens", "/payload/info/total_token_usage/cached_input_tokens"], output: &["/payload/info/total_token_usage/output_tokens", "/payload/info/total_token_usage/reasoning_output_tokens"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: None, version: None, timestamp: Some("/timestamp"), number: NumberRule::Strict, text: TextRule::String, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: false, invalid_utf8_fails: false, non_object_fails: false, activity_non_object_fails: true, totals_never_decrease: false, water_survives_replacement: true }),
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Codex observation mode not integrated", non_object: Some("Codex record is not an object") }, compat: Compat { absent_file_is_empty: false, no_python_turn_fallback: true, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "copilot",
        parser: Parser::Copilot,
        parser_id: "rust-copilot-jsonl-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Sqlite,
                usage: UsageMode::PerTurn,
                store_file: Some("session-store.db"),
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity],
                request: &[],
                usage_fields: Some(UsageFields { select: &[], object: None, input: &["/input_tokens"], output: &["/output_tokens"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: Some("/model"), version: None, timestamp: Some("/created_at"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: Some(Store { rows: Some(Rows { table: "assistant_usage_events", id: "id", key: "id", group: Some("session_id"), payload: None, columns: &["model", "input_tokens", "output_tokens", "created_at"], anchor: &["session_id", "model", "input_tokens", "output_tokens", "created_at"], pending_until: None, pending_max_age: None, cursor_tag: None, raw_id: None }), parent: Some(Parent { table: "sessions", key: "id", dir: "cwd", up: None, version: None, columns: &[], join: JoinPolicy::Left, top: None, dir_form: DirForm::AsStored }), marks: &[Marks { table: "assistant_usage_events", id: "id", key: None, group: Some("session_id"), owner: None, payload: None, stamp: None, columns: &["created_at"], prefix: "db_act:usage:", key_scope: KeyScope::None, page: 256, text_cap: None, start: MarkStart::Oldest, shrink: MarkShrink::Oldest, pending_max_age: None, reply_from: None, walk: Walk::Copilot(CopilotWalk::AssistantUsageEvents), after: None }, Marks { table: "turns", id: "id", key: None, group: Some("session_id"), owner: None, payload: None, stamp: None, columns: &["user_message", "assistant_response", "timestamp"], prefix: "db_act:turn:", key_scope: KeyScope::None, page: 256, text_cap: None, start: MarkStart::Oldest, shrink: MarkShrink::Oldest, pending_max_age: None, reply_from: None, walk: Walk::Copilot(CopilotWalk::Turns), after: Some(Walk::Copilot(CopilotWalk::AssistantUsageEvents)) }], row_prefix: "copilot_usage::", marker: None }),
                rules: None,
                defers_to: None,
            },
            Layout {
                source: SourceKind::Jsonl,
                usage: UsageMode::Cumulative,
                store_file: Some("events.jsonl"),
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity],
                request: &[RequestField::Cwd, RequestField::Session],
                usage_fields: Some(UsageFields { select: &[], object: Some("/data/tokenDetails"), input: &["/data/tokenDetails/input/tokenCount", "/data/tokenDetails/cache_read/tokenCount", "/data/tokenDetails/cache_write/tokenCount"], output: &["/data/tokenDetails/output/tokenCount"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: None, version: None, timestamp: Some("/timestamp"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: true, invalid_utf8_fails: false, non_object_fails: false, activity_non_object_fails: false, totals_never_decrease: true, water_survives_replacement: true }),
                defers_to: Some(Defers { file: "session-store.db", up: 3 }),
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Copilot observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: true, no_python_turn_fallback: false, store_error_reported: true } },
    },
    ObserverFacts {
        vendor: "cursor",
        parser: Parser::Cursor,
        parser_id: "rust-cursor-sqlite-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Sqlite,
                usage: UsageMode::None,
                store_file: Some("store.db"),
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity, Mode::Identify],
                request: &[RequestField::Cwd, RequestField::Session, RequestField::BaseEpoch],
                usage_fields: None,
                store: Some(Store { rows: None, parent: None, marks: &[Marks { table: "blobs", id: "rowid", key: Some("id"), group: None, owner: None, payload: Some("data"), stamp: None, columns: &[], prefix: "blob:", key_scope: KeyScope::None, page: 512, text_cap: Some(8000), start: MarkStart::NewestOrZero, shrink: MarkShrink::Newest, pending_max_age: None, reply_from: None, walk: Walk::Cursor(CursorWalk::Blobs), after: None }], row_prefix: "", marker: Some(Marker { text: "at-pane:", on: "blobs", role: None, order_by_stamp: false, within: Some(Within { rows: 512, bytes: 262144 }), binding: Binding::PathSession, pointer: None }) }),
                rules: None,
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Cursor observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: false, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "droid",
        parser: Parser::Droid,
        parser_id: "rust-droid-jsonl-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Jsonl,
                usage: UsageMode::Cumulative,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::SessionUsage, Mode::Activity],
                request: &[RequestField::Cwd, RequestField::Session],
                usage_fields: Some(UsageFields { select: &[], object: Some("/tokenUsage"), input: &["/tokenUsage/inputTokens", "/tokenUsage/cacheCreationTokens", "/tokenUsage/cacheReadTokens"], output: &["/tokenUsage/outputTokens"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: Some("/model"), version: None, timestamp: None, number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Folded, time: TimeRule::Text, document: Some(".settings.json"), cwd: None, session: None }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: false, invalid_utf8_fails: true, non_object_fails: false, activity_non_object_fails: false, totals_never_decrease: false, water_survives_replacement: true }),
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Droid observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: true, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "goose",
        parser: Parser::Goose,
        parser_id: "rust-goose-sqlite-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Sqlite,
                usage: UsageMode::PerTurn,
                store_file: Some("sessions.db"),
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity, Mode::HasSession, Mode::Identify],
                request: &[],
                usage_fields: Some(UsageFields { select: &[], object: None, input: &["/input_tokens"], output: &["/output_tokens"], cache_read: &["/cache_read_tokens"], cache_creation: &["/cache_write_tokens"], key: &[], key_join: "", model: Some("/model"), version: None, timestamp: Some("/created_timestamp"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Separate, time: TimeRule::EpochSeconds, document: None, cwd: None, session: None }),
                store: Some(Store { rows: Some(Rows { table: "usage_ledger", id: "id", key: "id", group: Some("session_id"), payload: None, columns: &["model", "created_timestamp", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"], anchor: &["id", "session_id"], pending_until: None, pending_max_age: None, cursor_tag: None, raw_id: Some("row_id") }), parent: Some(Parent { table: "sessions", key: "id", dir: "working_dir", up: Some("parent_session_id"), version: None, columns: &["session_type"], join: JoinPolicy::Inner, top: Some(TopIs { column: "session_type", at: 0, equals: "user" }), dir_form: DirForm::AsStored }), marks: &[Marks { table: "messages", id: "id", key: None, group: Some("session_id"), owner: None, payload: Some("content_json"), stamp: Some("created_timestamp"), columns: &["role", "metadata_json"], prefix: "message:", key_scope: KeyScope::None, page: 256, text_cap: None, start: MarkStart::NewestOrZero, shrink: MarkShrink::Keep, pending_max_age: None, reply_from: None, walk: Walk::Goose(GooseWalk::Messages), after: None }], row_prefix: "usage:", marker: Some(Marker { text: "at-pane:", on: "messages", role: Some(Role::Column(ColumnIs { column: "role", equals: "user" })), order_by_stamp: false, within: None, binding: Binding::StoreRows, pointer: None }) }),
                rules: None,
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Goose observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: false, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "grok",
        parser: Parser::Grok,
        parser_id: "rust-grok-jsonl-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Jsonl,
                usage: UsageMode::PerTurn,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity, Mode::Turns],
                request: &[RequestField::Cwd],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/kind", equals: "turn_completed" }], object: Some("/usage"), input: &["/usage/inputTokens"], output: &["/usage/outputTokens"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: Some("/model"), version: None, timestamp: Some("/timestamp"), number: NumberRule::Lenient, text: TextRule::String, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: Some("/session") }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: true, invalid_utf8_fails: false, non_object_fails: false, activity_non_object_fails: false, totals_never_decrease: false, water_survives_replacement: false }),
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Grok observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: true, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "kilo",
        parser: Parser::Opencode,
        parser_id: "rust-kilo-sqlite-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Sqlite,
                usage: UsageMode::PerTurn,
                store_file: Some("kilo.db"),
                applies_when: &["session_v2", "session_message"],
                modes: &[Mode::Usage, Mode::Legacy, Mode::Activity, Mode::HasSession, Mode::Identify],
                request: &[],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/type", equals: "assistant" }], object: None, input: &["/tokens/input", "/tokens/cache/read", "/tokens/cache/write"], output: &["/tokens/output", "/tokens/reasoning"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: Some("/model/id"), version: None, timestamp: Some("/time/completed"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: Some(Store { rows: Some(Rows { table: "session_message", id: "rowid", key: "id", group: Some("session_id"), payload: Some("data"), columns: &["type"], anchor: &["id", "session_id"], pending_until: Some("/time/completed"), pending_max_age: Some(PendingAge { pointer: Some("/time/created"), unit: TimeUnit::Millis, seconds: 86400 }), cursor_tag: Some("v2"), raw_id: None }), parent: Some(Parent { table: "session_v2", key: "id", dir: "directory", up: Some("parent_id"), version: Some("version"), columns: &[], join: JoinPolicy::Inner, top: None, dir_form: DirForm::WindowsNative }), marks: &[Marks { table: "session_message", id: "rowid", key: None, group: Some("session_id"), owner: None, payload: Some("data"), stamp: Some("time_created"), columns: &["type", "seq"], prefix: "message:", key_scope: KeyScope::None, page: 256, text_cap: None, start: MarkStart::NewestOrZero, shrink: MarkShrink::Keep, pending_max_age: None, reply_from: Some(ReplyFrom { order: "seq", matching: ColumnIs { column: "type", equals: "assistant" }, before: 1 }), walk: Walk::Opencode(OpencodeWalk::SessionMessage), after: None }], row_prefix: "msg:", marker: Some(Marker { text: "at-pane:", on: "session_message", role: Some(Role::Column(ColumnIs { column: "type", equals: "user" })), order_by_stamp: true, within: None, binding: Binding::StoreRows, pointer: Some("/text") }) }),
                rules: None,
                defers_to: None,
            },
            Layout {
                source: SourceKind::Sqlite,
                usage: UsageMode::PerTurn,
                store_file: Some("kilo.db"),
                applies_when: &[],
                modes: &[Mode::Usage, Mode::Legacy, Mode::Activity, Mode::HasSession, Mode::Identify],
                request: &[],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/role", equals: "assistant" }], object: None, input: &["/tokens/input", "/tokens/cache/read", "/tokens/cache/write"], output: &["/tokens/output", "/tokens/reasoning"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: Some("/modelID"), version: None, timestamp: Some("/time/completed"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: Some(Store { rows: Some(Rows { table: "message", id: "rowid", key: "id", group: Some("session_id"), payload: Some("data"), columns: &[], anchor: &["id", "session_id"], pending_until: Some("/time/completed"), pending_max_age: Some(PendingAge { pointer: Some("/time/created"), unit: TimeUnit::Millis, seconds: 86400 }), cursor_tag: None, raw_id: None }), parent: Some(Parent { table: "session", key: "id", dir: "directory", up: Some("parent_id"), version: Some("version"), columns: &[], join: JoinPolicy::Inner, top: None, dir_form: DirForm::AsStored }), marks: &[Marks { table: "part", id: "rowid", key: None, group: Some("session_id"), owner: Some("message_id"), payload: Some("data"), stamp: Some("time_created"), columns: &[], prefix: "part:", key_scope: KeyScope::None, page: 256, text_cap: None, start: MarkStart::Newest, shrink: MarkShrink::Keep, pending_max_age: None, reply_from: None, walk: Walk::Opencode(OpencodeWalk::Part), after: None }], row_prefix: "msg:", marker: Some(Marker { text: "at-pane:", on: "part", role: Some(Role::Pointer(Select { pointer: "/role", equals: "user" })), order_by_stamp: true, within: None, binding: Binding::StoreRows, pointer: None }) }),
                rules: None,
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "OpenCode observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: false, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "kimi",
        parser: Parser::Kimi,
        parser_id: "rust-kimi-jsonl-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Jsonl,
                usage: UsageMode::PerTurn,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity],
                request: &[RequestField::Cwd, RequestField::Session, RequestField::NowMs],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/type", equals: "usage.record" }], object: Some("/usage"), input: &["/usage/inputOther", "/usage/inputCacheRead", "/usage/inputCacheCreation"], output: &["/usage/output"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: Some("/model"), version: None, timestamp: Some("/time"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: true, invalid_utf8_fails: true, non_object_fails: true, activity_non_object_fails: true, totals_never_decrease: false, water_survives_replacement: true }),
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Kimi observation mode not integrated", non_object: Some("Kimi record is not an object") }, compat: Compat { absent_file_is_empty: false, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "muse",
        parser: Parser::Muse,
        parser_id: "rust-muse-jsonl-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Jsonl,
                usage: UsageMode::PerTurn,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity],
                request: &[RequestField::Cwd, RequestField::Session, RequestField::CliVersion],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/payload_type", equals: "runtime.session" }, Select { pointer: "/payload/event/kind", equals: "goal_usage_attribution" }, Select { pointer: "/payload/event/record/owner/requester_kind", equals: "main" }], object: Some("/payload/event/record/quantity"), input: &["/payload/event/record/quantity/input_tokens"], output: &["/payload/event/record/quantity/output_tokens"], cache_read: &[], cache_creation: &[], key: &["/payload/event/record/usage_id"], key_join: "", model: None, version: None, timestamp: Some("/recorded_at"), number: NumberRule::Lenient, text: TextRule::String, cache: CacheRule::Folded, time: TimeRule::EpochMicros, document: None, cwd: None, session: None }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: true, invalid_utf8_fails: true, non_object_fails: false, activity_non_object_fails: false, totals_never_decrease: false, water_survives_replacement: true }),
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Muse observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: false, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "opencode",
        parser: Parser::Opencode,
        parser_id: "rust-opencode-sqlite-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Sqlite,
                usage: UsageMode::PerTurn,
                store_file: Some("opencode.db"),
                applies_when: &["session_v2", "session_message"],
                modes: &[Mode::Usage, Mode::Legacy, Mode::Activity, Mode::HasSession, Mode::Identify],
                request: &[],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/type", equals: "assistant" }], object: None, input: &["/tokens/input", "/tokens/cache/read", "/tokens/cache/write"], output: &["/tokens/output", "/tokens/reasoning"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: Some("/model/id"), version: None, timestamp: Some("/time/completed"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: Some(Store { rows: Some(Rows { table: "session_message", id: "rowid", key: "id", group: Some("session_id"), payload: Some("data"), columns: &["type"], anchor: &["id", "session_id"], pending_until: Some("/time/completed"), pending_max_age: Some(PendingAge { pointer: Some("/time/created"), unit: TimeUnit::Millis, seconds: 86400 }), cursor_tag: Some("v2"), raw_id: None }), parent: Some(Parent { table: "session_v2", key: "id", dir: "directory", up: Some("parent_id"), version: Some("version"), columns: &[], join: JoinPolicy::Inner, top: None, dir_form: DirForm::WindowsNative }), marks: &[Marks { table: "session_message", id: "rowid", key: None, group: Some("session_id"), owner: None, payload: Some("data"), stamp: Some("time_created"), columns: &["type", "seq"], prefix: "message:", key_scope: KeyScope::None, page: 256, text_cap: None, start: MarkStart::NewestOrZero, shrink: MarkShrink::Keep, pending_max_age: None, reply_from: Some(ReplyFrom { order: "seq", matching: ColumnIs { column: "type", equals: "assistant" }, before: 1 }), walk: Walk::Opencode(OpencodeWalk::SessionMessage), after: None }], row_prefix: "msg:", marker: Some(Marker { text: "at-pane:", on: "session_message", role: Some(Role::Column(ColumnIs { column: "type", equals: "user" })), order_by_stamp: true, within: None, binding: Binding::StoreRows, pointer: Some("/text") }) }),
                rules: None,
                defers_to: None,
            },
            Layout {
                source: SourceKind::Sqlite,
                usage: UsageMode::PerTurn,
                store_file: Some("opencode.db"),
                applies_when: &[],
                modes: &[Mode::Usage, Mode::Legacy, Mode::Activity, Mode::HasSession, Mode::Identify],
                request: &[],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/role", equals: "assistant" }], object: None, input: &["/tokens/input", "/tokens/cache/read", "/tokens/cache/write"], output: &["/tokens/output", "/tokens/reasoning"], cache_read: &[], cache_creation: &[], key: &[], key_join: "", model: Some("/modelID"), version: None, timestamp: Some("/time/completed"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: Some(Store { rows: Some(Rows { table: "message", id: "rowid", key: "id", group: Some("session_id"), payload: Some("data"), columns: &[], anchor: &["id", "session_id"], pending_until: Some("/time/completed"), pending_max_age: Some(PendingAge { pointer: Some("/time/created"), unit: TimeUnit::Millis, seconds: 86400 }), cursor_tag: None, raw_id: None }), parent: Some(Parent { table: "session", key: "id", dir: "directory", up: Some("parent_id"), version: Some("version"), columns: &[], join: JoinPolicy::Inner, top: None, dir_form: DirForm::AsStored }), marks: &[Marks { table: "part", id: "rowid", key: None, group: Some("session_id"), owner: Some("message_id"), payload: Some("data"), stamp: Some("time_created"), columns: &[], prefix: "part:", key_scope: KeyScope::None, page: 256, text_cap: None, start: MarkStart::Newest, shrink: MarkShrink::Keep, pending_max_age: None, reply_from: None, walk: Walk::Opencode(OpencodeWalk::Part), after: None }], row_prefix: "msg:", marker: Some(Marker { text: "at-pane:", on: "part", role: Some(Role::Pointer(Select { pointer: "/role", equals: "user" })), order_by_stamp: true, within: None, binding: Binding::StoreRows, pointer: None }) }),
                rules: None,
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "OpenCode observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: false, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "pi",
        parser: Parser::Pi,
        parser_id: "rust-pi-jsonl-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Jsonl,
                usage: UsageMode::PerTurn,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity],
                request: &[RequestField::Cwd, RequestField::Session, RequestField::NowMs],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/kind", equals: "usage" }], object: Some("/usage"), input: &["/usage/input", "/usage/cacheRead", "/usage/cacheWrite"], output: &["/usage/output"], cache_read: &[], cache_creation: &[], key: &["/id"], key_join: "", model: Some("/model"), version: None, timestamp: Some("/timestamp"), number: NumberRule::Lenient, text: TextRule::String, cache: CacheRule::Folded, time: TimeRule::Text, document: None, cwd: None, session: None }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: true, invalid_utf8_fails: true, non_object_fails: false, activity_non_object_fails: false, totals_never_decrease: false, water_survives_replacement: true }),
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Pi observation mode not integrated", non_object: None }, compat: Compat { absent_file_is_empty: true, no_python_turn_fallback: false, store_error_reported: false } },
    },
    ObserverFacts {
        vendor: "qwen",
        parser: Parser::Qwen,
        parser_id: "rust-qwen-jsonl-v1",
        platforms: &[Platform::Unix, Platform::Windows],
        layouts: &[
            Layout {
                source: SourceKind::Jsonl,
                usage: UsageMode::PerTurn,
                store_file: None,
                applies_when: &[],
                modes: &[Mode::Usage, Mode::SessionUsage, Mode::Activity],
                request: &[RequestField::Cwd, RequestField::Session, RequestField::NowMs],
                usage_fields: Some(UsageFields { select: &[Select { pointer: "/type", equals: "assistant" }], object: Some("/usageMetadata"), input: &["/usageMetadata/promptTokenCount"], output: &["/usageMetadata/candidatesTokenCount", "/usageMetadata/thoughtsTokenCount"], cache_read: &["/usageMetadata/cachedContentTokenCount"], cache_creation: &[], key: &["/uuid"], key_join: "", model: Some("/model"), version: Some("/version"), timestamp: Some("/timestamp"), number: NumberRule::Lenient, text: TextRule::Any, cache: CacheRule::Separate, time: TimeRule::Text, document: None, cwd: Some("/cwd"), session: None }),
                store: None,
                rules: Some(JsonlRules { missing_identity_rotates: true, invalid_utf8_fails: true, non_object_fails: true, activity_non_object_fails: true, totals_never_decrease: false, water_survives_replacement: true }),
                defers_to: None,
            },
        ],
        core: Core { messages: Messages { unsupported_mode: "Qwen observation mode not integrated", non_object: Some("Qwen record is not an object") }, compat: Compat { absent_file_is_empty: true, no_python_turn_fallback: false, store_error_reported: false } },
    },
];

/// The observer declared for `vendor` on this platform.
pub fn find(vendor: &str) -> Option<&'static ObserverFacts> {
    OBSERVERS.iter().find(|facts| facts.vendor == vendor && facts.platforms.contains(&CURRENT))
}

/// One observation: the result document.
/// Every parser is named here, so a declared parser without a branch does not compile. The usage and capability
/// types of a parser come from its declaration, so an adapter that does not implement what its declaration
/// names does not compile either.
pub fn dispatch(facts: &'static ObserverFacts, args: &Value) -> io::Result<Value> {
    match facts.parser {
        Parser::Aider => {
            let request = Request::parse(args)?;
            let document = observe::<aider::Aider, super::JsonlFile, super::PerTurn, super::NoCapability>(&request, facts)?;
            Ok(document)
        }
        Parser::Antigravity => {
            let request = Request::parse(args)?;
            let document = observe::<antigravity::Antigravity, super::Sqlite, super::NoUsage, super::NoCapability>(&request, facts)?;
            Ok(document)
        }
        Parser::Claude => {
            let request = Request::parse(args)?;
            let document = observe::<claude::Claude, super::JsonlFile, super::PerTurn, super::ByTurns>(&request, facts)?;
            Ok(document)
        }
        Parser::Codex => {
            let request = Request::parse(args)?;
            let document = observe::<codex::Codex, super::JsonlFile, super::Cumulative, super::NoCapability>(&request, facts)?;
            Ok(document)
        }
        Parser::Copilot => {
            let request = Request::parse(args)?;
            // One arm per store file: each names the source, usage and capability types of its own declaration.
            let document = match facts.slot(request.path) {
                0 => observe::<copilot::Copilot, super::Sqlite, super::PerTurn, super::NoCapability>(&request, facts)?,
                _ => observe::<copilot::Copilot, super::JsonlFile, super::Cumulative, super::NoCapability>(&request, facts)?,
            };
            Ok(document)
        }
        Parser::Cursor => {
            let request = Request::parse(args)?;
            let document = observe::<cursor::Cursor, super::Sqlite, super::NoUsage, super::ByMarker>(&request, facts)?;
            Ok(document)
        }
        Parser::Droid => {
            let request = Request::parse(args)?;
            let document = observe::<droid::Droid, super::JsonlFile, super::Cumulative, super::NoCapability>(&request, facts)?;
            Ok(document)
        }
        Parser::Goose => {
            let request = Request::parse(args)?;
            let document = observe::<goose::Goose, super::Sqlite, super::PerTurn, (super::BySession, super::ByMarker)>(&request, facts)?;
            Ok(document)
        }
        Parser::Grok => {
            let request = Request::parse(args)?;
            let document = observe::<grok::Grok, super::JsonlFile, super::PerTurn, super::ByTurns>(&request, facts)?;
            Ok(document)
        }
        Parser::Opencode => {
            let request = Request::parse(args)?;
            let document = observe::<opencode::Opencode, super::Sqlite, super::PerTurn, (super::BySession, super::ByMarker)>(&request, facts)?;
            Ok(document)
        }
        Parser::Kimi => {
            let request = Request::parse(args)?;
            let document = observe::<kimi::Kimi, super::JsonlFile, super::PerTurn, super::NoCapability>(&request, facts)?;
            Ok(document)
        }
        Parser::Muse => {
            let request = Request::parse(args)?;
            let document = observe::<muse::Muse, super::JsonlFile, super::PerTurn, super::NoCapability>(&request, facts)?;
            Ok(document)
        }
        Parser::Pi => {
            let request = Request::parse(args)?;
            let document = observe::<pi::Pi, super::JsonlFile, super::PerTurn, super::NoCapability>(&request, facts)?;
            Ok(document)
        }
        Parser::Qwen => {
            let request = Request::parse(args)?;
            let document = observe::<qwen::Qwen, super::JsonlFile, super::PerTurn, super::NoCapability>(&request, facts)?;
            Ok(document)
        }
    }
}
