//! The declaration schema: everything one CLI is, as typed static data.
//!
//! A vendor file under `src/vendors/` fills one `Cli`. The core reads nothing
//! else about a vendor, and nothing here names one. Every type is `'static`
//! data (slices and string literals), so a declaration costs no allocation and
//! no parsing, and is checked by the compiler.
use serde::Serialize;

pub type Str = &'static str;

#[derive(Debug, Serialize)]
pub struct Cli {
    pub key: Str,
    pub label: Str,
    pub kind: Kind,
    pub launch: Launch,
    pub resume: Resume,
    /// Byte(s) that interrupt the CLI in its PTY.
    pub interrupt: &'static [u8],
    /// `None`: not declared here (no transcript, or its records are not yet
    /// described); the backend keeps main's Python reader for it.
    pub observe: Option<Observe>,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Cli,
    Agent,
}

// ---- launch -----------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct Launch {
    pub program: Str,
    pub base_args: &'static [Str],
    /// Appended when the pane runs unattended (YOLO). Empty: the CLI has none.
    pub yolo_args: &'static [Str],
    /// Arguments that name the model; `{}` is the value. `None`: refused.
    pub model: Option<&'static [Str]>,
    /// Arguments that name the reasoning effort; `{}` is the value.
    pub effort: Option<&'static [Str]>,
    /// Closed effort vocabulary; empty = any value is passed through.
    pub efforts: &'static [Str],
    /// Added only when neither the request nor the inherited env has the name.
    pub env_defaults: &'static [(Str, Str)],
    /// Names that relocate the CLI's home: stripped from inherited env.
    pub env_strip: &'static [Str],
}

// ---- resume -----------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct Resume {
    /// Resume arguments after the program; `{id}` is the session id.
    pub args: Option<&'static [Str]>,
    /// How a saved command names the session it resumes.
    pub parse: Option<ResumeParse>,
}

#[derive(Debug, Serialize)]
pub struct ResumeParse {
    /// Accepted first tokens.
    pub programs: &'static [Str],
    pub tokens: Tokens,
    /// Token 1 must be one of these (a subcommand); empty = no subcommand.
    pub subcommand: &'static [Str],
    /// The id follows the subcommand directly (`codex resume <id>`).
    pub id_after_subcommand: bool,
    pub flags: &'static [Str],
    /// `--flag=value` is accepted too, with a value of this shape.
    pub joined: Option<ValueShape>,
    /// Which occurrence wins when a flag repeats. `Whitespace` picks among
    /// the occurrences whose value has the right shape (main's regexes
    /// backtrack past a bad one); `Literal` lets the first occurrence decide.
    pub pick: Pick,
    pub value: ValueShape,
}

#[derive(Debug, Serialize, Clone, Copy, PartialEq, Eq)]
pub enum Tokens {
    /// Whitespace split of the command's last element (a shell wrapper's script).
    Whitespace,
    /// A literal invocation only: known shell wrappers unwrapped, no shell
    /// metacharacters, shell-quoted split, scanning stops at `--`.
    Literal,
}

#[derive(Debug, Serialize, Clone, Copy, PartialEq, Eq)]
pub enum Pick {
    First,
    Last,
}

#[derive(Debug, Serialize, Clone, Copy, PartialEq, Eq)]
pub enum ValueShape {
    Any,
    /// Must not start with `-`.
    NoDash,
    /// A canonical hyphenated UUID in any case; the id is its lowercase form.
    Uuid,
    /// Starts with this prefix and has no surrounding whitespace.
    Prefix(Str),
}

// ---- observe ----------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct Observe {
    pub store: Store,
    pub format: Format,
    /// Which files are transcripts (`None`: any file the store lists).
    pub accept: Option<Accept>,
    /// Per-file values (cwd, session id, last reply text, …). Their names are
    /// also the keys they are persisted under in a checkpoint.
    pub slots: &'static [Slot],
    pub usage: Option<UsageSpec>,
    pub activity: ActivitySpec,
    pub turns: TurnsSpec,
}

#[derive(Debug, Serialize)]
pub struct Accept {
    /// The exact file name.
    pub name: Str,
    /// Not when the `n`-th directory above (1 = its own) has this name
    /// (muse: a subagent's log sits under `subagent/<child>/`).
    pub not_under: Option<(usize, Str)>,
}

/// Where records live: a root, then a glob below it.
#[derive(Debug, Serialize)]
pub struct Store {
    /// Environment variables whose value is the root (first set wins), each
    /// with the path appended below it.
    pub env: &'static [(Str, &'static [Str])],
    /// Home-relative fallbacks; the first existing directory wins.
    pub home: &'static [&'static [Str]],
    /// Glob segments below the root (`*` matches one name).
    pub files: &'static [&'static [Str]],
}

/// The record backend. Only JSONL is built; a SQLite store (one database
/// for every session, rows read after a watermark) is the planned second
/// variant and its vendors stay on the Python readers until then.
#[derive(Debug, Serialize)]
pub enum Format {
    Jsonl,
}

#[derive(Debug, Serialize)]
pub struct Slot {
    pub name: Str,
    pub init: SlotInit,
}

#[derive(Debug, Serialize)]
pub enum SlotInit {
    Empty,
    /// File name without extension.
    FileStem,
    /// Name of the `n`-th directory above the file (1 = its own directory).
    DirName(usize),
    /// Text after the first `_` of the stem ("" when there is none).
    StemAfterUnderscore,
    /// `field` of the first JSON record matching `when` within the first
    /// `lines` lines (blank and malformed lines count; 0 = no limit). With
    /// `first_line`, only the first non-blank line is considered at all.
    Header { lines: usize, when: Pred, field: Text, first_line: bool },
    /// A JSON object file in the `up`-th directory above the transcript
    /// (1 = its own directory); the first key with a truthy value wins.
    SidecarJson { up: usize, file: Str, keys: &'static [Str] },
    /// The `up`-th directory's name, URL-decoded, unless `override_file` in
    /// it holds the real value (stripped).
    GroupDir { up: usize, override_file: Str },
    /// Directory name `-a-b` read back as `/a/b` (lossy; a last resort).
    DashDir,
    /// First non-empty of several initialisers.
    FirstOf(&'static [SlotInit]),
}

// ---- record access ----------------------------------------------------------

/// A path into a JSON record: object keys, read left to right.
pub type Path = &'static [Str];

#[derive(Debug, Serialize)]
pub enum Pred {
    True,
    /// The value at the path is this string.
    Eq(Path, Str),
    In(Path, &'static [Str]),
    /// The key exists (any value, `null` included).
    Has(Path),
    /// Python truthiness of the value.
    Truthy(Path),
    IsObject(Path),
    IsList(Path),
    IsString(Path),
    /// A test on computed text.
    Text(Text, StrTest),
    /// Some element of the list at the path satisfies the predicate.
    AnyItem(Path, &'static Pred),
    /// Anthropic content tree: a string, or blocks whose `text` (type text) or
    /// `content` (any other type) is itself a content tree.
    ContentTree(Path, StrTest),
    /// The inferred-turn tracker has a turn that was cancelled.
    TurnCancelled,
    /// A slot is non-empty.
    SlotSet(Str),
    All(&'static [Pred]),
    Any(&'static [Pred]),
    Not(&'static Pred),
}

#[derive(Debug, Serialize, Clone, Copy)]
pub enum StrTest {
    Eq(Str),
    Ne(Str),
    In(&'static [Str]),
    NotIn(&'static [Str]),
    StartsWith(Str),
    Contains(Str),
    NonEmpty,
    Empty,
}

#[derive(Debug, Serialize)]
pub enum Text {
    Lit(Str),
    /// Python `str(value or "")`.
    Py(Path),
    /// The value only if it is a string, else "".
    Str(Path),
    Slot(Str),
    /// First non-empty.
    FirstOf(&'static [Text]),
    Concat(&'static [Text]),
    /// `join_text_blocks`: a string as is, else the `text` of blocks of this type.
    Blocks(Path, Str),
    /// Join `item` evaluated per element of the list at `list` (elements that
    /// fail `filter` skipped), with `sep`; empty results are kept only when
    /// `keep_empty`.
    Join { list: Path, filter: &'static Pred, item: &'static Text, sep: Str, keep_empty: bool },
    /// Values of `<tag>…</tag>` occurrences (non-empty, no `<`), joined by `\n`.
    TagValues(&'static Text, Str),
    /// The first `<tag>…</tag>` value.
    TagFirst(&'static Text, Str),
    /// When the text starts with `prefix`, the non-space token right after it.
    AfterPrefix(&'static Text, Str),
    /// `user_prompt_text`: stripped, "" for `<`-wrappers, capped at 500 chars.
    UserPrompt(&'static Text),
    /// Stripped.
    Strip(&'static Text),
    /// Over `n` chars: the first and last halves around `\n…\n`.
    Cap(&'static Text, usize),
    If(&'static Pred, &'static Text),
    /// A number rendered as decimal.
    Num(&'static Num),
    /// Epoch milliseconds (number or numeric string) rendered as digits; "" when not a number.
    Millis(Path),
    /// ISO-8601 from epoch milliseconds at the first path, else epoch seconds at the second.
    IsoFromEpoch(Path, Path),
    /// ISO-8601 (`+00:00`, microseconds when non-zero) from epoch microseconds.
    IsoFromMicros(Path),
    /// Key of the first entry of the object at the path.
    FirstKey(Path),
    /// 1-based line number of the record.
    Line,
    /// Byte offset just past the record.
    Offset,
    /// The key of the map entry being iterated (turn calls over a map).
    EntryKey,
}

#[derive(Debug, Serialize)]
pub enum Num {
    Zero,
    /// Python `max(0, int(v))`, 0 when not a number.
    Of(Path),
    Sum(&'static [Num]),
    /// `max(0, a - b - …)`.
    Sub(&'static Num, &'static [Num]),
    /// Sum of `field` over the values of the object at `map`; `None` when no
    /// value carried `field`'s object (copilot modelMetrics).
    MapSum { map: Path, object: Path, field: Path },
    /// The value of the first alternative whose `when` holds (even when that
    /// value is "no reading").
    Case(&'static [(Pred, Num)]),
    /// Only when the record holds the path at all (else this rule yields no reading).
    Required(Path, &'static Num),
}

// ---- usage ------------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct UsageSpec {
    /// Slots that shadow the file's same-named ones while reading usage.
    pub slots: &'static [Slot],
    pub rules: &'static [UsageRule],
    pub mode: UsageMode,
    /// Slots persisted in the checkpoint (and restored from it).
    pub persist: &'static [Str],
    /// The `persist` slots stored as JSON booleans (set = `true`), as main
    /// keeps its flags (codex's `meta_seen`).
    pub flags: &'static [Str],
    /// When a checkpoint's persisted slots and totals are thrown away.
    pub restore: Restore,
    /// Constants every checkpoint carries (codex's `basis`). A cumulative
    /// checkpoint with totals but without them re-derives its baseline from
    /// the last totals record before its offset.
    pub stamp: &'static [(Str, i64)],
}

#[derive(Debug, Serialize, Clone, Copy, PartialEq, Eq)]
pub enum Restore {
    /// Whenever the cursor restarts (rewrite, shrink, replacement).
    UnlessRotated,
    /// Only when the file is a different one (identity changed).
    UnlessReplaced,
}

#[derive(Debug, Serialize)]
pub enum UsageMode {
    /// One event per record. `window`: dedup keys remembered across reads
    /// (`recent_keys`; 0 = none kept); `keep_on_rotation`: the window survives
    /// a rewrite; `credited`: a full re-read after a rewrite replays the
    /// already-credited count silently (`credited_count`).
    PerRecord { window: usize, keep_on_rotation: bool, credited: bool },
    /// The record carries running totals: one event per read with the delta.
    Cumulative,
    /// The running totals live in one JSON document beside the transcript
    /// (`<stem><suffix>`, rewritten in place), not in its records: the rules
    /// read that document, and each read yields the delta since the totals
    /// last credited. The checkpoint is main's legacy seen-set
    /// (`{"kind": "legacy", "legacy_seen": [...]}`): the credited keys plus one
    /// `mark + "<in>:<out>"` key. A shrink re-baselines without an event.
    Snapshot { suffix: Str, mark: Str },
}

#[derive(Debug, Serialize)]
pub struct UsageRule {
    pub when: Pred,
    /// Slot updates this record makes (before any reading).
    pub effects: &'static [Effect],
    pub reading: Option<Reading>,
}

#[derive(Debug, Serialize)]
pub struct Reading {
    pub input: Num,
    pub output: Num,
    pub cache_read: Num,
    pub cache_creation: Num,
    /// Dedup key; an empty key means "not a usage record" when `key_required`.
    pub key: Text,
    pub key_required: bool,
    /// Key that is never valid (claude's `::`).
    pub key_invalid: Str,
    pub model: Text,
    pub timestamp: Text,
    pub cli_version: Text,
    pub cwd: Text,
    pub session_id: Text,
    /// Extra values for the cost ledger: `(nested key path, value)`.
    pub raw: &'static [(Path, RawValue)],
}

#[derive(Debug, Serialize)]
pub enum RawValue {
    Num(Num),
    Text(Text),
}

// ---- activity ---------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct ActivitySpec {
    /// Slots that shadow the file's same-named ones during a walk.
    pub slots: &'static [Slot],
    pub cursor: Cursor,
    pub rules: &'static [Rule],
    /// Close the latest inferred turn once the transcript has been quiet.
    pub idle: Option<Idle>,
    /// How the inferred-turn tracker stamps a closed turn.
    pub turn_stamp: TurnStamp,
    /// Slot holding the reply text a closed turn carries.
    pub turn_text: Str,
    /// Slots kept across walks (all others are re-initialised per walk).
    pub persist: &'static [Str],
    /// How the kept state is stored in the watcher's per-file seen-keys bag.
    pub bag: Bag,
}

/// The walk state as keys in the log watcher's per-file `seen_keys` bag,
/// which the watcher persists. The line mark is always main's `act_hw::N`;
/// the rest uses the key formats main's readers use, so the bag means the
/// same thing whichever reader wrote it.
#[derive(Debug, Serialize)]
pub struct Bag {
    pub slots: &'static [BagSlot],
    /// The inferred-turn tracker as one JSON key: `{idx, flushed, ts}` for a
    /// `TurnStamp::Text` tracker, `{idx, last_ms, flushed[, cancelled]}` for `Millis`.
    pub turn: Option<BagTurn>,
    /// The byte cursor (`Cursor::Bytes`) as `prefix + json(sort_keys)`.
    pub cursor: Option<Str>,
}

#[derive(Debug, Serialize)]
pub enum BagSlot {
    /// `prefix + text`, rewritten every walk; no key while empty.
    Text { slot: Str, prefix: Str },
    /// `prefix + text`, rewritten only when the walk wrote the slot (even
    /// with the same value); an empty value keeps its bare-prefix key.
    TextOnChange { slot: Str, prefix: Str },
    /// `prefix + json(text)`; no key while empty.
    JsonText { slot: Str, prefix: Str },
}

#[derive(Debug, Serialize)]
pub struct BagTurn {
    pub prefix: Str,
    /// `json.dumps(separators=(",", ":"))` instead of the default `", "` / `": "`.
    pub compact: bool,
    /// No tracker is stored as `prefix + "null"` (else: no key).
    pub keep_null: bool,
}

#[derive(Debug, Serialize, Clone, Copy, PartialEq, Eq)]
pub enum Cursor {
    /// Dense line high-water mark (resumable by byte offset).
    Lines,
    /// A byte cursor over complete records (rewrites restart it).
    Bytes,
}

#[derive(Debug, Serialize)]
pub struct Idle {
    pub after_ms: u64,
    pub clock: Clock,
    pub detail: Str,
}

#[derive(Debug, Serialize, Clone, Copy)]
pub enum Clock {
    /// Time since the file's last write.
    FileMtime,
    /// Time since the turn's last recorded epoch-ms.
    TurnMillis,
}

#[derive(Debug, Serialize, Clone, Copy)]
pub enum TurnStamp {
    Text,
    Millis,
}

#[derive(Debug, Serialize)]
pub struct Rule {
    pub when: Pred,
    pub effects: &'static [Effect],
}

#[derive(Debug, Serialize)]
pub enum Effect {
    Emit(Emit),
    Set(Str, Text),
    /// Set only while the flag slot is empty, then set the flag.
    SetOnce { flag: Str, slot: Str, text: Text },
    SetIfNonEmpty(Str, Text),
    /// Append, capped like `Text::Cap`.
    Append(Str, Text, usize),
    Turn(TurnOp),
    When(&'static Pred, &'static [Effect]),
    /// Stop evaluating rules for this record.
    Stop,
}

#[derive(Debug, Serialize)]
pub struct Emit {
    pub kind: EventKind,
    pub key: Text,
    pub detail: Text,
    pub text: Text,
    pub timestamp: Text,
    /// Session id override (per-record ids); `None` = the file's.
    pub session: Option<Text>,
    /// Emit only when `text` is non-empty.
    pub require_text: bool,
}

#[derive(Debug, Serialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    AgentActive,
    TurnComplete,
    SubagentTurnEnd,
}

/// Operations on the core's inferred-turn tracker (`{idx, ts, ms, flushed, cancelled}`).
#[derive(Debug, Serialize)]
pub enum TurnOp {
    /// Close the open turn (emitting `boundary`), then open the next one at this record.
    Open { boundary: Str, ts: Text, ms: Num },
    /// Open the next turn only when none is open.
    EnsureOpen { ts: Text, ms: Num },
    /// Re-stamp the open turn with this record's time (text replaces, ms takes the max).
    Touch { ts: Option<Text>, ms: Option<Num> },
    /// Close the open turn with `detail` (stamped with the max of its ms and the record's).
    Close { detail: Str, ms: Option<Num> },
    /// Close (if open) with `detail` and mark the turn cancelled.
    Cancel { detail: Str, ms: Option<Num> },
}

// ---- turns ------------------------------------------------------------------

#[derive(Debug, Serialize)]
pub struct TurnsSpec {
    /// Slots that shadow the file's same-named ones while splitting turns.
    pub slots: &'static [Slot],
    pub method: TurnMethod,
    pub filter: SessionFilter,
    /// Slot updates made while walking (they run before `skip`).
    pub effects: &'static [Rule],
    /// Records skipped before any turn rule (claude: no `message` object).
    pub skip: Pred,
    pub open: Option<TurnOpen>,
    pub call: Option<TurnCall>,
    pub close: Option<TurnClose>,
    /// Slot holding the turns' session id.
    pub session: Str,
}

#[derive(Debug, Serialize, Clone, Copy, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum TurnMethod {
    /// The transcript names its own turns (the rules below).
    Exact,
    /// Usage cut on `turn_complete` timestamps (main's base reader).
    Inferred,
    Unsupported,
}

#[derive(Debug, Serialize)]
pub enum SessionFilter {
    /// A requested session id is ignored.
    Ignore,
    /// A requested id must be the file's session slot or its stem.
    File,
    /// Records whose own session id differs are skipped.
    Record(Text),
}

#[derive(Debug, Serialize)]
pub struct TurnOpen {
    pub when: Pred,
    pub mode: OpenMode,
    pub prompt: Text,
    pub merge: PromptMerge,
    pub started: Text,
}

#[derive(Debug, Serialize)]
pub enum OpenMode {
    /// One turn per key; a later record with the same key rejoins it.
    ByKey(Text),
    /// Every matching record starts a new turn.
    Each,
    /// Join the open turn; start one when none is open.
    Join,
}

#[derive(Debug, Serialize, Clone, Copy)]
pub enum PromptMerge {
    /// `turn_excerpt` of the prompt.
    Excerpt,
    /// `merge_prompt_excerpt`: the first typed prompt wins.
    Merge,
}

#[derive(Debug, Serialize)]
pub struct TurnCall {
    pub when: Pred,
    /// One call per entry of an object; `None`: one call, read off the record.
    pub each: Option<Each>,
    /// Paths are relative to the entry when iterating.
    pub input: Num,
    pub cache_read: Num,
    pub cache_creation: Num,
    pub output: Num,
    /// `input` counts the cache parts too: subtract them.
    pub input_includes_cache: bool,
    /// The counters are running totals: calls are their deltas.
    pub cumulative: bool,
    /// A call whose counters are all zero is not a call.
    pub skip_zero: bool,
    pub key: Option<Text>,
    pub key_invalid: Str,
    pub model: Text,
    pub ts: Text,
    pub cli_version: Text,
    pub orphan: Orphan,
}

#[derive(Debug, Serialize)]
pub struct Each {
    /// The object whose entries are calls (the entry key is `Text::EntryKey`).
    pub map: Path,
    /// Used as the single entry, with an empty key, when `map` is missing or empty.
    pub fallback: Path,
}

#[derive(Debug, Serialize, Clone, Copy, PartialEq, Eq)]
pub enum Orphan {
    /// A call before any open turn is dropped.
    Drop,
    /// Until the first open, each call is its own turn.
    OwnTurnUntilFirstOpen,
    /// The call is attached to the turn its close record makes.
    ToClose,
}

#[derive(Debug, Serialize)]
pub struct TurnClose {
    pub when: Pred,
    pub ended: Text,
    pub session: Option<Text>,
    pub model_calls: Option<Num>,
    /// Drop a closed turn whose four counters are all zero.
    pub drop_empty: bool,
}
