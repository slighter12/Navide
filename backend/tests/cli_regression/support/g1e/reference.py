"""G1-E reference checkers, registry, outcome schema and fail-closed derivation (ticket E4 port of `e2-v10-tools.py`).

Provenance: this module is the ported body of the E2 draft `evidence/e2/e2-v10-tools.py` (SHA-256 32e91686268ee48ef00c054c57b52faa95cf4105fc2a10245e21457f9a5ed031,
lines 47-1463 and 2156-2188), moved here unchanged except for the method-8 changes recorded in `evidence/e4/e4-harness-handoff.md` section 3:
  * `E1_BOUND_SHA256` is E1 v8b (E2 freeze Addendum 1), not E1 v8b; every comment, message and docstring that said "E1 v8b" says "E1 v8b";
  * the schema id is `acr-g1e-outcome/13` since E2 v13 (/10 was the v10 schema with the E1 v8b constant; v13 adds the `derivation` block, the no-matrix path and the V5 record) and lives next to this file;
  * the reference `_clean_for_log` copy is a plain definition (the draft re-executed its own source between two markers);
  * the run-time test body of the draft (`run_tests`, ~700 lines of print/SystemExit) is split into pytest tests `backend/tests/test_g1e_*.py`.
The rules (registry, margins, statistics, decision table, 21 correctness rows) are the frozen E2 v10 rules and are not changed here.
The checkers are pure functions over receipts handed to them; the production decider (`decider.py`) feeds them from manifested receipt files.
"""
from __future__ import annotations
import codecs, copy, functools, hashlib, inspect, itertools, json, math, random, re, sys, tempfile
from math import comb
from pathlib import Path
import jsonschema

TABLE_VERSION = 'prereg-e/14'   # v10's table plus the v13 no-matrix path (V10 item 5) and carried stop facts (V7 item 5), plus v14-r2: noisy rows reported with their error (V11-V13), I4 narrowed, I10 gains `noisy`
SCHEMA_ID = 'acr-g1e-outcome/14'   # v14-r2: `noisy` row status, noisyReport / c1Report / interpretation sections, options c1Noisy|noisyGuard (history: /13 under v13, /10 under E2 v10)
E1_BOUND_SHA256 = '39b82cb84fde56284f94af93bd09088f6f9dce29c60e3ca3601209a5b566451d'   # design/e1-runtime-protocol-v8b.md, bound by E2 freeze Addendum 1 (v7 was 5c2e32bd...70bd7)
N_PAIRS = 10          # fixed for this pre-registration; a different n is a NEW proposal/schema/tests/approval/freeze
PARITY_MAX_EXCEED = 1  # pass iff <= 1 of 10 pairs exceed (exact level P(Bin(10,.5)<=1)=0.01074 under independence)
CELLS = (1, 4)
ARMS = ('python', 'rust-shell')

def A(v): return {'mode': 'absolute', 'value': v, 'floor': None}
def R(v, f): return {'mode': 'relative', 'value': v, 'floor': f}
STREAM = [
    ('lat:io_ms', 'ms', A(0.5), 'P'), ('lat:io_ms:p95', 'ms', A(1.0), 'P'),
    ('lat:burst_ms', 'ms', A(10), 'P'), ('lat:burst_input_ms', 'ms', A(10), 'P'),
    ('lat:reconnect_recovery_ms', 'ms', A(3), 'P'),
    ('res:input_output:component:backend:cpuOneCorePercent', 'pt', R(0.12, 0.15), 'J'),
    ('res:input_output:view:combined:cpuOneCorePercent', 'pt', R(0.15, 0.20), 'J'),
    ('res:input_output:component:backend:rssMiB', 'MiB', A(1.0), 'J'),
    ('res:live_idle:component:backend:rssMiB', 'MiB', A(1.0), 'J'),
    ('res:empty_started_idle:component:backend:rssMiB', 'MiB', A(1.0), 'J'),
]
LIFE_A = [  # (metric, cells, unit, margin, basis)
    ('lat:first_shell_ready_ms', (1, 4), 'ms', A(20), 'J'), ('lat:warm_shell_ready_ms', (1, 4), 'ms', A(20), 'J'),
    ('lat:cold_to_first_usable_ms', (1, 4), 'ms', R(0.10, 30), 'J'), ('lat:observation_ms', (4,), 'ms', A(5), 'P'),    # v14-r2 V18 (rule R10): max(30 ms, 10 % of the pair's Python value)
    ('res:live_idle:view:combined:cpuOneCorePercent', (1, 4), 'pt', A(0.10), 'P'),
    ('res:empty_started_idle:view:combined:cpuOneCorePercent', (1, 4), 'pt', A(0.10), 'P'),
    ('lat:kill_reap_ms', (1,), 'ms', A(40), 'J'),
]
BUDGET = [  # one-arm budgets are on the Rust arm only; B2 is a paired delta
    ('res:live_idle:component:sidecar:rssMiB', (1, 4), 'MiB', A(6.5), 'P', 'rust-value'),
    ('res:input_output:component:sidecar:rssMiB', (1, 4), 'MiB', A(6.5), 'P', 'rust-value'),
    ('res:empty_started_idle:component:sidecar:rssMiB', (1, 4), 'MiB', A(6.5), 'P', 'rust-value'),
    ('lat:first_shell_ack_ms', (1, 4), 'ms', A(15), 'P', 'paired-delta'),
    ('lat:warm_shell_ack_ms', (1, 4), 'ms', A(4), 'P', 'paired-delta'),
    ('res:input_output:component:sidecar:cpuOneCorePercent', (1, 4), 'pt', A(0.30), 'P', 'rust-value'),
    ('res:live_idle:component:sidecar:cpuOneCorePercent', (1, 4), 'pt', A(0.10), 'P', 'rust-value'),
    ('res:empty_started_idle:component:sidecar:cpuOneCorePercent', (1, 4), 'pt', A(0.05), 'P', 'rust-value'),
]
C1_KEY = ('lat:kill_reap_ms', 4, 'ms', R(0.10, 20), 'J')    # v14-r2 V18 (rule R10): max(20 ms, 10 % of the pair's Python value); the improvement test (d < 0) does not use it

def build_registry() -> list[dict]:
    rows = []
    for m, u, mg, b in STREAM:
        for c in (1, 4): rows.append(dict(cls='stream', metric=m, cell=c, kind='parity', unit=u, margin=mg, basis=b, source='paired-delta'))
    for m, cells, u, mg, b in LIFE_A:
        for c in cells: rows.append(dict(cls='lifecycle', metric=m, cell=c, kind='parity', unit=u, margin=mg, basis=b, source='paired-delta'))
    for m, cells, u, mg, b, src in BUDGET:
        for c in cells: rows.append(dict(cls='budget', metric=m, cell=c, kind='budget', unit=u, margin=mg, basis=b, source=src))
    m, c, u, mg, b = C1_KEY
    rows.append(dict(cls='beat', metric=m, cell=c, kind='beat', unit=u, margin=mg, basis=b, source='paired-delta'))
    for r in rows: r['id'] = f"{r['cls']}|{r['metric']}|cell{r['cell']}"
    assert len({r['id'] for r in rows}) == len(rows)
    return rows

REG = build_registry()
assert len(REG) == 49 and not any('snapshot' in r['metric'] for r in REG)
assert sum(r['cls'] == 'stream' for r in REG) == 20
assert sum(r['cls'] == 'lifecycle' for r in REG) == 12
assert sum(r['cls'] == 'budget' for r in REG) == 16
assert sum(r['cls'] == 'beat' for r in REG) == 1


REPORT_ONLY_IDS = ['resize_ack_ms']
READER_ROWS = [f'reader|{m}|{fx}' for fx in ('claude', 'opencode') for m in ('m1_wall_ms', 'm2_cpu_s', 'm3_peak_rss_mib')]
CORRECTNESS_IDS = ['02-shell-runtime', '02-bounds-failures', '03-claude-reference', '04-codex-principal', '04-opencode-principal',
                   '05-cancel-cleanup-recovery', 'E-cleanup-no-difference', 'E-failure-isolation', 'E-sidecar-loss-uniform',
                   'E-handoff-binding', 'E-ordering-terminal-stream', 'E-bounded-reader-backpressure', 'E-drop-oldest-integrity',
                   'E-output-split-64k', 'E-log-replay-identity', 'E-cancel-handoff-no-leak', 'E-fd-hygiene', 'E-stale-generation',
                   'E-resize-baseline-ordering', 'E-ordinary-shell', 'E-observation-equality']
OPTIONAL_CORRECTNESS = 'E-observation-equality'   # 'not-required' only when readerRequired is false
assert len(CORRECTNESS_IDS) == 21

CANARY_METRICS = ('lat:cold_backend_ready_ms', 'res:live_idle:component:backend:rssMiB', 'lat:io_ms')
CANARY_IDS = [f'canary|{a}|cell{c}|{m}' for a in ARMS for c in CELLS for m in CANARY_METRICS]
assert len(CANARY_IDS) == 12
CALIBRATION_MODES = ('none', 'rows', 'rows+reader')
SNAPSHOT_OPTION = 'deferred'   # the ONLY value: measuring descendant snapshot needs a new pre-registration (entry point and cadence do not exist)

FACT_DOMAINS = {
    'matrixComplete': [False, True], 'designCompliant': [False, True], 'independenceScreen': ['pass', 'fail'],
    'calibrationRows': ['not_run', 'clear', 'noisy_listed', 'uncalibrated'],
    'baselineIdentity': ['identical', 'refactor_reviewed', 'changed'],
    'survivorsRustArm': [False, True], 'isolationBreach': [False, True],
    'correctness': ['ok', 'missing', 'failed_other', 'failed_rust_bounded', 'failed_rust_unbounded'],
    'claudeObservation': ['ok', 'rust_only_failure', 'other_failure', 'incomplete'],
    'streamStatus': ['pass', 'adverse', 'unresolved'], 'budgetStatus': ['pass', 'adverse', 'unresolved'],
    'c1Status': ['improved', 'not_improved', 'adverse', 'unresolved', 'noisy'],
    'readerBenchmark': ['pass', 'worse', 'unresolved', 'not_run', 'not_required'],
    'localityViolation': [False, True], 'maintainabilityComplete': [False, True]}
assert len(FACT_DOMAINS) == 15

ND = 'not_derivable'   # E2 v13 V10 item 5(b): legal ONLY on the no-matrix path, and only for these facts; on the v10 path it is out of domain and raises
ND_FACTS = ('designCompliant', 'independenceScreen', 'calibrationRows', 'baselineIdentity', 'streamStatus', 'budgetStatus', 'c1Status', 'localityViolation', 'maintainabilityComplete')
PATHS = ('matrix', 'no-matrix')
STOP_RULES = {'S1': 'S1-survivors-rust-arm', 'S2': 'S2-isolation-breach', 'S3': 'S3-correctness-failed-rust-unbounded',
              'S4': 'S4-terminal-stream-adverse', 'S5': 'S5-lifecycle-budget-adverse', 'S6': 'S6-kill-reap-adverse'}
RUN_KINDS = ('calibration', 'reader-calibration', 'matrix', 'reader-measure')
assert set(ND_FACTS) <= set(FACT_DOMAINS)

def decide(f: dict, path: str = 'matrix', carried=()) -> tuple[str, list[str]]:
    """The registered decision table. `path` is 'matrix' (v10 section 8, unchanged: every fact in its v10 domain) or 'no-matrix' (V10 item 5: no matrix attempt exists, `matrixComplete`
    is False and the facts in ND_FACTS may be `not_derivable`). `carried` holds stop-rule codes (S1..S6) derived from earlier runs (V7 item 5): they are stop facts of this decision."""
    if path not in PATHS: raise ValueError(f'unknown derivation path {path!r}')
    nm = path == 'no-matrix'
    for k, dom in FACT_DOMAINS.items():
        if k not in f: raise ValueError(f'fact {k} missing or outside its domain: {f.get(k)!r}')
        if isinstance(f[k], str) and f[k] == ND:
            if not (nm and k in ND_FACTS): raise ValueError(f'fact {k} is {ND}, which is legal only on the no-matrix path for {ND_FACTS}')
        elif f[k] not in dom or (isinstance(dom[0], bool) and not isinstance(f[k], bool)): raise ValueError(f'fact {k} missing or outside its domain: {f.get(k)!r}')
    if set(f) != set(FACT_DOMAINS): raise ValueError('unknown fact')
    carried = list(carried)
    if any(c not in STOP_RULES for c in carried): raise ValueError(f'carried stop fact outside S1..S6: {carried!r}')
    if nm and f['matrixComplete'] is not False: raise ValueError('the no-matrix path derives matrixComplete = False')
    nd = lambda k: isinstance(f[k], str) and f[k] == ND
    held = set(carried)
    if f['survivorsRustArm']: held.add('S1')
    if f['isolationBreach']: held.add('S2')
    if f['correctness'] == 'failed_rust_unbounded': held.add('S3')
    if f['streamStatus'] == 'adverse': held.add('S4')
    if f['budgetStatus'] == 'adverse': held.add('S5')
    if f['c1Status'] == 'adverse': held.add('S6')
    stop = [STOP_RULES[c] for c in sorted(STOP_RULES) if c in held]
    if stop: return 'stop', stop
    inc = []
    if not f['matrixComplete']: inc.append('I1-matrix-incomplete')
    if not nd('designCompliant') and not f['designCompliant']: inc.append('I2-design-noncompliant')
    if not nd('independenceScreen') and f['independenceScreen'] == 'fail': inc.append('I3-independence-screen-failed')
    if f['calibrationRows'] == 'uncalibrated': inc.append('I4-calibration-uncalibrated-row')
    if f['baselineIdentity'] == 'changed': inc.append('I5-baseline-changed')
    if f['correctness'] in ('missing', 'failed_other', 'failed_rust_bounded'): inc.append('I6-correctness-' + f['correctness'])
    if f['claudeObservation'] != 'ok': inc.append('I7-claude-observation-' + f['claudeObservation'])
    if f['streamStatus'] == 'unresolved': inc.append('I8-terminal-stream-unresolved')
    if f['budgetStatus'] == 'unresolved': inc.append('I9-lifecycle-budget-unresolved')
    if f['c1Status'] in ('not_improved', 'unresolved', 'noisy'): inc.append('I10-kill-reap-' + f['c1Status'])      # v14-r2 V13: `noisy` is always an I10 value (option (d) was not chosen)
    if f['readerBenchmark'] in ('worse', 'unresolved', 'not_run'): inc.append('I11-reader-benchmark-' + f['readerBenchmark'])
    if not nd('localityViolation') and f['localityViolation']: inc.append('I12-locality-violation')
    if not nd('maintainabilityComplete') and not f['maintainabilityComplete']: inc.append('I13-maintainability-incomplete')
    if inc: return 'inconclusive', inc
    if nm: raise ValueError('the no-matrix path can never derive CONTINUE')
    return 'continue', ['C-all-registered-conditions-met']

def continue_predicate(f: dict) -> bool:
    """Independent statement of when CONTINUE is allowed (checked against decide() over the whole fact space)."""
    return (f['matrixComplete'] is True and f['designCompliant'] is True and f['independenceScreen'] == 'pass'
            and f['calibrationRows'] in ('not_run', 'clear', 'noisy_listed') and f['baselineIdentity'] in ('identical', 'refactor_reviewed')
            and f['survivorsRustArm'] is False and f['isolationBreach'] is False and f['correctness'] == 'ok'
            and f['claudeObservation'] == 'ok' and f['streamStatus'] == 'pass' and f['budgetStatus'] == 'pass'
            and f['c1Status'] == 'improved' and f['readerBenchmark'] in ('pass', 'not_required')
            and f['localityViolation'] is False and f['maintainabilityComplete'] is True)

# ---- pairs and rows ----------------------------------------------------------------------------------------------------------
# ==== v9: ONE strict input-validation layer, called first by every reference checker (assignment 9) =====================================
# Every reference checker is registered with @checker(contract). The wrapper binds the call, marks each required argument that was not supplied as MISSING,
# runs the checker's contract (built only from the v_* primitives below) and only then the checker body, with the cleaned arguments. An invalid shape returns
# a typed 'invalid-input' result; an exception in the body returns a typed 'checker-error' result. Neither is ever a pass and neither escapes as an exception.
# "Known empty" (b'', [], an explicit None where the contract allows it) is distinct from "missing" (MISSING / not supplied) and from "unknown" (None where the
# contract requires a value): missing and unknown are always rejected, never treated as empty.
MISSING = object()   # sentinel: an argument that was not supplied is not an observation (distinct from a known-empty value)

class InputRejected(Exception):
    def __init__(self, path: str, msg: str):
        super().__init__(f'{path}: {msg}'); self.path, self.msg = path, msg

class Verdict(tuple):
    """A checker result. Equal to the plain tuple (ok, why); .kind is 'pass', 'fail', 'invalid-input' or 'checker-error'; .value carries a measurement."""
    def __new__(cls, ok: bool, why: str, kind: str | None = None, value=None):
        self = tuple.__new__(cls, (ok, why)); self.kind = kind or ('pass' if ok else 'fail'); self.value = value
        return self
    @property
    def ok(self): return self[0]
    @property
    def why(self): return self[1]

def _rej(p, msg): raise InputRejected(p, msg)
def _tn(x): return 'MISSING' if x is MISSING else type(x).__name__
def v_present(x, p):
    if x is MISSING: _rej(p, 'missing (not supplied)')
    if x is None: _rej(p, 'unknown (None)')
    return x
def v_bool(x, p):
    v_present(x, p)
    if type(x) is not bool: _rej(p, f'expected bool, got {_tn(x)}')
    return x
def v_int(x, p, lo=0, hi=None):
    v_present(x, p)
    if type(x) is not int: _rej(p, f'expected int, got {_tn(x)}')
    if lo is not None and x < lo: _rej(p, f'value {x} below the permitted minimum {lo}')
    if hi is not None and x > hi: _rej(p, f'value {x} above the permitted maximum {hi}')
    return x
def v_num(x, p, lo=0.0, hi=None):
    v_present(x, p)
    if type(x) not in (int, float): _rej(p, f'expected number, got {_tn(x)}')
    if not math.isfinite(x): _rej(p, f'non-finite number {x!r}')
    if lo is not None and x < lo: _rej(p, f'value {x} below the permitted minimum {lo}')
    if hi is not None and x > hi: _rej(p, f'value {x} above the permitted maximum {hi}')
    return x
def v_bytes(x, p, nonempty=False):
    v_present(x, p)
    if type(x) is not bytes: _rej(p, f'expected bytes, got {_tn(x)}')
    if nonempty and not x: _rej(p, 'empty bytes where at least one byte is required')
    return x
def v_str(x, p, enum=None, pattern=None):
    v_present(x, p)
    if type(x) is not str: _rej(p, f'expected str, got {_tn(x)}')
    if not x: _rej(p, 'empty string')
    if enum is not None and x not in enum: _rej(p, f'{x!r} not one of {list(enum)}')
    if pattern is not None and not re.fullmatch(pattern, x): _rej(p, f'{x!r} does not match {pattern}')
    return x
def v_instance(x, p, cls):
    v_present(x, p)
    if not isinstance(x, cls): _rej(p, f'expected {cls.__name__}, got {_tn(x)}')
    return x
def v_list(x, p, item, min_len=0, max_len=None):
    v_present(x, p)
    if type(x) is not list: _rej(p, f'expected list, got {_tn(x)}' + (' (an empty string is not a list)' if x == '' else ''))
    if len(x) < min_len: _rej(p, f'{len(x)} element(s), at least {min_len} required')
    if max_len is not None and len(x) > max_len: _rej(p, f'{len(x)} element(s), at most {max_len} permitted')
    return [item(e, f'{p}[{i}]') for i, e in enumerate(x)]
def v_record(x, p, fields: dict, optional=frozenset()):
    v_present(x, p)
    if type(x) is not dict: _rej(p, f'expected record (dict), got {_tn(x)}')
    if any(type(k) is not str for k in x): _rej(p, 'non-string field name')
    missing = sorted(set(fields) - set(x) - set(optional)); extra = sorted(set(x) - set(fields))
    if missing: _rej(p, f'missing field(s) {missing}')
    if extra: _rej(p, f'unknown field(s) {extra}')
    return {k: fields[k](x[k], f'{p}.{k}') for k in x}
def v_interval(x, p, lo, hi):
    v_present(x, p)
    if type(x) not in (list, tuple) or len(x) != 2: _rej(p, 'expected a 2-element [a, b) pair (list or tuple)')
    a = v_int(x[0], f'{p}[0]', lo, hi); b = v_int(x[1], f'{p}[1]', lo, hi)
    if b <= a: _rej(p, f'empty or reversed interval [{a},{b})')
    return (a, b)
def v_strictly_increasing_intervals(iv, p):
    for i in range(1, len(iv)):
        if iv[i][0] < iv[i - 1][1]:
            _rej(f'{p}[{i}]', f'[{iv[i][0]},{iv[i][1]}) duplicates, overlaps or precedes [{iv[i - 1][0]},{iv[i - 1][1]}) (intervals must be strictly increasing and non-overlapping)')
    return iv
def v_intervals(x, p, lo, hi, min_len=0):
    return v_strictly_increasing_intervals(v_list(x, p, lambda e, q: v_interval(e, q, lo, hi), min_len), p)
def v_strictly_increasing(values, p):
    for i in range(1, len(values)):
        if not values[i] > values[i - 1]: _rej(f'{p}[{i}]', f'{values[i]!r} does not strictly exceed {values[i - 1]!r} (duplicate or out of order)')
    return values
ID_PATTERN = r'[A-Za-z0-9._:-]{1,128}'
def v_id(x, p): return v_str(x, p, pattern=ID_PATTERN)
def v_hex64(x, p): return v_str(x, p, pattern=r'[0-9a-f]{64}')

class SemanticRejected(Exception):
    """v10: the input has a valid shape but violates a semantic precondition (temporal order, fixture-variant range, frozen-artifact binding)."""
    def __init__(self, path: str, msg: str):
        super().__init__(f'{path}: {msg}'); self.path, self.msg = path, msg
def _srej(p, msg): raise SemanticRejected(p, msg)
def _canon_sha(obj) -> str: return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
def fixture_registry_digest() -> str: return _canon_sha({'tokens': FIXTURE_TOKENS, 'separators': TOKEN_SEPARATOR, 'inputs': INPUTS, 'workload': WORKLOAD})
def log_policy_digest() -> str:
    return _canon_sha({'logLimitBytes': LOG_LIMIT_BYTES, 'peerMaxBytes': PEER_MAX_BYTES, 'logStableReads': LOG_STABLE_READS, 'logStableGapMs': LOG_STABLE_GAP_MS,
                       'preOriginReadMaxBytes': PRE_ORIGIN_READ_MAX, 'utf8ReplacementExpansion': UTF8_REPLACEMENT_EXPANSION})
FROZEN_SCHEMA_PATH = Path(__file__).resolve().parent / 'e2-g1e-outcome.schema.json'
FROZEN = {'e1_sha256': E1_BOUND_SHA256, 'fixture_registry_sha256': '5dc1c12cfecd8614c8edd5817a332e09ed89f1fba59625adda3681bf6ef71cf4', 'log_policy_sha256': 'e728240deb7fbed3fc391c0f16686201ce58a7ae457738d0260445f7f156e6d4', 'schema_sha256': 'df5810c4991e6f8b15157e85b570cec64f57d4eab79f6e7e0c34646849ea014e'}
def s_frozen_current(key: str, path: str):
    cur = {'fixture_registry_sha256': fixture_registry_digest, 'log_policy_sha256': log_policy_digest}[key]()
    if cur != FROZEN[key]: _srej(path, f'the in-force {key[:-7]} hashes to {cur[:12]}..., not the frozen {FROZEN[key][:12]}...')
def s_binds(value: str, key: str, path: str):
    if value != FROZEN[key]: _srej(path, f'receipt bound to {value[:12]}..., not the frozen {key[:-7]} {FROZEN[key][:12]}...')
def s_order(pairs, path: str, strict: bool = True):
    for (n1, v1), (n2, v2) in zip(pairs, pairs[1:]):
        if (v2 <= v1) if strict else (v2 < v1): _srej(path, f'{n2} = {v2} must {"follow" if strict else "not precede"} {n1} = {v1}')
def load_frozen_schema() -> dict:
    """The registered outcome schema, read from its frozen path and accepted only if its bytes hash to FROZEN['schema_sha256']."""
    try: raw = Path(FROZEN_SCHEMA_PATH).read_bytes()
    except OSError as e: _srej('schema', f'frozen schema not readable at {FROZEN_SCHEMA_PATH}: {type(e).__name__}')
    got = hashlib.sha256(raw).hexdigest()
    if got != FROZEN['schema_sha256']: _srej('schema', f'schema file hashes to {got[:12]}..., not the frozen {FROZEN["schema_sha256"][:12]}... (a substituted or weakened schema is refused)')
    return json.loads(raw)

CHECKERS: dict = {}
def checker(contract, semantic=None, invalid_result=None):
    """Register a reference checker whose every call is first validated by `contract` (shape) and then by `semantic` (v10; see the module comment above)."""
    def deco(fn):
        sig = inspect.signature(fn)
        def bad(kind, why): return invalid_result(kind, why) if invalid_result else Verdict(False, why, kind)
        def wrapper(*args, **kwargs):
            try: bound = sig.bind_partial(*args, **kwargs)
            except TypeError as e: return bad('invalid-input', f'INVALID INPUT arguments: {e}')
            a = {nm: (bound.arguments[nm] if nm in bound.arguments else (prm.default if prm.default is not inspect.Parameter.empty else MISSING))
                 for nm, prm in sig.parameters.items()}
            try: clean = contract(a)
            except InputRejected as e: return bad('invalid-input', f'INVALID INPUT {e.path}: {e.msg}')
            except SemanticRejected as e: return bad('invalid-semantic', f'INVALID (semantic) {e.path}: {e.msg}')
            except Exception as e: return bad('invalid-input', f'INVALID INPUT (unvalidatable shape): {type(e).__name__}: {e}')
            if semantic is not None:
                try: semantic(clean)
                except SemanticRejected as e: return bad('invalid-semantic', f'INVALID (semantic) {e.path}: {e.msg}')
                except InputRejected as e: return bad('invalid-input', f'INVALID INPUT {e.path}: {e.msg}')
                except Exception as e: return bad('invalid-semantic', f'INVALID (semantic, unvalidatable): {type(e).__name__}: {e}')
            try: r = fn(**clean)
            except Exception as e: return bad('checker-error', f'CHECKER ERROR {type(e).__name__}: {e}')
            return Verdict(*r) if type(r) is tuple else r
        wrapper.__name__ = fn.__name__; wrapper.__doc__ = fn.__doc__; wrapper.contract = contract; wrapper.semantic = semantic; wrapper.signature = sig; wrapper.invalid_result = invalid_result
        CHECKERS[fn.__name__] = wrapper
        return wrapper
    return deco

def result_kind(r) -> str:
    return r.kind if isinstance(r, Verdict) else (r.get('kind', 'pass' if r.get('valid') else 'fail') if isinstance(r, dict) else 'not-a-result')
def result_ok(r) -> bool:
    return bool(r.ok) if isinstance(r, Verdict) else (bool(r.get('valid')) if isinstance(r, dict) else False)

def finite(x) -> bool: return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

def margin_for(spec: dict, python_value: float) -> float:
    """The margin of a registered row for a pair whose Python value is `python_value`: the absolute value, or max(floor, share x python) for a relative row (the one shared definition)."""
    m = spec['margin']
    return m['value'] if m['mode'] == 'absolute' else max(m['floor'], m['value'] * python_value)

def excess(spec: dict, p: dict) -> float:
    """Per-pair excess d' for a registered row (spec = registry row) given a pair {python, rust}."""
    if spec['source'] == 'rust-value': return p['rust'] - spec['margin']['value']
    return p['rust'] - p['python'] - margin_for(spec, p['python'])

def pair_ok(spec_source: str, p: dict, forced_invalid: bool) -> bool:
    if forced_invalid or not p['valid']: return False
    if spec_source == 'rust-value': return finite(p['rust'])
    return finite(p['rust']) and finite(p['python'])

def _median(v: list[float]) -> float:
    v = sorted(v); n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

def row_counts(spec: dict, row: dict, bad_pairs: set[int]) -> dict:
    ok = [pair_ok(spec['source'], p, p['repeat'] in bad_pairs) for p in row['pairs']]
    out = {'validPairs': sum(ok)}
    if spec['kind'] == 'beat':
        valid = [p for p, o in zip(row['pairs'], ok) if o]
        ds = [p['rust'] - p['python'] for p in valid]
        es = [excess(spec, p) for p in valid]                                      # the pair's own margin (V18); `improved` below ignores it
        out['exceedCount'] = sum(e > 0 for e in es)
        out['notImprovedCount'] = sum(d >= 0 for d in ds)
        out['medianExcess'] = _median(es) if len(es) == N_PAIRS else None
    else:
        es = [excess(spec, p) for p, o in zip(row['pairs'], ok) if o]
        out['exceedCount'] = sum(e > 0 for e in es)
        out['medianExcess'] = _median(es) if len(es) == N_PAIRS else None
    return out

def status_from_counts(kind: str, n: int, e: int, ni: int | None = None, noisy: bool = False) -> str:
    """The row status. A row that is not noisy keeps v10's rule. A NOISY row (v14-r2 V11 item 2, V12; guard g = 8): n < 10 -> unresolved; E = 10 -> adverse; E = 9 -> unresolved; otherwise `noisy`,
    except the beat row, which is `improved` iff all ten differences are below zero (`ni` = #{d_r >= 0} = 0) and `noisy` if not."""
    if n < N_PAIRS: return 'unresolved'
    if e == N_PAIRS: return 'adverse'
    if noisy:
        if e > NOISY_GUARD: return 'unresolved'
        return ('improved' if ni == 0 else 'noisy') if kind == 'beat' else 'noisy'
    if kind == 'beat': return 'improved' if ni <= PARITY_MAX_EXCEED else 'not_improved'
    return 'pass' if e <= PARITY_MAX_EXCEED else 'unresolved'

def aggregate(statuses: list[str]) -> str:
    """v10's aggregation over the rows of a class; v14-r2 V13: a `noisy` row is omitted (it can neither pass nor block)."""
    statuses = [s for s in statuses if s != 'noisy']
    if 'adverse' in statuses: return 'adverse'
    if 'unresolved' in statuses: return 'unresolved'
    return 'pass'

# ---- v14-r2 V11-V13 and V21: noisy rows reported with their error. Pure functions live HERE, not in `calibration.py`: `calibration.py` imports this module, so this module cannot import it ------------
NOISY_LABEL = 'noisy (reported with error, not decision-bearing)'
NOISY_GUARD = 8                   # g (USER DECISION 2): E = 9 on a noisy row is `unresolved`, E <= 8 is `noisy`
BAND_LOW_RANK, BAND_HIGH_RANK = 2, 9      # the matrix error band is [v(2), v(9)] of ten sorted values
BAND_TAIL = 11 / 1024             # P(Bin(10, 1/2) <= 1): one tail of the band
BAND_TAIL_NARROW = 56 / 1024      # P(Bin(10, 1/2) <= 2): one tail of the rejected narrower band [v(3), v(8)]
BAND_COVERAGE = 1 - 22 / 1024     # nominal coverage of [v(2), v(9)] (V11 item 5)
CONTINUE_SCOPE = ('CONTINUE means at most that no performance regression was found on the decision-bearing rows, together with the C1 result, given the registered correctness and baseline checks it also '
                  'requires. It says nothing about maintainability or any other non-performance value.')
KILL_REAP_NOTE = ('A faster Rust kill/reap may come from an implementation choice, not from the language: the Python backend scans the whole host process table with psutil on every kill '
                  '(`terminals.py kill()` → `_descendant_pids`), a step that Python could also remove or bound. It is not evidence that the language is faster.')
NARROWED_CONTINUE = ('CONTINUE under v14-r2 says that no resolvable row shows a regression beyond its margin at the registered level, that no row (noisy or not) showed all ten pairs beyond margin, '
                     'that C1 is `improved` under rule (b), and that the {k} noisy rows were measured and reported with their error but neither certified nor refuted.')
NOISY_LIMITATION_PREFIX = 'noisy rows: k ='
PAIRED_IDS = [r['id'] for r in REG if r['source'] == 'paired-delta']      # the 37 rows that have a Python value: the only rows that can be noisy or uncalibrated
SPEC_BY_ID = {r['id']: r for r in REG}
C1_ID = next(r['id'] for r in REG if r['kind'] == 'beat')

def noisy_options(calibration: str) -> dict:
    """The options record the document and F0 carry: constants after the user's decisions (C1 option (b), guard 8, no cap); 'n/a' when there is no calibration (no row can be noisy)."""
    if calibration not in CALIBRATION_MODES: raise ValueError(f'unknown calibration mode {calibration!r}')
    return {'c1Noisy': 'n/a', 'noisyGuard': 'n/a'} if calibration == 'none' else {'c1Noisy': 'b', 'noisyGuard': NOISY_GUARD}

def band(values: list) -> list | None:
    """[v(2), v(9)] of ten values (None unless there are exactly ten): the closed interval between the second smallest and the second largest."""
    if len(values) != N_PAIRS: return None
    v = sorted(values); return [v[BAND_LOW_RANK - 1], v[BAND_HIGH_RANK - 1]]

def raw_range(values: list) -> list | None:
    if len(values) != N_PAIRS: return None
    v = sorted(values); return [v[0], v[-1]]

def band_reading(excess_band: list | None) -> str | None:
    """A description, not a verdict: `within` if the upper end of the excess band is below 0, `beyond` if the lower end is above 0, otherwise `straddles`."""
    if excess_band is None: return None
    if excess_band[1] < 0: return 'within'
    if excess_band[0] > 0: return 'beyond'
    return 'straddles'

def matrix_python_med_abs(pythons: list) -> float | None:
    """Median of the 45 unordered |Python_i - Python_j| of the matrix's ten Python values (a stationarity cross-check, report-only)."""
    if len(pythons) != N_PAIRS: return None
    return _median([abs(a - b) for a, b in itertools.combinations(pythons, 2)])

def mean10(values: list) -> float | None:
    """Sum in repeat order divided by 10 (None unless there are exactly ten values). Report-only."""
    if len(values) != N_PAIRS: return None
    total = 0.0
    for v in values: total += v
    return total / N_PAIRS

def sd10(values: list) -> float | None:
    """Sample SD, two-pass: the mean first, then sqrt(sum((v - mean)^2) / 9) (None unless there are exactly ten values). Report-only."""
    m = mean10(values)
    if m is None: return None
    total = 0.0
    for v in values: total += (v - m) ** 2
    return math.sqrt(total / (N_PAIRS - 1))

MATRIX_KEYS = ('d', 'dExcess', 'medianD', 'medianExcess', 'bandD', 'bandExcess', 'rawRange', 'meanD', 'sdD', 'pythonMean', 'pythonSd', 'rustMean', 'rustSd', 'matrixPythonMedAbs')

def matrix_block(spec: dict, row: dict, bad_pairs=frozenset()) -> dict:
    """The matrix result of a paired row (V11 item 4), from its pairs only: counts, the ten d_r and d'_r in repeat order, their medians, the bands [v(2), v(9)], the raw range, the spread statistics
    and `matrixPythonMedAbs`. With fewer than ten valid pairs every field but the two counts is None."""
    bad_pairs = set(bad_pairs); counts = row_counts(spec, row, bad_pairs)
    valid = [p for p in row['pairs'] if pair_ok(spec['source'], p, p['repeat'] in bad_pairs)]
    out = {'validPairs': counts['validPairs'], 'exceedCount': counts['exceedCount']}
    if len(valid) != N_PAIRS: return {**out, **{k: None for k in MATRIX_KEYS}}
    d = [p['rust'] - p['python'] for p in valid]; de = [excess(spec, p) for p in valid]; py = [p['python'] for p in valid]; ru = [p['rust'] for p in valid]
    return {**out, 'd': d, 'dExcess': de, 'medianD': _median(d), 'medianExcess': _median(de), 'bandD': band(d), 'bandExcess': band(de), 'rawRange': raw_range(d), 'meanD': mean10(d), 'sdD': sd10(d),
            'pythonMean': mean10(py), 'pythonSd': sd10(py), 'rustMean': mean10(ru), 'rustSd': sd10(ru), 'matrixPythonMedAbs': matrix_python_med_abs(py)}

def noise_block(cal: dict) -> dict:
    """The calibration noise of a noisy row as the document records it, from a row of the calibration report (`calibration.row_calibration`): qHat, the old 90-pair figure (report-only), the ten chain values,
    the median and the nearest-rank 90th percentile of the adjacent |differences|, the margin at the median value and its ratio to the median |difference| (None if that is 0 or absent)."""
    med = cal['calMedAbs']; mg = cal['marginAtMedian']
    return {'qHat': cal['qHat'], 'qHatAllPairs': cal['qHatAllPairs'], 'calValues': list(cal['calValues']), 'calMedAbs': med, 'calP90Abs': cal['calP90Abs'], 'marginAtMedian': mg,
            'marginOverCalMedAbs': None if mg is None or not med else mg / med}

def example_noise(spec: dict) -> dict:
    """Synthetic calibration noise for the EXAMPLE documents only (a production document takes the numbers from the receipts, `decider.noisy_calibration_problems`)."""
    values = [100.0 + 10.0 * (i % 2) for i in range(10)]
    return noise_block({'qHat': 2 / 18, 'qHatAllPairs': 0.2, 'calValues': values, 'calMedAbs': 10.0, 'calP90Abs': 10.0, 'marginAtMedian': margin_for(spec, 105.0)})

def noisy_ids(doc: dict) -> list[str]:
    """The frozen noisy rows of the document, in registry order."""
    listed = set(doc['preregistration']['calibration']['noisyRows'])
    return [rid for rid in PAIRED_IDS if rid in listed]

def noisy_limitation(ids: list[str]) -> str:
    """The limitation entry the schema requires when `noisyRows` is non-empty: it names k and the list and states the narrowed CONTINUE (V13)."""
    return f'{NOISY_LIMITATION_PREFIX} {len(ids)} ({", ".join(ids)}). ' + NARROWED_CONTINUE.format(k=len(ids))

def build_reports(doc: dict, strict: bool = False) -> tuple[list, dict, dict]:
    """(noisyReport, c1Report, interpretation) derived from the document's pairs and its frozen noisy list. The calibration noise of each noisy row cannot be recomputed here (it needs the Python
    receipts: `decider.noisy_calibration_problems` compares it); it is taken from the document's own entry. `strict=False` (building) supplies the example noise for an entry that has none;
    `strict=True` (checking) requires the entry and that its ratio is consistent."""
    bad = bad_slot_pairs(doc); given = {e['id']: e for e in doc.get('noisyReport', []) if isinstance(e, dict) and 'id' in e}
    noisy = []
    for rid in noisy_ids(doc):
        spec = SPEC_BY_ID[rid]; cal = given.get(rid, {}).get('calibration')
        if cal is None:
            if strict: raise ValueError(f'{rid}: noisyReport holds no calibration noise for a noisy row')
            cal = example_noise(spec)
        elif strict and noise_block(cal) != cal: raise ValueError(f'{rid}: calibration noise is not self-consistent (marginOverCalMedAbs differs from marginAtMedian / calMedAbs)')
        block = matrix_block(spec, doc['rows'][rid], bad[spec['cell']])
        noisy.append({'id': rid, 'label': NOISY_LABEL, 'calibration': cal, 'matrix': block, 'bandReading': band_reading(block['bandExcess'])})
    spec = SPEC_BY_ID[C1_ID]; block = matrix_block(spec, doc['rows'][C1_ID], bad[spec['cell']]); block.pop('matrixPythonMedAbs')
    c1 = {'id': C1_ID, 'status': doc['rows'][C1_ID]['status'], **block}
    interpretation = {'continueScope': CONTINUE_SCOPE}
    if c1['validPairs'] == N_PAIRS and c1['medianD'] < 0: interpretation['killReap'] = KILL_REAP_NOTE
    return noisy, c1, interpretation

def bad_slot_pairs(doc: dict) -> dict[int, set[int]]:
    """cell -> repeats whose arm trial is invalid or missing (such a pair can never be valid)."""
    bad = {c: set() for c in CELLS}
    for t in doc['matrix']['evidenceInvalidTrials'] + doc['matrix']['missingSlots']:
        arm, cell, rep = t.split(':'); bad[int(cell)].add(int(rep))
    return bad

# ---- schedule, design, independence ------------------------------------------------------------------------------------------
def _shuffle(items: list, seed: str, label: str) -> list:
    items = list(items); i = len(items) - 1; counter = 0
    while i > 0:
        j = int.from_bytes(hashlib.sha256(f'{seed}|{label}|{counter}'.encode()).digest()[:8], 'big') % (i + 1)
        items[i], items[j] = items[j], items[i]; i -= 1; counter += 1
    return items

def make_schedule(seed: str) -> list[dict]:
    """Frozen randomised, counterbalanced schedule: 20 pair-slots (cell, repeat) in seeded-shuffled order; per cell exactly 5 repeats Python-first
    and 5 Rust-first (seeded-shuffled). Both arms of a pair run back to back, first arm as listed."""
    first = {c: _shuffle(['python'] * 5 + ['rust-shell'] * 5, seed, f'first{c}') for c in CELLS}
    order = _shuffle([(c, r) for c in CELLS for r in range(N_PAIRS)], seed, 'order')
    return [{'slot': s, 'cell': c, 'repeat': r, 'firstArm': first[c][r]} for s, (c, r) in enumerate(order)]

def schedule_sha(schedule: list[dict]) -> str:
    return hashlib.sha256(json.dumps(schedule, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def design_compliant(doc: dict) -> bool:
    d = doc['design']
    try:
        if d['schedule'] != make_schedule(d['scheduleSeed']): return False
        if d['scheduleSha256'] != schedule_sha(d['schedule']): return False
        for c in CELLS:
            if sum(1 for s in d['schedule'] if s['cell'] == c and s['firstArm'] == 'python') != 5: return False
        ids = d['backendIdentities']
        expected_trials = {f'{a}:{c}:{r}' for a in ARMS for c in CELLS for r in range(N_PAIRS)} - set(doc['matrix']['missingSlots'])
        if {i['trial'] for i in ids} != expected_trials or len(ids) != len(expected_trials): return False
        if len({(i['pid'], i['start']) for i in ids}) != len(ids): return False   # a fresh backend process per attempt
    except Exception:
        return False
    return True

def _mahonian(n: int) -> list[int]:
    dist = [1]
    for k in range(2, n + 1):
        new = [0] * (len(dist) + k - 1)
        for i, v in enumerate(dist):
            for j in range(k): new[i + j] += v
        dist = new
    return dist
MAHONIAN10 = _mahonian(10); assert sum(MAHONIAN10) == math.factorial(10)
def kendall_s(x: list[float]) -> int:
    return sum((x[j] > x[i]) - (x[j] < x[i]) for i in range(len(x)) for j in range(i + 1, len(x)))
def kendall_p_two_sided(s: int) -> float:
    i = math.floor((45 - abs(s)) / 2)            # inversions <= i  <=>  S >= |s|; with ties the no-tie table is used (approximate)
    return min(1.0, 2 * sum(MAHONIAN10[:i + 1]) / math.factorial(10))
TRIP_ALPHA = 0.005
S_TRIP = min(s for s in range(0, 46) if kendall_p_two_sided(s) <= TRIP_ALPHA)

def canary_trip(c: dict) -> bool | None:
    v = c['values']
    if len(v) != 10 or any(not finite(x) for x in v): return None   # missing values: the screen cannot run
    return abs(kendall_s(v)) >= S_TRIP

def independence_status(doc: dict) -> str:
    cs = doc['independence']['canaries']
    if [c['id'] for c in cs] != CANARY_IDS: return 'fail'
    for c in cs:
        t = canary_trip(c)
        if t is None or t: return 'fail'
    return 'pass'

# ---- derivation --------------------------------------------------------------------------------------------------------------
def reduce_correctness(rows: dict, reader_required: bool) -> str:
    rank = {'ok': 0, 'missing': 1, 'failed_other': 2, 'failed_rust_bounded': 3, 'failed_rust_unbounded': 4}
    worst = 'ok'
    for cid in CORRECTNESS_IDS:
        r = rows[cid]; s = r['status']
        if cid == OPTIONAL_CORRECTNESS and not reader_required:
            level = 'ok' if s == 'not-required' else 'missing'
        elif s == 'pass-current': level = 'ok' if 'receipt' in r else 'missing'
        elif s == 'failed':
            if r.get('attribution') == 'other': level = 'failed_other'
            elif r.get('attribution') == 'rust-design' and r.get('boundedCorrectivePath') is True: level = 'failed_rust_bounded'
            else: level = 'failed_rust_unbounded'
        else: level = 'missing'
        if rank[level] > rank[worst]: worst = level
    return worst

def reader_spec(rid: str) -> dict:
    return {'id': rid, 'kind': 'parity', 'source': 'paired-delta', 'margin': None}

def reader_row_counts(row: dict, bad: set[int]) -> dict:
    ok = [pair_ok('paired-delta', p, False) for p in row['pairs']]
    es = [p['rust'] - p['python'] - row['margin']['value'] for p, o in zip(row['pairs'], ok) if o]
    return {'validPairs': sum(ok), 'exceedCount': sum(e > 0 for e in es)}

def reader_status(doc: dict) -> str:
    rb = doc['readerBenchmark']; opts = doc['preregistration']['options']
    if not opts['readerRequired']: return 'not_required'
    m0 = rb['m0']
    if not all(m0[fx][r] for fx in ('claude', 'opencode') for r in ('python', 'rust')): return 'not_run'
    if set(rb['rows']) != set(READER_ROWS) or len(rb['fixtures']) < 2 or rb['goldenEvents'] is None or rb['calibrationRuns'] < 10 or rb['runs'] < 10: return 'not_run'
    sts = []
    for rid, row in rb['rows'].items():
        c = reader_row_counts(row, set())
        if (row['validPairs'], row['exceedCount']) != (c['validPairs'], c['exceedCount']): raise ValueError(f'{rid}: declared counts differ from the pairs')
        st = status_from_counts('parity', c['validPairs'], c['exceedCount'])
        if row['status'] != st: raise ValueError(f'{rid}: reader row status differs from derived')
        sts.append(st)
    agg = aggregate(sts)
    return {'pass': 'pass', 'adverse': 'worse', 'unresolved': 'unresolved'}[agg]

def derive_rows(doc: dict) -> dict[str, str]:
    reg = REG
    rows = doc['rows']
    if set(rows) != {r['id'] for r in reg}: raise ValueError('row id set differs from the registry')
    bad = bad_slot_pairs(doc)
    listed = doc['preregistration']['calibration']['noisyRows']
    if not set(listed) <= set(PAIRED_IDS): raise ValueError('noisyRows lists a row that is not a paired row (a one-arm budget row can never be noisy)')
    if listed != noisy_ids(doc): raise ValueError('noisyRows is not the frozen list in registry order')
    noisy = set(listed)
    st = {}
    for spec in reg:
        row = rows[spec['id']]
        if row['margin'] != {**spec['margin'], 'basis': spec['basis']}: raise ValueError(f'{spec["id"]}: margin differs from the registered one')
        for k in ('kind', 'metric', 'cell', 'unit', 'source', 'class'):
            if row[k] != (spec['cls'] if k == 'class' else spec[k]): raise ValueError(f'{spec["id"]}: {k} differs from the registry')
        c = row_counts(spec, row, bad[spec['cell']])
        for k, v in c.items():
            if k == 'medianExcess':
                if (v is None) != (row['medianExcess'] is None) or (v is not None and abs(v - row['medianExcess']) > 1e-9): raise ValueError(f'{spec["id"]}: medianExcess differs from the pairs')
            elif row[k] != v: raise ValueError(f'{spec["id"]}: declared {k} {row[k]} != derived {v}')
        s = status_from_counts(spec['kind'], c['validPairs'], c['exceedCount'], c.get('notImprovedCount'), spec['id'] in noisy)
        if row['status'] != s: raise ValueError(f'{spec["id"]}: status {row["status"]} != derived {s}')
        st[spec['id']] = s
    return st

def derive_matrix_complete(doc: dict) -> bool:
    m = doc['matrix']
    trials = m['evidenceInvalidTrials'] + m['missingSlots']
    if len(set(trials)) != len(trials): raise ValueError('a trial is listed twice')
    if m['accepted'] != 40 - len(trials): raise ValueError('accepted != 40 - invalid - missing')
    return not trials and not m['integrityFailures'] and m['accepted'] == 40

CALIBRATION_ATTEMPTS = 20   # scored Python-only calibration attempts (two chains of ten); with the one discarded warm-up (v14-r2 V19) a complete calibration lists 21 receipts; fewer = calibration did not complete
CALIBRATION_RECEIPTS = CALIBRATION_ATTEMPTS + 1

def derivation_path(doc: dict) -> str:
    return doc['derivation']['path']

def carried_stop_codes(doc: dict) -> list[str]:
    """The stop-rule codes (S1..S6) carried into this decision from earlier runs (V7 item 5), in canonical order."""
    held = {c for run in doc['derivation']['earlierRuns'] for c in run['stopFacts']}
    return [c for c in STOP_RULES if c in held]

def calibration_fact(doc: dict) -> str:
    cal = doc['preregistration']['calibration']; mode = doc['preregistration']['options']['calibration']
    if mode == 'none': return 'not_run'
    if derivation_path(doc) == 'no-matrix' and len(cal['receipts']) < CALIBRATION_RECEIPTS: return ND   # calibration did not complete (V10 item 5(b))
    if cal['uncalibratedRows']: return 'uncalibrated'      # v14-r2 V19: a row without nine valid adjacent pairs cannot be judged, the outcome is then INCONCLUSIVE (I4)
    return 'noisy_listed' if cal['noisyRows'] else 'clear'   # a noisy row is listed here and reported with its error; it is not an I-reason (v14-r2 V13); its row status is `status_from_counts`

def derive(doc: dict) -> dict:
    reg = REG
    st = derive_rows(doc)
    cls = lambda c: [st[r['id']] for r in reg if r['cls'] in c]
    req = doc['preregistration']['options']['readerRequired']
    rs = reader_status(doc)
    if doc['readerBenchmark']['status'] != rs: raise ValueError('readerBenchmark.status differs from derived')
    nm = derivation_path(doc) == 'no-matrix'
    complete = derive_matrix_complete(doc)
    if doc['matrix']['complete'] != complete: raise ValueError('matrix.complete differs from the lists')
    if nm and (doc['matrix']['accepted'] != 0 or complete): raise ValueError('a no-matrix document cannot hold an accepted matrix attempt')
    for section in ('baselineIdentity', 'locality', 'maintainability'):
        if doc[section] is None and not nm: raise ValueError(f'{section} is absent on the matrix path')
    facts = {
        'matrixComplete': complete, 'designCompliant': design_compliant(doc), 'independenceScreen': independence_status(doc),
        'calibrationRows': calibration_fact(doc), 'baselineIdentity': ND if doc['baselineIdentity'] is None else doc['baselineIdentity']['status'],
        'survivorsRustArm': doc['survivors']['rustArm'], 'isolationBreach': doc['isolation']['breach'],
        'correctness': reduce_correctness(doc['correctness'], req), 'claudeObservation': doc['claudeObservation']['state'],
        'streamStatus': aggregate(cls({'stream'})), 'budgetStatus': aggregate(cls({'lifecycle', 'budget'})),
        'c1Status': cls({'beat'})[0], 'readerBenchmark': rs,
        'localityViolation': ND if doc['locality'] is None else doc['locality']['violation'],
        'maintainabilityComplete': ND if doc['maintainability'] is None else doc['maintainability']['complete']}
    if nm:   # facts that need matrix pairs have no value without a matrix: explicit `not_derivable`, never read as favourable
        for k in ('designCompliant', 'independenceScreen', 'streamStatus', 'budgetStatus', 'c1Status'): facts[k] = ND
    return facts

def validate_document(doc: dict, schema: dict) -> None:
    jsonschema.Draft202012Validator(schema).validate(doc)
    facts = derive(doc)
    if doc['decision']['facts'] != facts: raise ValueError('decision.facts differ from the derived facts')
    outcome, reasons = decide(facts, derivation_path(doc), carried_stop_codes(doc))
    if doc['outcome'] != outcome: raise ValueError(f'outcome {doc["outcome"]} != derived {outcome}')
    if [r['rule'] for r in doc['decision']['reasons']] != reasons: raise ValueError('reasons differ from derived reasons')
    # pair validity must be forced false for any invalid/missing slot (the schema cannot express this)
    bad = bad_slot_pairs(doc)
    for rid, row in doc['rows'].items():
        for p in row['pairs']:
            if p['repeat'] in bad[row['cell']] and p['valid']: raise ValueError(f'{rid}: pair {p["repeat"]} valid although its trial is invalid or missing')
    for c in doc['independence']['canaries']:
        t = canary_trip(c)
        if (c['S'] is None) != (t is None) or (t is not None and (c['S'] != kendall_s(c['values']) or c['trip'] != t)): raise ValueError(f'{c["id"]}: declared S/trip differ from the values')
    if doc['independence']['status'] != independence_status(doc): raise ValueError('independence.status differs from derived')
    if doc['design']['compliant'] != design_compliant(doc): raise ValueError('design.compliant differs from derived')
    # v14-r2: the noisy-row report, the C1 report and the interpretation clause are derived from the frozen data (never entered by hand); a difference raises. The calibration noise inside a
    # noisyReport entry needs the Python receipts: `decider.noisy_calibration_problems` compares it, here only its self-consistency is checked.
    noisy, c1, interpretation = build_reports(doc, strict=True)
    for name, want in (('noisyReport', noisy), ('c1Report', c1), ('interpretation', interpretation)):
        if doc[name] != want: raise ValueError(f'{name} differs from its derivation from the pairs and the frozen noisy list')
    want_limits = [noisy_limitation(noisy_ids(doc))] if noisy_ids(doc) else []
    if [t for t in doc['limitations'] if t.startswith(NOISY_LIMITATION_PREFIX)] != want_limits: raise ValueError('the limitation entry naming k and the noisy rows is missing, wrong or not required')

def c_document(a):
    """doc: dict; mode: 'example' | 'production'. No schema argument exists: the checker itself loads the frozen schema from its registered path, accepts it only
    if its bytes hash to the frozen SHA-256, and validates doc against it (JSON Schema 2020-12: closed key sets, exact row ids, constant margins, E1 hash constant)."""
    doc = v_present(a['doc'], 'doc')
    if type(doc) is not dict: _rej('doc', f'expected dict, got {_tn(doc)}')
    mode = v_str(a['mode'], 'mode', enum=('example', 'production'))
    schema = load_frozen_schema()
    err = next(iter(sorted(jsonschema.Draft202012Validator(schema).iter_errors(doc), key=lambda e: list(e.absolute_path).__repr__())), None)
    if err is not None: _rej('doc' + ''.join(f'[{x!r}]' for x in err.absolute_path), f'schema: {err.message[:160]}')
    return {'doc': doc, 'mode': mode}

def s_document(c):
    """Frozen-artifact binding: the schema used is the frozen file (path + SHA-256, checked in the contract); evaluated.e1ProtocolSha256 is the frozen E1 v8b
    hash; decision.tableVersion is this registration's table; mode binding: 'production' rejects a document carrying exampleOnly, 'example' requires
    exampleOnly = true (a synthetic illustration can never be scored as a result, and a result can never be relabelled an example)."""
    d = c['doc']
    s_binds(d['evaluated']['e1ProtocolSha256'], 'e1_sha256', 'doc.evaluated.e1ProtocolSha256')
    if d['decision']['tableVersion'] != TABLE_VERSION: _srej('doc.decision.tableVersion', f'{d["decision"]["tableVersion"]!r} is not {TABLE_VERSION!r}')
    if c['mode'] == 'production' and 'exampleOnly' in d: _srej('doc.exampleOnly', 'a synthetic example (exampleOnly) cannot be checked as a production result')
    if c['mode'] == 'example' and d.get('exampleOnly') is not True: _srej('doc.exampleOnly', 'example mode requires exampleOnly = true')

@checker(c_document, s_document)
def document_ok(doc, mode='example'):
    """Document-level checker: after the frozen-schema layer and the semantic stage, re-derives every row, fact, the outcome and the reasons (validate_document)."""
    try: validate_document(doc, load_frozen_schema())
    except Exception as e: return Verdict(False, f'derivation: {type(e).__name__}: {str(e)[:160]}')
    return Verdict(True, 'ok')

# ---- schema ------------------------------------------------------------------------------------------------------------------
HEX = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
NB = {'type': 'string', 'pattern': r'\S'}
NUMNULL = {'type': ['number', 'null']}
NUMNULL_NONNEG = {'type': ['number', 'null'], 'minimum': 0}   # v9: per-pair and canary measurements are latencies (ms), CPU (pt) or RSS (MiB): never negative
TRIAL = {'type': 'string', 'pattern': '^(python|rust-shell):(1|4):[0-9]$'}
def hashref(): return {'$ref': '#/$defs/hashRef'}
def ref(name): return {'$ref': f'#/$defs/{name}'}

def counts_binding(kind: str) -> list[dict]:
    """What the counts allow a status to be. The schema cannot see whether a row is noisy, so it allows the two possible statuses of a non-noisy and of a noisy row where they differ (v14-r2 V11 item 2,
    V12); the exact one is re-derived with the frozen list (`derive_rows`) and `noisy` is tied to the list by the document-level conditions."""
    if_ = lambda props, then: {'if': {'properties': props, 'required': list(props)}, 'then': {'properties': {'status': then if isinstance(then, dict) else {'const': then}}}}
    ten = {'validPairs': {'const': N_PAIRS}}
    out = [if_({'validPairs': {'maximum': N_PAIRS - 1}}, 'unresolved'),
           if_({**ten, 'exceedCount': {'const': N_PAIRS}}, 'adverse'),
           {'if': {'properties': {'status': {'const': 'noisy'}}, 'required': ['status']},       # `noisy` needs ten valid pairs and at most g = 8 exceeding
            'then': {'properties': {'validPairs': {'const': N_PAIRS}, 'exceedCount': {'maximum': NOISY_GUARD}}}}]
    if kind == 'beat':
        out += [if_({**ten, 'exceedCount': {'maximum': NOISY_GUARD}, 'notImprovedCount': {'const': 0}}, 'improved'),
                if_({**ten, 'exceedCount': {'maximum': NOISY_GUARD}, 'notImprovedCount': {'const': 1}}, {'enum': ['improved', 'noisy']}),
                if_({**ten, 'exceedCount': {'maximum': NOISY_GUARD}, 'notImprovedCount': {'minimum': 2}}, {'enum': ['not_improved', 'noisy']}),
                if_({**ten, 'exceedCount': {'const': N_PAIRS - 1}}, {'enum': ['not_improved', 'unresolved']})]
    else:
        out += [if_({**ten, 'exceedCount': {'maximum': PARITY_MAX_EXCEED}}, {'enum': ['pass', 'noisy']}),
                if_({**ten, 'exceedCount': {'minimum': PARITY_MAX_EXCEED + 1, 'maximum': NOISY_GUARD}}, {'enum': ['unresolved', 'noisy']}),
                if_({**ten, 'exceedCount': {'const': N_PAIRS - 1}}, 'unresolved')]
    return out

PAIRS_SCHEMA = {'type': 'array', 'minItems': N_PAIRS, 'maxItems': N_PAIRS, 'prefixItems': [ref(f'pair{i}') for i in range(N_PAIRS)], 'items': False}

def row_base(kind: str) -> dict:
    props = {'id': NB, 'class': NB, 'metric': NB, 'cell': {'enum': list(CELLS)}, 'kind': NB, 'unit': NB, 'source': NB,
             'margin': {'type': 'object'}, 'pairs': PAIRS_SCHEMA, 'validPairs': {'type': 'integer', 'minimum': 0, 'maximum': N_PAIRS},
             'exceedCount': {'type': 'integer', 'minimum': 0, 'maximum': N_PAIRS}, 'medianExcess': NUMNULL,
             'status': {'enum': ['improved', 'not_improved', 'adverse', 'unresolved', 'noisy'] if kind == 'beat' else ['pass', 'adverse', 'unresolved', 'noisy']}}
    req = ['id', 'class', 'metric', 'cell', 'kind', 'unit', 'source', 'margin', 'pairs', 'validPairs', 'exceedCount', 'medianExcess', 'status']
    if kind == 'beat': props['notImprovedCount'] = {'type': 'integer', 'minimum': 0, 'maximum': N_PAIRS}; req.append('notImprovedCount')
    return {'type': 'object', 'additionalProperties': False, 'required': req, 'properties': props, 'allOf': counts_binding(kind)}

def row_schema(r: dict) -> dict:
    margin = {**r['margin'], 'basis': r['basis']}
    props = {'id': {'const': r['id']}, 'class': {'const': r['cls']}, 'metric': {'const': r['metric']}, 'cell': {'const': r['cell']},
             'kind': {'const': r['kind']}, 'unit': {'const': r['unit']}, 'source': {'const': r['source']}, 'margin': {'const': margin}}
    if r['source'] != 'paired-delta': props['status'] = {'not': {'const': 'noisy'}}              # a one-arm budget row has no Python value, is not calibrated and can never be noisy
    return {'allOf': [ref('rowBeat' if r['kind'] == 'beat' else 'rowStd')], 'properties': props}

def noisy_report_schema() -> dict:
    """The closed key sets of v14-r2 V11 item 4 and V12 (`noisyReport`, `c1Report`, `interpretation`); every value is re-derived by `build_reports`."""
    arr = {'type': ['array', 'null'], 'minItems': N_PAIRS, 'maxItems': N_PAIRS, 'items': {'type': 'number'}}
    pair = {'type': ['array', 'null'], 'minItems': 2, 'maxItems': 2, 'items': {'type': 'number'}}
    count = {'type': 'integer', 'minimum': 0, 'maximum': N_PAIRS}
    block = {'validPairs': count, 'exceedCount': count, 'd': arr, 'dExcess': arr, 'medianD': NUMNULL, 'medianExcess': NUMNULL, 'bandD': pair, 'bandExcess': pair, 'rawRange': pair,
             'meanD': NUMNULL, 'sdD': NUMNULL, 'pythonMean': NUMNULL, 'pythonSd': NUMNULL, 'rustMean': NUMNULL, 'rustSd': NUMNULL}
    closed = lambda props: {'type': 'object', 'additionalProperties': False, 'required': list(props), 'properties': props}
    noise = closed({'qHat': {'type': 'number', 'minimum': 0, 'maximum': 1}, 'qHatAllPairs': NUMNULL, 'calValues': {'type': 'array', 'minItems': 10, 'maxItems': 10, 'items': NUMNULL},
                    'calMedAbs': NUMNULL, 'calP90Abs': NUMNULL, 'marginAtMedian': NUMNULL, 'marginOverCalMedAbs': NUMNULL})
    entry = closed({'id': {'enum': PAIRED_IDS}, 'label': {'const': NOISY_LABEL}, 'calibration': noise, 'matrix': closed({**block, 'matrixPythonMedAbs': NUMNULL}),
                    'bandReading': {'enum': ['within', 'beyond', 'straddles', None]}})
    c1 = closed({'id': {'const': C1_ID}, 'status': {'enum': ['improved', 'not_improved', 'adverse', 'unresolved', 'noisy']}, **block})
    interpretation = {'type': 'object', 'additionalProperties': False, 'required': ['continueScope'], 'properties': {'continueScope': {'const': CONTINUE_SCOPE}, 'killReap': {'const': KILL_REAP_NOTE}}}
    return {'noisyReport': {'type': 'array', 'uniqueItems': True, 'maxItems': len(PAIRED_IDS), 'items': entry}, 'c1Report': c1, 'interpretation': interpretation}

def reader_row_schema(rid: str) -> dict:
    unit = 'ms' if 'm1_' in rid else ('s' if 'm2_' in rid else 'MiB')
    return {'allOf': [ref('readerRow')], 'properties': {'id': {'const': rid}, 'unit': {'const': unit}}}

def correctness_schema() -> dict:
    def row(cid):
        return {'type': 'object', 'additionalProperties': False, 'required': ['status'],
                'properties': {'status': {'enum': ['pass-current', 'not-rerun', 'unknown', 'failed'] + (['not-required'] if cid == OPTIONAL_CORRECTNESS else [])},
                               'receipt': hashref(), 'attribution': {'enum': ['rust-design', 'other', 'unknown']}, 'boundedCorrectivePath': {'enum': [True, False, 'unknown']}, 'note': {'type': 'string'}},
                'allOf': [{'if': {'properties': {'status': {'const': 'pass-current'}}, 'required': ['status']}, 'then': {'required': ['receipt']}},
                          ]}   # a failed row may state attribution/bound as 'unknown' or leave them absent; both derive failed_rust_unbounded (STOP S3) unless attribution is 'other'
    return {'type': 'object', 'additionalProperties': False, 'required': CORRECTNESS_IDS, 'properties': {c: row(c) for c in CORRECTNESS_IDS}}

def build_schema() -> dict:
    base_ids = [r['id'] for r in REG]
    facts_props = {k: ({'enum': v + ([ND] if k in ND_FACTS else [])} if not isinstance(v[0], bool) else ({'enum': [True, False, ND]} if k in ND_FACTS else {'type': 'boolean'})) for k, v in FACT_DOMAINS.items()}
    baseline_full = {'type': 'object', 'additionalProperties': False, 'required': ['status', 'upstreamMerge', 'e3Parent', 'e3Commit', 'e3Set', 'pythonArmFileList', 'referenceManifest', 'changedFiles'],
                     'properties': {'status': {'enum': FACT_DOMAINS['baselineIdentity']}, 'upstreamMerge': {'type': 'string', 'pattern': '^[0-9a-f]{40}$'},
                                    'e3Parent': {'type': 'string', 'pattern': '^[0-9a-f]{40}$'}, 'e3Commit': {'type': 'string', 'pattern': '^[0-9a-f]{40}$'},
                                    'e3Set': {'type': 'array', 'uniqueItems': True, 'items': NB}, 'pythonArmFileList': hashref(), 'referenceManifest': hashref(),
                                    'changedFiles': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'loadedSha256', 'referenceSha256', 'diff', 'verdicts'],
                                                     'properties': {'path': NB, 'loadedSha256': {'type': 'string', 'pattern': '^([0-9a-f]{64}|absent)$'},
                                                                    'referenceSha256': {'type': 'string', 'pattern': '^([0-9a-f]{64}|absent)$'}, 'diff': hashref(),
                                                                    'verdicts': {'type': 'array', 'minItems': 2, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['reviewer', 'session', 'report', 'verdict', 'loadedSha256'],
                                                                                 'properties': {'reviewer': NB, 'session': NB, 'report': hashref(), 'verdict': {'enum': ['behaviour-neutral', 'other']}, 'loadedSha256': HEX}}}}}}},
                     'allOf': [{'if': {'properties': {'status': {'const': 'identical'}}, 'required': ['status']}, 'then': {'properties': {'changedFiles': {'maxItems': 0}}}},
                               {'if': {'properties': {'status': {'enum': ['refactor_reviewed', 'changed']}}, 'required': ['status']}, 'then': {'properties': {'changedFiles': {'minItems': 1}}}},
                               {'if': {'properties': {'status': {'const': 'refactor_reviewed'}}, 'required': ['status']},
                                'then': {'properties': {'changedFiles': {'items': {'properties': {'verdicts': {'items': {'properties': {'verdict': {'const': 'behaviour-neutral'}}}}}}}}}}]}
    nullable = lambda s: {'anyOf': [{'type': 'null'}, s]}
    run_ref = {'type': 'object', 'additionalProperties': False, 'required': ['manifestSha256', 'kind', 'stopReason', 'stopFacts'],
               'properties': {'manifestSha256': HEX, 'kind': {'enum': list(RUN_KINDS)}, 'stopReason': {'type': ['string', 'null']},
                              'stopFacts': {'type': 'array', 'uniqueItems': True, 'items': {'enum': list(STOP_RULES)}}}}
    gate = {'type': 'object', 'additionalProperties': False, 'required': ['open', 'missing'],
            'properties': {'open': {'type': 'boolean'}, 'missing': {'type': 'array', 'uniqueItems': True, 'items': NB}},
            'allOf': [{'if': {'properties': {'open': {'const': True}}, 'required': ['open']}, 'then': {'properties': {'missing': {'maxItems': 0}}}}]}
    authority = {'type': 'object', 'additionalProperties': False, 'required': ['recorded'],
                 'properties': {'recorded': {'type': 'boolean'}, 'evidence': ref('evidenceRef')},
                 'allOf': [{'if': {'properties': {'recorded': {'const': True}}, 'required': ['recorded']}, 'then': {'required': ['evidence']}}]}
    opt = lambda k, v: {'properties': {'preregistration': {'properties': {'options': {'properties': {k: v}}}}}}
    pair_defs = {f'pair{i}': {'type': 'object', 'additionalProperties': False, 'required': ['repeat', 'python', 'rust', 'valid'],
                              'properties': {'repeat': {'const': i}, 'python': NUMNULL_NONNEG, 'rust': NUMNULL_NONNEG, 'valid': {'type': 'boolean'}},
                              'allOf': [{'if': {'properties': {'valid': {'const': True}}, 'required': ['valid']}, 'then': {'properties': {'rust': {'type': 'number'}}}}]}
                 for i in range(N_PAIRS)}
    reader_row = {'type': 'object', 'additionalProperties': False, 'required': ['id', 'unit', 'margin', 'pairs', 'validPairs', 'exceedCount', 'status'],
                  'properties': {'id': NB, 'unit': NB,
                                 'margin': {'type': 'object', 'additionalProperties': False, 'required': ['mode', 'value', 'frozenIn'],
                                            'properties': {'mode': {'const': 'absolute'}, 'value': {'type': 'number', 'exclusiveMinimum': 0}, 'frozenIn': hashref()}},
                                 'pairs': PAIRS_SCHEMA, 'validPairs': {'type': 'integer', 'minimum': 0, 'maximum': N_PAIRS},
                                 'exceedCount': {'type': 'integer', 'minimum': 0, 'maximum': N_PAIRS}, 'status': {'enum': ['pass', 'adverse', 'unresolved']}},
                  'allOf': counts_binding('parity')}
    schema = {
        '$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': SCHEMA_ID,
        'title': 'G1-E outcome (ticket E4) — complete standalone schema v10; ticket-06 schema acr-g1-outcome/5 and schemas acr-g1e-outcome/1 (E2 v2), /2 (v3), /3 (v4), /4 (v5), /5 (v6), /6 (v7), /7 (v8) and /8 (v9) stay history',
        'description': 'Structure plus the per-pair values every status is derived from. A decider re-derives every count, status, fact and the outcome from the pairs and the lists and rejects any difference; gates are necessary, not sufficient. exampleOnly=true marks synthetic illustrations that a production decider rejects.',
        'type': 'object', 'additionalProperties': False,
        'required': ['schema', 'ticket', 'derivation', 'evaluated', 'preregistration', 'design', 'independence', 'matrix', 'survivors', 'isolation', 'claudeObservation',
                     'baselineIdentity', 'locality', 'maintainability', 'readerBenchmark', 'correctness', 'rows', 'reportOnly', 'exploratory', 'decision',
                     'outcome', 'limitations', 'receiptsManifest', 'jointReview', 'continuationAuthority', 'gates', 'gateSemantics', 'notAdoption', 'noisyReport', 'c1Report', 'interpretation'],
        'properties': {
            **noisy_report_schema(),
            'exampleOnly': {'const': True}, 'schema': {'const': SCHEMA_ID}, 'ticket': {'const': 'E4'},
            'derivation': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'earlierRuns'],
                           'properties': {'path': {'enum': list(PATHS)}, 'earlierRuns': {'type': 'array', 'items': run_ref}}},
            'evaluated': {'type': 'object', 'additionalProperties': False,
                          'required': ['head', 'branch', 'sidecarSha256', 'methodRevision', 'freezeSha256', 'e1ProtocolSha256', 'evidenceRevision', 'summarySha256', 'inputsSha256', 'receiptsManifestSha256'],
                          'properties': {'head': {'type': 'string', 'pattern': '^[0-9a-f]{40}$'}, 'branch': NB, 'sidecarSha256': HEX, 'methodRevision': NB, 'freezeSha256': HEX,
                                         'e1ProtocolSha256': {'const': E1_BOUND_SHA256}, 'evidenceRevision': NB, 'summarySha256': HEX, 'inputsSha256': HEX, 'receiptsManifestSha256': HEX}},
            'preregistration': {'type': 'object', 'additionalProperties': False, 'required': ['document', 'sha256', 'userApproval', 'options', 'calibration'],
                                'properties': {'document': NB, 'sha256': HEX, 'userApproval': ref('evidenceRef'),
                                               'options': {'type': 'object', 'additionalProperties': False, 'required': ['readerRequired', 'descendantSnapshot', 'calibration', 'pairsPerCell', 'c1Noisy', 'noisyGuard'],
                                                           'properties': {'readerRequired': {'type': 'boolean'}, 'descendantSnapshot': {'const': SNAPSHOT_OPTION},
                                                                          'calibration': {'enum': list(CALIBRATION_MODES)}, 'pairsPerCell': {'const': N_PAIRS},
                                                                          'c1Noisy': {'enum': ['b', 'n/a']}, 'noisyGuard': {'enum': [NOISY_GUARD, 'n/a']}}},     # v14-r2: constants after the user's decisions; NO noisyCap key exists
                                               'calibration': {'type': 'object', 'additionalProperties': False, 'required': ['receipts', 'noisyRows', 'uncalibratedRows'],
                                                               'properties': {'receipts': {'type': 'array', 'items': hashref()},
                                                                              'noisyRows': {'type': 'array', 'uniqueItems': True, 'items': {'enum': PAIRED_IDS}},
                                                                              'uncalibratedRows': {'type': 'array', 'uniqueItems': True, 'items': {'enum': [r['id'] for r in REG]}}}}}},
            'design': {'type': 'object', 'additionalProperties': False, 'required': ['scheduleSeed', 'schedule', 'scheduleSha256', 'backendIdentities', 'compliant', 'logPolicy'],
                       'properties': {'scheduleSeed': NB, 'scheduleSha256': HEX, 'compliant': {'type': 'boolean'},
                                      'logPolicy': {'type': 'object', 'additionalProperties': False, 'required': ['logLimitBytes', 'peerMaxBytes', 'preOriginReadMaxBytes', 'rotationInvalidatesTrial', 'logComparedAfterExitFdReleasedStable', 'originFromRendererStream',
                                                                 'logFdReleasedAfterExit', 'logStableReads', 'logStableGapMs'],
                                                    'properties': {'logLimitBytes': {'const': LOG_LIMIT_BYTES}, 'peerMaxBytes': {'const': PEER_MAX_BYTES}, 'preOriginReadMaxBytes': {'const': PRE_ORIGIN_READ_MAX},
                                                                   'rotationInvalidatesTrial': {'const': True}, 'logComparedAfterExitFdReleasedStable': {'const': True}, 'originFromRendererStream': {'const': True},
                                                                   'logFdReleasedAfterExit': {'const': True}, 'logStableReads': {'const': LOG_STABLE_READS}, 'logStableGapMs': {'const': LOG_STABLE_GAP_MS}}},
                                      'schedule': {'type': 'array', 'minItems': 20, 'maxItems': 20, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['slot', 'cell', 'repeat', 'firstArm'],
                                                   'properties': {'slot': {'type': 'integer', 'minimum': 0, 'maximum': 19}, 'cell': {'enum': list(CELLS)}, 'repeat': {'type': 'integer', 'minimum': 0, 'maximum': 9}, 'firstArm': {'enum': list(ARMS)}}}},
                                      'backendIdentities': {'type': 'array', 'maxItems': 40, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['trial', 'pid', 'start'],
                                                            'properties': {'trial': TRIAL, 'pid': {'type': 'integer', 'minimum': 1}, 'start': NB}}}}},
            'independence': {'type': 'object', 'additionalProperties': False, 'required': ['canaries', 'status'],
                             'properties': {'status': {'enum': ['pass', 'fail']},
                                            'canaries': {'type': 'array', 'minItems': 12, 'maxItems': 12, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['id', 'values', 'S', 'trip'],
                                                         'properties': {'id': {'enum': CANARY_IDS}, 'values': {'type': 'array', 'minItems': 10, 'maxItems': 10, 'items': NUMNULL_NONNEG},
                                                                        'S': {'type': ['integer', 'null']}, 'trip': {'type': ['boolean', 'null']}}}}}},
            'matrix': {'type': 'object', 'additionalProperties': False, 'required': ['attempted', 'accepted', 'complete', 'integrityFailures', 'evidenceInvalidTrials', 'missingSlots'],
                       'properties': {'attempted': {'const': 40}, 'accepted': {'type': 'integer', 'minimum': 0, 'maximum': 40}, 'complete': {'type': 'boolean'},
                                      'integrityFailures': {'type': 'array', 'uniqueItems': True, 'items': NB}, 'evidenceInvalidTrials': {'type': 'array', 'uniqueItems': True, 'items': TRIAL},
                                      'missingSlots': {'type': 'array', 'uniqueItems': True, 'items': TRIAL}},
                       'allOf': [{'if': {'properties': {'complete': {'const': True}}, 'required': ['complete']},
                                  'then': {'properties': {'accepted': {'const': 40}, 'integrityFailures': {'maxItems': 0}, 'evidenceInvalidTrials': {'maxItems': 0}, 'missingSlots': {'maxItems': 0}}}}]},
            'survivors': {'type': 'object', 'additionalProperties': False, 'required': ['rustArm', 'trials'],
                          'properties': {'rustArm': {'type': 'boolean'}, 'trials': {'type': 'array', 'uniqueItems': True, 'items': NB}},
                          'allOf': [{'if': {'properties': {'rustArm': {'const': False}}, 'required': ['rustArm']}, 'then': {'properties': {'trials': {'maxItems': 0}}}}]},
            'isolation': {'type': 'object', 'additionalProperties': False, 'required': ['breach', 'trials'],
                          'properties': {'breach': {'type': 'boolean'},
                                         'trials': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['trial', 'backendPidBefore', 'backendPidAfter', 'backendStartBefore', 'backendStartAfter'],
                                                    'properties': {'trial': NB, 'backendPidBefore': {'type': 'integer', 'minimum': 1}, 'backendPidAfter': {'type': 'integer', 'minimum': 1}, 'backendStartBefore': NB, 'backendStartAfter': NB}}}}},
            'claudeObservation': {'type': 'object', 'additionalProperties': False, 'required': ['state', 'findings'],
                                  'properties': {'state': {'enum': FACT_DOMAINS['claudeObservation']}, 'findings': {'type': 'array', 'uniqueItems': True, 'items': NB}}},
            'baselineIdentity': nullable(baseline_full),   # null only on the no-matrix path (see the derivation allOf below)
            'locality': {'type': 'object', 'additionalProperties': False, 'required': ['violation', 'checkScript', 'scopeList', 'allowlist', 'findings', 'referenceManifestSha256'],
                         'properties': {'violation': {'type': 'boolean'}, 'checkScript': hashref(), 'scopeList': hashref(), 'allowlist': hashref(), 'findings': {'type': 'array', 'uniqueItems': True, 'items': NB},
                                        'referenceManifestSha256': HEX},
                         'allOf': [{'if': {'properties': {'violation': {'const': False}}, 'required': ['violation']}, 'then': {'properties': {'findings': {'maxItems': 0}}}}]},
            'maintainability': {'type': 'object', 'additionalProperties': False, 'required': ['complete', 'analyses', 'touchedResponsibilities', 'repeatedFacts', 'manualCompositionSites'],
                                'properties': {'complete': {'type': 'boolean'}, 'analyses': {'type': 'array', 'items': hashref()}, 'touchedResponsibilities': {'type': 'array', 'uniqueItems': True, 'items': NB},
                                               'repeatedFacts': {'type': 'array', 'uniqueItems': True, 'items': NB}, 'manualCompositionSites': {'type': 'array', 'uniqueItems': True, 'items': NB}},
                                'allOf': [{'if': {'properties': {'complete': {'const': True}}, 'required': ['complete']},
                                           'then': {'properties': {'analyses': {'minItems': 1}, 'touchedResponsibilities': {'minItems': 1}, 'repeatedFacts': {'minItems': 1}, 'manualCompositionSites': {'minItems': 1}}}}]},
            'readerBenchmark': {'type': 'object', 'additionalProperties': False, 'required': ['status', 'fixtures', 'goldenEvents', 'calibrationRuns', 'runs', 'findings', 'm0', 'rows'],
                                'properties': {'status': {'enum': FACT_DOMAINS['readerBenchmark']}, 'fixtures': {'type': 'array', 'items': hashref()},
                                               'goldenEvents': {'oneOf': [hashref(), {'type': 'null'}]}, 'calibrationRuns': {'type': 'integer', 'minimum': 0}, 'runs': {'type': 'integer', 'minimum': 0},
                                               'findings': {'type': 'array', 'uniqueItems': True, 'items': NB},
                                               'm0': {'type': 'object', 'additionalProperties': False, 'required': ['claude', 'opencode'],
                                                      'properties': {fx: {'type': 'object', 'additionalProperties': False, 'required': ['python', 'rust'], 'properties': {'python': {'type': 'boolean'}, 'rust': {'type': 'boolean'}}} for fx in ('claude', 'opencode')}},
                                               'rows': {'type': 'object', 'additionalProperties': False, 'properties': {rid: reader_row_schema(rid) for rid in READER_ROWS}}},
                                'allOf': [{'if': {'properties': {'status': {'enum': ['pass', 'worse', 'unresolved']}}, 'required': ['status']},
                                           'then': {'required': ['rows'], 'properties': {'fixtures': {'minItems': 2}, 'goldenEvents': hashref(), 'calibrationRuns': {'minimum': 10}, 'runs': {'minimum': 10}, 'rows': {'required': READER_ROWS}}}}]},
            'correctness': correctness_schema(),
            'rows': {'type': 'object', 'additionalProperties': False, 'required': base_ids, 'properties': {r['id']: row_schema(r) for r in REG}},
            'reportOnly': {'type': 'object', 'additionalProperties': False, 'required': ['nonDecisional', 'resizeAckMs'],
                           'properties': {'nonDecisional': {'const': True},
                                          'resizeAckMs': {'type': 'object', 'additionalProperties': False, 'required': ['1', '4'],
                                                          'properties': {c: {'type': 'object', 'additionalProperties': False, 'required': ['validPairs', 'medianDelta'],
                                                                             'properties': {'validPairs': {'type': 'integer', 'minimum': 0, 'maximum': N_PAIRS}, 'medianDelta': NUMNULL}} for c in ('1', '4')}}}},
            'exploratory': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['id', 'description', 'nonDecisional'],
                                                       'properties': {'id': NB, 'description': NB, 'nonDecisional': {'const': True}}}},
            'decision': {'type': 'object', 'additionalProperties': False, 'required': ['tableVersion', 'facts', 'reasons'],
                         'properties': {'tableVersion': {'const': TABLE_VERSION},
                                        'facts': {'type': 'object', 'additionalProperties': False, 'required': list(FACT_DOMAINS), 'properties': facts_props},
                                        'reasons': {'type': 'array', 'minItems': 1, 'items': {'type': 'object', 'additionalProperties': False, 'required': ['rule'], 'properties': {'rule': NB, 'detail': {'type': 'string'}}}}}},
            'outcome': {'enum': ['continue', 'stop', 'inconclusive']},
            'limitations': {'type': 'array', 'minItems': 1, 'uniqueItems': True, 'items': NB},
            'receiptsManifest': {'type': 'object', 'additionalProperties': False, 'required': ['sha256', 'evidence'], 'properties': {'sha256': HEX, 'evidence': ref('evidenceRef')}},
            'jointReview': {'type': 'object', 'additionalProperties': False, 'required': ['status', 'reports'],
                            'properties': {'status': {'enum': ['pass', 'pending', 'fail']}, 'recordedBy': NB, 'reports': {'type': 'array', 'items': hashref()}},
                            'allOf': [{'if': {'properties': {'status': {'const': 'pass'}}, 'required': ['status']}, 'then': {'required': ['recordedBy'], 'properties': {'reports': {'minItems': 2}}}}]},
            'continuationAuthority': authority,
            'gates': {'type': 'object', 'additionalProperties': False, 'required': ['ticketE5', 'g2'], 'properties': {'ticketE5': gate, 'g2': gate}},
            'gateSemantics': {'const': 'necessary-not-sufficient'}, 'notAdoption': {'const': True}},
        'allOf': [
            {'properties': {'limitations': {'contains': {'type': 'string', 'pattern': 'descendant snapshot'}}}},   # the named limitation against D4 must be recorded
            # option combinations: D6 required <=> reader calibration; calibration 'none' carries no receipts and no noisy or uncalibrated rows
            {'if': opt('readerRequired', {'const': True}), 'then': {'properties': {'preregistration': {'properties': {'options': {'properties': {'calibration': {'const': 'rows+reader'}}}}}}}},
            {'if': opt('calibration', {'const': 'rows+reader'}), 'then': {'properties': {'preregistration': {'properties': {'options': {'properties': {'readerRequired': {'const': True}}}}}}}},
            {'if': opt('calibration', {'const': 'none'}), 'then': {'properties': {'preregistration': {'properties': {'calibration': {'properties': {'receipts': {'maxItems': 0}, 'noisyRows': {'maxItems': 0}, 'uncalibratedRows': {'maxItems': 0}}}}}}}},
            # v14-r2: the C1 option and the guard are constants (b, 8) with a calibration and 'n/a' without one; there is no cap
            {'if': opt('calibration', {'const': 'none'}), 'then': opt('c1Noisy', {'const': 'n/a'}) | {'allOf': [opt('noisyGuard', {'const': 'n/a'})]}},
            {'if': opt('calibration', {'enum': ['rows', 'rows+reader']}), 'then': opt('c1Noisy', {'const': 'b'}) | {'allOf': [opt('noisyGuard', {'const': NOISY_GUARD})]}},
            # a noisy row is reported with its error: the report section exists exactly for the listed rows, and the limitation entry naming k and the list is required with them
            {'if': {'properties': {'preregistration': {'properties': {'calibration': {'properties': {'noisyRows': {'maxItems': 0}}, 'required': ['noisyRows']}}, 'required': ['calibration']}}},
             'then': {'properties': {'noisyReport': {'maxItems': 0}}},
             'else': {'properties': {'limitations': {'contains': {'type': 'string', 'pattern': r'^noisy rows: k = [0-9]+ \('}}}}},
            # V21: the kill/reap clause is required iff the C1 row has ten valid pairs and a median d_r below 0, and absent otherwise
            {'if': {'properties': {'c1Report': {'properties': {'validPairs': {'const': N_PAIRS}, 'medianD': {'exclusiveMaximum': 0}}, 'required': ['validPairs', 'medianD']}}, 'required': ['c1Report']},
             'then': {'properties': {'interpretation': {'required': ['killReap']}}}, 'else': {'properties': {'interpretation': {'not': {'required': ['killReap']}}}}},
            *[{'if': {'properties': {'rows': {'properties': {rid: {'properties': {'status': {'const': 'noisy'}}, 'required': ['status']}}, 'required': [rid]}}, 'required': ['rows']},      # `noisy` only for a listed row
               'then': {'properties': {'preregistration': {'properties': {'calibration': {'properties': {'noisyRows': {'contains': {'const': rid}}}, 'required': ['noisyRows']}}, 'required': ['calibration']}}}}
              for rid in PAIRED_IDS],
            {'if': {'allOf': [{'properties': {'preregistration': {'properties': {'options': {'properties': {'calibration': {'enum': ['rows', 'rows+reader']}}}}}}},
                              {'properties': {'derivation': {'properties': {'path': {'const': 'matrix'}}}}}]},
             'then': {'properties': {'preregistration': {'properties': {'calibration': {'properties': {'receipts': {'minItems': 1}}}}}}}},
            # V10 item 5: `not_derivable` and an absent (null) baseline / locality / maintainability record are legal only on the no-matrix path
            {'if': {'properties': {'derivation': {'properties': {'path': {'const': 'matrix'}}}}},
             'then': {'properties': {'baselineIdentity': {'type': 'object'}, 'locality': {'type': 'object'}, 'maintainability': {'type': 'object'},
                                     'decision': {'properties': {'facts': {'properties': {k: {'not': {'const': ND}} for k in ND_FACTS}}}}}}},
            {'if': {'properties': {'derivation': {'properties': {'path': {'const': 'no-matrix'}}}}},
             'then': {'properties': {'matrix': {'properties': {'accepted': {'const': 0}, 'complete': {'const': False}}},
                                     'decision': {'properties': {'facts': {'properties': {'matrixComplete': {'const': False}}}}}}}},
            {'if': opt('readerRequired', {'const': False}),
             'then': {'properties': {'readerBenchmark': {'properties': {'status': {'const': 'not_required'}, 'rows': {'maxProperties': 0}}},
                                     'correctness': {'properties': {OPTIONAL_CORRECTNESS: {'properties': {'status': {'const': 'not-required'}}}}}}}},
            {'if': opt('readerRequired', {'const': True}),
             'then': {'properties': {'readerBenchmark': {'properties': {'status': {'enum': ['pass', 'worse', 'unresolved', 'not_run']}}},
                                     'correctness': {'properties': {OPTIONAL_CORRECTNESS: {'properties': {'status': {'not': {'const': 'not-required'}}}}}}}}}],
        '$defs': {
            'hashRef': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'sha256'], 'properties': {'path': NB, 'sha256': HEX}},
            'evidenceRef': {'type': 'object', 'additionalProperties': False, 'required': ['ledger', 'date', 'quote'],
                            'properties': {'ledger': NB, 'date': {'type': 'string', 'pattern': r'^\d{4}-\d{2}-\d{2}$'}, 'quote': NB}},
            'rowStd': row_base('parity'), 'rowBeat': row_base('beat'), 'readerRow': reader_row, **pair_defs}}
    for key in ('locality', 'maintainability'):
        schema['properties'][key] = nullable(schema['properties'][key])
    return schema

# ---- synthetic documents -----------------------------------------------------------------------------------------------------
def h(label: str) -> str: return hashlib.sha256(label.encode()).hexdigest()
def hr(label: str) -> dict: return {'path': f'synthetic/{label}', 'sha256': h(label)}
def ev(label: str) -> dict: return {'ledger': 'synthetic/progress.md', 'date': '2000-01-01', 'quote': f'SYNTHETIC {label}'}
def spec_of(doc: dict, rid: str) -> dict:
    return next(r for r in REG if r['id'] == rid)

def good_pairs(spec: dict) -> list[dict]:
    out = []
    for r in range(N_PAIRS):
        py = 100.0 + r * 0.1
        if spec['source'] == 'rust-value': out.append({'repeat': r, 'python': None, 'rust': 0.0, 'valid': True})
        elif spec['kind'] == 'beat': out.append({'repeat': r, 'python': 300.0 + r, 'rust': 20.0 + r, 'valid': True})
        else: out.append({'repeat': r, 'python': py, 'rust': py, 'valid': True})
    return out

def recompute(doc: dict) -> None:
    """Set every derived field of a synthetic document from its pairs/lists (used only to BUILD examples; tests mutate without it)."""
    bad = bad_slot_pairs(doc)
    noisy = set(doc['preregistration']['calibration']['noisyRows'])
    for spec in REG:
        row = doc['rows'][spec['id']]
        for p in row['pairs']:
            if p['repeat'] in bad[spec['cell']]: p.update(valid=False, python=None, rust=None)
        row.update(row_counts(spec, row, set()))
        row['status'] = status_from_counts(spec['kind'], row['validPairs'], row['exceedCount'], row.get('notImprovedCount'), spec['id'] in noisy)
        if spec['kind'] != 'beat': row.pop('notImprovedCount', None)
    rb = doc['readerBenchmark']
    for rid, row in rb['rows'].items():
        c = reader_row_counts(row, set()); row.update(c)
        row['status'] = status_from_counts('parity', c['validPairs'], c['exceedCount'])
    t = doc['matrix']['evidenceInvalidTrials'] + doc['matrix']['missingSlots']
    doc['matrix'].update(accepted=40 - len(t), complete=not t and not doc['matrix']['integrityFailures'])
    doc['design']['backendIdentities'] = [i for i in doc['design']['backendIdentities'] if i['trial'] not in set(doc['matrix']['missingSlots'])]
    for c in doc['independence']['canaries']:
        c['S'] = kendall_s(c['values']) if all(finite(x) for x in c['values']) else None
        c['trip'] = canary_trip(c)
    doc['independence']['status'] = independence_status(doc)
    doc['design']['compliant'] = design_compliant(doc)
    rb['status'] = reader_status(doc)
    doc['decision'] = {'tableVersion': TABLE_VERSION, 'facts': derive(doc), 'reasons': []}
    doc['outcome'], reasons = decide(doc['decision']['facts'], derivation_path(doc), carried_stop_codes(doc))
    doc['decision']['reasons'] = [{'rule': r} for r in reasons]
    doc['noisyReport'], doc['c1Report'], doc['interpretation'] = build_reports(doc)      # v14-r2: derived, never entered by hand; an existing entry keeps its calibration noise (the decider supplies it from the receipts)
    ids = noisy_ids(doc)
    doc['limitations'] = [t for t in doc['limitations'] if not t.startswith(NOISY_LIMITATION_PREFIX)] + ([noisy_limitation(ids)] if ids else [])

def make_doc(reader_required: bool = True, calibration: str | None = None) -> dict:
    calibration = calibration or ('rows+reader' if reader_required else 'rows')
    reg = REG
    seed = 'synthetic-seed-1'; sched = make_schedule(seed)
    rows = {}
    for r in reg:
        rows[r['id']] = {'id': r['id'], 'class': r['cls'], 'metric': r['metric'], 'cell': r['cell'], 'kind': r['kind'], 'unit': r['unit'], 'source': r['source'],
                         'margin': {**r['margin'], 'basis': r['basis']}, 'pairs': good_pairs(r), 'validPairs': 10, 'exceedCount': 0, 'medianExcess': 0.0, 'status': 'pass'}
        if r['kind'] == 'beat': rows[r['id']]['notImprovedCount'] = 0
    reader_rows = {rid: {'id': rid, 'unit': 'ms' if 'm1_' in rid else ('s' if 'm2_' in rid else 'MiB'),
                         'margin': {'mode': 'absolute', 'value': 1.0, 'frozenIn': hr('calibration')},
                         'pairs': [{'repeat': i, 'python': 10.0, 'rust': 10.0, 'valid': True} for i in range(N_PAIRS)], 'validPairs': 10, 'exceedCount': 0, 'status': 'pass'}
                   for rid in READER_ROWS} if reader_required else {}
    canaries = [{'id': cid, 'values': [100.0 + ((i * 7 + k * 3) % 10) for i in range(10)], 'S': 0, 'trip': False} for k, cid in enumerate(CANARY_IDS)]
    doc = {
        'exampleOnly': True, 'schema': SCHEMA_ID, 'ticket': 'E4', 'derivation': {'path': 'matrix', 'earlierRuns': []},
        'evaluated': {'head': 'a' * 40, 'branch': 'synthetic', 'sidecarSha256': h('sidecar'), 'methodRevision': 'method-10-e', 'freezeSha256': h('freeze'), 'e1ProtocolSha256': E1_BOUND_SHA256,
                      'evidenceRevision': 'synthetic', 'summarySha256': h('summary'), 'inputsSha256': h('inputs'), 'receiptsManifestSha256': h('manifest')},
        'preregistration': {'document': 'design/e2-g1e-prereg-proposal-v10.md', 'sha256': h('prereg'), 'userApproval': ev('approval'),
                            'options': {'readerRequired': reader_required, 'descendantSnapshot': SNAPSHOT_OPTION, 'calibration': calibration, 'pairsPerCell': 10, **noisy_options(calibration)},
                            'calibration': {'receipts': [] if calibration == 'none' else [hr('calibration-receipts')], 'noisyRows': [], 'uncalibratedRows': []}},
        'design': {'scheduleSeed': seed, 'schedule': sched, 'scheduleSha256': schedule_sha(sched), 'compliant': True, 'logPolicy': {'logLimitBytes': LOG_LIMIT_BYTES, 'peerMaxBytes': PEER_MAX_BYTES, 'preOriginReadMaxBytes': PRE_ORIGIN_READ_MAX, 'rotationInvalidatesTrial': True, 'logComparedAfterExitFdReleasedStable': True, 'originFromRendererStream': True, 'logFdReleasedAfterExit': True, 'logStableReads': LOG_STABLE_READS, 'logStableGapMs': LOG_STABLE_GAP_MS},
                   'backendIdentities': [{'trial': f'{a}:{c}:{r}', 'pid': 1000 + n, 'start': f'synthetic-start-{n}'} for n, (a, c, r) in enumerate((a, c, r) for a in ARMS for c in CELLS for r in range(10))]},
        'independence': {'canaries': canaries, 'status': 'pass'},
        'matrix': {'attempted': 40, 'accepted': 40, 'complete': True, 'integrityFailures': [], 'evidenceInvalidTrials': [], 'missingSlots': []},
        'survivors': {'rustArm': False, 'trials': []}, 'isolation': {'breach': False, 'trials': []}, 'claudeObservation': {'state': 'ok', 'findings': []},
        'baselineIdentity': {'status': 'identical', 'upstreamMerge': 'b' * 40, 'e3Parent': 'c' * 40, 'e3Commit': 'd' * 40, 'e3Set': [], 'pythonArmFileList': hr('pyfiles'),
                             'referenceManifest': hr('reference-manifest'), 'changedFiles': []},
        'locality': {'violation': False, 'checkScript': hr('locality-check'), 'scopeList': hr('scope'), 'allowlist': hr('allow'), 'findings': [], 'referenceManifestSha256': h('reference-manifest')},
        'maintainability': {'complete': True, 'analyses': [hr('maintainability')], 'touchedResponsibilities': ['synthetic'], 'repeatedFacts': ['synthetic'], 'manualCompositionSites': ['synthetic']},
        'readerBenchmark': {'status': 'pass' if reader_required else 'not_required', 'fixtures': [hr('fixture-claude'), hr('fixture-opencode')] if reader_required else [],
                            'goldenEvents': hr('golden') if reader_required else None, 'calibrationRuns': 10 if reader_required else 0, 'runs': 10 if reader_required else 0, 'findings': [],
                            'm0': {fx: {'python': True, 'rust': True} for fx in ('claude', 'opencode')}, 'rows': reader_rows},
        'correctness': {cid: {'status': 'pass-current', 'receipt': hr('receipt-' + cid)} for cid in CORRECTNESS_IDS},
        'rows': rows, 'reportOnly': {'nonDecisional': True, 'resizeAckMs': {'1': {'validPairs': 10, 'medianDelta': 0.4}, '4': {'validPairs': 10, 'medianDelta': 0.6}}},
        'exploratory': [], 'outcome': 'continue', 'limitations': ['SYNTHETIC illustration; macOS arm64 only', 'descendant snapshot is not measured (named limitation against D4)'],
        'receiptsManifest': {'sha256': h('manifest'), 'evidence': ev('manifest')},
        'jointReview': {'status': 'pass', 'recordedBy': 'synthetic-manager', 'reports': [hr('review-a'), hr('review-b')]},
        'continuationAuthority': {'recorded': False},
        'gates': {'ticketE5': {'open': False, 'missing': ['user authorization for hosted Actions']}, 'g2': {'open': False, 'missing': ['E5 functional pass', 'continuation authority']}},
        'gateSemantics': 'necessary-not-sufficient', 'notAdoption': True}
    if not reader_required:
        doc['correctness'][OPTIONAL_CORRECTNESS] = {'status': 'not-required'}
    recompute(doc)
    return doc

def make_no_matrix_doc(carried: list[str] | None = None, reader_required: bool = True) -> dict:
    """A synthetic document of the V10 item 5 path: no matrix attempt exists (all 40 slots missing), nothing else was measured; `carried` = stop codes of an earlier run (V7 item 5)."""
    d = make_doc(reader_required=reader_required)
    d['derivation'] = {'path': 'no-matrix', 'earlierRuns': ([{'manifestSha256': h('earlier-run'), 'kind': 'matrix', 'stopReason': None, 'stopFacts': list(carried)}] if carried else [])}
    d['matrix'].update(missingSlots=[f'{a}:{c}:{r}' for a in ARMS for c in CELLS for r in range(10)], evidenceInvalidTrials=[], integrityFailures=[])
    d['design']['backendIdentities'] = []
    d['preregistration']['calibration'] = {'receipts': [], 'noisyRows': [], 'uncalibratedRows': []}
    for c in d['independence']['canaries']: c['values'] = [None] * 10
    d['claudeObservation'] = {'state': 'incomplete', 'findings': ['no matrix attempt']}
    d['baselineIdentity'] = d['locality'] = d['maintainability'] = None
    rb = d['readerBenchmark']
    if reader_required:
        rb.update(fixtures=[], goldenEvents=None, calibrationRuns=0, runs=0, findings=['no reader-measure run'], m0={fx: {'python': False, 'rust': False} for fx in ('claude', 'opencode')}, rows={})
    for cid in CORRECTNESS_IDS:
        if not (cid == OPTIONAL_CORRECTNESS and not reader_required): d['correctness'][cid] = {'status': 'not-rerun'}
    d['jointReview'] = {'status': 'pending', 'reports': []}
    d['gates'] = {'ticketE5': {'open': False, 'missing': ['outcome is not continue']}, 'g2': {'open': False, 'missing': ['E5 functional pass', 'continuation authority']}}
    recompute(d)
    return d

def clone(d): return json.loads(json.dumps(d))

def example_docs() -> dict[str, dict]:
    ex = {'complete': make_doc(), 'complete-reader-not-required': make_doc(reader_required=False, calibration='none')}
    d = make_doc()
    d['matrix']['missingSlots'] = ['python:4:3', 'rust-shell:4:3']
    for i in range(5, 9):                                                         # 4 repeats beyond +10 ms: neither pass nor adverse
        p = d['rows']['stream|lat:burst_ms|cell4']['pairs'][i]; p['rust'] = p['python'] + 20.0
    d['correctness']['E-fd-hygiene'] = {'status': 'not-rerun'}
    d['readerBenchmark']['m0']['opencode']['rust'] = False; d['readerBenchmark'].update(rows={}, findings=['synthetic: fixture not available'])
    d['maintainability']['complete'] = False; d['jointReview'] = {'status': 'pending', 'reports': []}
    d['independence']['canaries'][0]['values'][3] = None
    recompute(d); ex['absent-evidence'] = d
    d = make_doc()
    for p in d['rows']['stream|lat:burst_ms|cell4']['pairs']: p['rust'] = p['python'] + 31.0
    d['rows']['lifecycle|lat:first_shell_ready_ms|cell1']['pairs'][2].update(valid=False, python=None, rust=None)
    d['jointReview'] = {'status': 'pending', 'reports': []}
    recompute(d); ex['adverse'] = d
    d = make_doc()
    for p in d['readerBenchmark']['rows']['reader|m1_wall_ms|claude']['pairs']: p['rust'] = p['python'] + 3.0
    recompute(d); ex['reader-worse'] = d
    d = make_doc()
    d['readerBenchmark']['m0']['opencode']['rust'] = False
    d['correctness']['E-observation-equality'] = {'status': 'failed', 'attribution': 'rust-design', 'boundedCorrectivePath': True, 'receipt': hr('m0-failure')}
    recompute(d); ex['reader-m0-failed'] = d
    d = make_doc()
    d['preregistration']['calibration']['uncalibratedRows'] = ['stream|lat:burst_ms|cell4', 'stream|lat:burst_input_ms|cell4']
    recompute(d); ex['calibration-uncalibrated'] = d
    d = make_doc()
    d['preregistration']['calibration']['noisyRows'] = ['stream|lat:burst_ms|cell4', 'stream|lat:burst_input_ms|cell4']    # two noisy stream rows, all pairs equal: both `noisy`, the stream class passes without them
    recompute(d); ex['calibration-noisy-listed'] = d
    d = make_doc()
    d['preregistration']['calibration']['noisyRows'] = ['stream|lat:burst_ms|cell4']
    for i in (5, 6, 7):                                                              # 3 of 10 pairs beyond +10 ms: not a pass for a resolvable row, `noisy` for this one (E <= 8)
        p = d['rows']['stream|lat:burst_ms|cell4']['pairs'][i]; p['rust'] = p['python'] + 20.0
    recompute(d); ex['noisy-stream-row'] = d
    d = make_doc(); d['preregistration']['calibration']['noisyRows'] = [C1_ID]       # a noisy C1 with all ten differences below zero: `improved` (V12 option (b))
    recompute(d); ex['noisy-c1-improved'] = d
    d = make_doc(); d['preregistration']['calibration']['noisyRows'] = [C1_ID]       # a noisy C1 with one difference not below zero: `noisy`, INCONCLUSIVE (I10-kill-reap-noisy)
    p = d['rows'][C1_ID]['pairs'][3]; p['rust'] = p['python'] + 5.0
    recompute(d); ex['noisy-c1-noisy'] = d
    d = make_doc(); d['preregistration']['calibration']['noisyRows'] = list(PAIRED_IDS)   # all 37 paired rows noisy, C1 improved: CONTINUE with k = 37 (no cap, V13)
    recompute(d); ex['noisy-37-rows'] = d
    d = make_doc()
    for p in d['rows']['lifecycle|lat:first_shell_ready_ms|cell4']['pairs']: p['rust'] = p['python'] + 5.0
    d['independence']['canaries'][1]['values'] = [100.0 + i for i in range(10)]    # a monotone drift: the screen trips
    recompute(d); ex['independence-failed'] = d
    d = make_doc()                                                                   # B3: observed failure, attribution and bound explicitly 'unknown' -> STOP S3
    d['correctness']['E-fd-hygiene'] = {'status': 'failed', 'attribution': 'unknown', 'boundedCorrectivePath': 'unknown', 'receipt': hr('observed-failure')}
    recompute(d); ex['correctness-failed-unknown'] = d
    d = make_doc()                                                                   # B3: observed failure with attribution and bound absent -> STOP S3
    d['correctness']['E-stale-generation'] = {'status': 'failed', 'note': 'synthetic: failure observed, not yet attributed'}
    recompute(d); ex['correctness-failed-absent'] = d
    ex['no-matrix-inconclusive'] = make_no_matrix_doc()                              # V10 item 5: calibration stopped, nothing else observed: INCONCLUSIVE (I1, I11)
    ex['no-matrix-carried-stop'] = make_no_matrix_doc(carried=['S1'])                # V7 item 5: an earlier run's survivor is carried: STOP
    return ex

@functools.lru_cache(maxsize=1)
def example_digests() -> dict:
    """The SHA-256 of each synthetic example document (canonical JSON), by name. F0 lists them (v13 section 4: "schema and examples hashes") and F1 recomputes them, so a changed example, or
    a checker change that changes what an example derives, is a difference between F0 and the world."""
    return {name: _canon_sha(doc) for name, doc in sorted(example_docs().items())}


# ---- v6: pure reference definitions of the checks the proposal specifies (tested on simulated streams / exact distributions) ------
# One registry of EXACT fixture byte strings (finding F2). Every marker, waiter, bound and receipt uses these templates; the token-consistency test scans
# the proposal, the log-check script and this file for any BASELINE_ token that does not conform. All output lines are LF-terminated on the renderer stream.
from tests.cli_regression.support.g1e.tokens import FIXTURE_TOKENS, TOKEN_SEPARATOR, INPUTS, WORKLOAD  # noqa: E402  (stdlib-only registry; the peer imports it too)
def tok(name: str, **kw) -> str: return FIXTURE_TOKENS[name].format(**kw)
def line(name: str, **kw) -> bytes: return (tok(name, **kw) + '\n').encode('utf-8')

LOG_LIMIT_BYTES = 16 * 1024 * 1024          # per-session log file limit (registered)
PEER_MAX_BYTES = 12 * 1024 * 1024           # peer self-limit on accepted bytes since the origin (registered; v14-r2 V17: 40 cell-1 rounds are 10 689 733 B, ratio 1.18)
LOG_STABLE_READS = 2                        # after terminal.exit: two reads of the log must agree (registered)
LOG_STABLE_GAP_MS = 250                     # minimum spacing of the two reads (registered)
PRE_ORIGIN_READ_MAX = 256 * 1024            # reader read_total at the origin snapshot must not exceed this (registered; checked from the counter, no log read)
UTF8_REPLACEMENT_EXPANSION = 3              # worst case log bytes per stream byte: one invalid byte -> U+FFFD (3 UTF-8 bytes); the cleaning adds nothing else
BUF_CAP_SOURCE = 5 * 1024 * 1024            # terminals.py:323 _BUF_CAP; drop-oldest cannot fire with less buffered output, so no pre-origin gap-marker line

def burst_rounds(cell: int) -> int:
    """The registered burst rounds of ONE peer in `cell` (v14-r2 V17: cell 1 = 40 rounds of one peer, cell 4 = 10 rounds per peer x 4 peers)."""
    return WORKLOAD['burstRounds'][str(cell)]

def fixture_output_bytes(w: dict = WORKLOAD, cell: int = 1) -> dict:
    """Upper bound of the bytes ONE peer of `cell` writes from the start marker on, built from FIXTURE_TOKENS (round numbers <= 4 digits). The default, cell 1 (one peer x 40 rounds),
    is the byte maximum that PEER_MAX_BYTES and LOG_LIMIT_BYTES are checked against."""
    enc = len
    start = enc(line('start', nonce='0' * 32))
    ack_io = enc(line('ack', text=INPUTS['io'].format(round=9999, input=w['input'])))
    ack_mid = enc(line('ack', text=INPUTS['mid_burst'].format(kk='00')))
    io = w['ioRounds'] * ack_io + enc(line('win_begin', wid='i0')) + enc(line('win_end', wid='i0'))
    burst_line = enc(line('burst_line', kk='00', line=0, xs='x' * w['burstWidth']))
    per_round = w['burstLines'] * burst_line + ack_mid + enc(line('done', kk='00')) + enc(line('win_begin', wid='b0r00')) + enc(line('win_end', wid='b0r00'))
    burst = w['burstRounds'][str(cell)] * per_round
    final = enc(line('final', accepted='9' * 10, digest='0' * 64))
    post_origin = io + burst + final
    total = start + post_origin
    pre_origin_log_max = UTF8_REPLACEMENT_EXPANSION * PRE_ORIGIN_READ_MAX
    log_max = pre_origin_log_max + total + 3                     # + decoder tail at close (<= one U+FFFD)
    return {'start_line': start, 'ack_io_max': ack_io, 'ack_mid': ack_mid, 'burst_line': burst_line, 'io_window': io, 'burst_round': per_round, 'burst_rounds_total': burst,
            'final_max': final, 'post_origin': post_origin, 'total_peer': total, 'peer_limit_ratio': PEER_MAX_BYTES / post_origin,
            'pre_origin_log_max': pre_origin_log_max, 'log_max': log_max, 'log_limit_ratio': LOG_LIMIT_BYTES / log_max}

def reference_input_bytes() -> int: return len(WORKLOAD['input'].encode('utf-8'))
_ANSI_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|[PX^_][^\x1b]*\x1b\\"
    r"|[@-Z\\-~]"
    r")"
)
_TUI_NOISE_RE = re.compile(
    r"bypasspermissions|shift\+tab|esc to interrupt|esctointerrupt"
    r"|tointerrupt|for agents|/effort"
    r"|\[end of text\]"
    r"|^\s*$",
    re.IGNORECASE,
)
def reference_clean_for_log(raw):
    text = _ANSI_RE.sub("", raw)
    text = re.sub(r"\r(?!\n)", "\n", text)
    lines_out = []
    blank_run = 0
    for line in text.splitlines():
        stripped = line.rstrip()
        if _TUI_NOISE_RE.search(stripped):
            continue
        if not stripped:
            blank_run += 1
            if blank_run <= 1:
                lines_out.append("")
        else:
            blank_run = 0
            lines_out.append(stripped)
    return "\n".join(lines_out)
clean_for_log = reference_clean_for_log   # reference copy of backend terminals._clean_for_log, verified equal to the real one by e2-v5/e2-v6-log-clean-check.py (unchanged in v7)

def ws_strip(s: str) -> str:
    """The comparison key of the cleaned log: all whitespace removed. The real _clean_for_log rstrips every line of every flushed payload and joins without a
    trailing LF, so ONLY the whitespace-free text is preserved."""
    return re.sub(r'\s+', '', s)

class LineBufferedLog:
    """Model of the backend's log writer `open(path, "a", encoding="utf-8", buffering=1)` (terminals.py:663, cli_runtime.py:752): a write becomes visible on disk
    only when the written string contains '\\n' or '\\r' (line_buffering) or at close(). (Size-triggered flushes of the real buffers only make text visible EARLIER;
    the v6 protocol never reads the log before close, so ignoring them is conservative.) e2-v6-log-clean-check.py verifies this on a real file."""
    def __init__(self, rotate_at: int | None = None):
        self.segments = ['']; self.pending = ''; self.disk = ''; self.closed = False; self.rotate_at = rotate_at; self.inode = 1; self.reads_before_close = 0
    def write(self, text: str):
        assert not self.closed
        if self.rotate_at is not None and len(self.segments[-1].encode()) + len(text.encode()) > self.rotate_at:
            self.segments.append(''); self.inode += 1
        self.segments[-1] += text; self.pending += text
        if '\n' in text or '\r' in text: self.disk += self.pending; self.pending = ''
    def close(self): self.disk += self.pending; self.pending = ''; self.closed = True
    def peek_disk(self) -> str:                                  # what a separate reader of the file would see now (used only to demonstrate the v5 hazard)
        self.reads_before_close += 0 if self.closed else 1; return self.disk
    def read_after_close(self) -> str:
        if not self.closed: raise RuntimeError('the protocol compares the session log only after exit, fd release and two stable reads (modelled as the writer having finished)')
        return ''.join(self.segments)

class SimStream:
    """Simulated Terminal Stream: reader chunks, coalesced flush payloads and drop ranges are independent, as in the real pipeline. The renderer stream is the
    emitted bytes; the log receives clean_for_log(incremental_utf8_decode(payload)) per flushed payload (plus the gap line of a drop episode) through a
    line-buffered writer that is closed at session exit (terminals.py:2036-2041; cli_runtime.py:1059-1063, both BEFORE terminal.exit is emitted)."""
    def __init__(self, nonce: str, chunk_sizes, payload_sizes=(4096,), drop_ranges=(), rotate_at: int | None = None):
        self.nonce = nonce; self.marker = line('start', nonce=nonce)
        self.read_total = self.emitted_total = self.dropped_total = 0
        self.chunk_sizes = list(chunk_sizes); self.payload_sizes = list(payload_sizes); self.drop_ranges = list(drop_ranges)
        self.log = LineBufferedLog(rotate_at); self.stream = bytearray(); self.renderer = bytearray()
        self._dec = codecs.getincrementaldecoder('utf-8')(errors='replace'); self._pending_drop = 0; self._k = 0; self._p = 0
    def feed_all(self, data: bytes):
        self.stream += data; pos = 0; survivors = bytearray()
        while pos < len(data):
            n = self.chunk_sizes[self._k % len(self.chunk_sizes)]; self._k += 1
            chunk = data[pos:pos + n]; pos += n
            start = self.read_total; self.read_total += len(chunk)
            for i, b in enumerate(chunk):
                off = start + i
                if any(a <= off < c for a, c in self.drop_ranges): self.dropped_total += 1; self._pending_drop += 1
                else: self.emitted_total += 1; survivors.append(b)
        pos = 0
        while pos < len(survivors):
            n = self.payload_sizes[self._p % len(self.payload_sizes)]; self._p += 1
            payload = bytes(survivors[pos:pos + n]); pos += n
            self.renderer += payload
            if self._pending_drop:
                self.log.write(clean_for_log(f'\r\n[navide: {self._pending_drop} bytes of output dropped]\r\n')); self._pending_drop = 0
            self.log.write(clean_for_log(self._dec.decode(payload)))
    def exit(self):
        tail = self._dec.decode(b'', final=True)
        if tail: self.log.write(clean_for_log(tail))
        self.log.close()

def count_exact(hay: bytes, needle: bytes) -> int:
    n = 0; i = hay.find(needle)
    while i >= 0: n += 1; i = hay.find(needle, i + 1)
    return n

def c_log_policy(a):
    """log: a LineBufferedLog model whose writer has finished (closed is True); inode_at_create: int >= 0; pre_origin_log_bytes: int >= 0."""
    log = v_instance(a['log'], 'log', LineBufferedLog)
    if log.closed is not True: _rej('log', 'the modelled writer has not finished (the comparison runs only on after-exit reads)')
    return {'log': log, 'inode_at_create': v_int(a['inode_at_create'], 'inode_at_create'), 'pre_origin_log_bytes': v_int(a['pre_origin_log_bytes'], 'pre_origin_log_bytes')}

def s_log_policy(c):
    """The in-force log-policy constants still hash to the frozen log-policy digest; consistency: the pre-origin part of the log cannot exceed the log's size."""
    s_frozen_current('log_policy_sha256', 'log policy')
    size = sum(len(x.encode('utf-8')) for x in c['log'].segments)
    if c['pre_origin_log_bytes'] > size: _srej('pre_origin_log_bytes', f'{c["pre_origin_log_bytes"]} pre-origin bytes exceed the whole log ({size} bytes)')

@checker(c_log_policy, s_log_policy)
def log_policy_ok(log, inode_at_create, pre_origin_log_bytes):
    """Checked on the after-exit reads only. ANY violation invalidates the trial; no partial voiding, no segment reconstruction."""
    if len(log.segments) != 1: return False, 'rotated: more than one log segment/file'
    if log.inode != inode_at_create: return False, 'log file replaced (inode changed)'
    size = len(log.segments[0].encode('utf-8'))
    if size > LOG_LIMIT_BYTES: return False, 'log larger than the registered limit'
    if pre_origin_log_bytes > UTF8_REPLACEMENT_EXPANSION * PRE_ORIGIN_READ_MAX: return False, 'pre-origin log contribution above its registered bound'
    return True, 'ok'

def c_ledger(a):
    """sim: a SimStream model; startup: bytes (b'' = known empty); peer_payload: non-empty bytes; peer_final_extra: int >= 0; peer_bytes_limit: int >= 1;
    truncate_log_before_close: bool."""
    return {'sim': v_instance(a['sim'], 'sim', SimStream), 'startup': v_bytes(a['startup'], 'startup'), 'peer_payload': v_bytes(a['peer_payload'], 'peer_payload', nonempty=True),
            'peer_final_extra': v_int(a['peer_final_extra'], 'peer_final_extra'), 'peer_bytes_limit': v_int(a['peer_bytes_limit'], 'peer_bytes_limit', 1),
            'truncate_log_before_close': v_bool(a['truncate_log_before_close'], 'truncate_log_before_close')}

def s_ledger(c):
    """The in-force fixture registry and log policy still hash to the frozen digests; peer_bytes_limit is the registered PEER_MAX_BYTES; the SimStream model is
    fresh (nothing read, log writer not finished) with a 32-hex nonce, so one model cannot be reused across trials."""
    s_frozen_current('fixture_registry_sha256', 'fixture registry'); s_frozen_current('log_policy_sha256', 'log policy')
    if c['peer_bytes_limit'] != PEER_MAX_BYTES: _srej('peer_bytes_limit', f'{c["peer_bytes_limit"]} is not the registered PEER_MAX_BYTES {PEER_MAX_BYTES}')
    sim = c['sim']
    if sim.read_total or len(sim.stream) or sim.log.closed: _srej('sim', 'the model is not fresh (bytes already read or the log writer finished): one model per trial')
    if not re.fullmatch(r'[0-9a-f]{32}', str(sim.nonce)): _srej('sim.nonce', 'nonce is not 32 lowercase hex characters (128 bits)')

@checker(c_ledger, s_ledger, invalid_result=lambda kind, why: {'valid': False, 'why': why, 'kind': kind})
def ledger_check(sim, startup, peer_payload, peer_final_extra=0, peer_bytes_limit=PEER_MAX_BYTES, truncate_log_before_close=False) -> dict:
    """v6 reference ledger (finding F1). ORIGIN from the renderer stream and the reader counters ONLY: the driver waits for the exact bytes
    'BASELINE_LEDGER_START:<nonce>\\n' to have arrived exactly once on the renderer stream, snapshots the counters (the peer is quiescent), checks the pre-origin
    bound from read_total, and releases the peer. The session log is NOT read before release, so the release cannot wait on log-writer buffering.
    LOG comparison only after terminal.exit, the fd-release check and two stable reads (in this model: after the writer finished; close() success itself is not claimed): marker located as _clean_for_log writes it (whitespace-free,
    exactly once in the raw text), then ws_strip(text after the marker) == ws_strip(peer payload). Startup output before the marker is excluded."""
    inode_at_create = sim.log.inode
    sim.feed_all(startup + sim.marker)
    if count_exact(bytes(sim.renderer), sim.marker) != 1: return {'valid': False, 'why': 'origin marker not exactly once on the renderer stream'}
    snap = dict(read=sim.read_total, emitted=sim.emitted_total, dropped=sim.dropped_total)
    if snap['read'] != len(sim.stream): return {'valid': False, 'why': 'reader behind at snapshot'}
    if snap['read'] - len(sim.marker) > PRE_ORIGIN_READ_MAX: return {'valid': False, 'why': 'pre-origin output above its registered bound'}
    marker_on_disk_at_origin = tok('start', nonce=sim.nonce) in sim.log.peek_disk()       # recorded ONLY to show the v5 hazard; never a gate
    if len(peer_payload) > peer_bytes_limit: return {'valid': False, 'why': 'peer exceeded its registered byte limit', 'marker_on_disk_at_origin': marker_on_disk_at_origin}
    sim.feed_all(peer_payload)
    d_read, d_emit, d_drop = sim.read_total - snap['read'], sim.emitted_total - snap['emitted'], sim.dropped_total - snap['dropped']
    if truncate_log_before_close: sim.log.segments[-1] = sim.log.segments[-1][: len(sim.log.segments[-1]) // 3]
    sim.exit()
    raw = sim.log.read_after_close()
    mstr = tok('start', nonce=sim.nonce)
    if raw.count(mstr) != 1: return {'valid': False, 'why': 'origin marker not exactly once in the closed log', 'marker_on_disk_at_origin': marker_on_disk_at_origin}
    idx = raw.index(mstr)
    ok_policy, why = log_policy_ok(sim.log, inode_at_create, len(raw[:idx].encode('utf-8')))
    if not ok_policy: return {'valid': False, 'why': why, 'marker_on_disk_at_origin': marker_on_disk_at_origin}
    accepted = len(peer_payload) + peer_final_extra
    counters_ok = d_read == accepted and d_emit + d_drop == d_read and d_drop == 0
    renderer_ok = bytes(sim.renderer).endswith(peer_payload)
    text_ok = ws_strip(raw[idx + len(mstr):]) == ws_strip(peer_payload.decode('utf-8', errors='replace'))
    ok = counters_ok and renderer_ok and text_ok
    return {'valid': ok, 'why': 'ok' if ok else ('counters' if not counters_ok else ('renderer stream' if not renderer_ok else 'log text differs')),
            'd_read': d_read, 'd_emit': d_emit, 'd_drop': d_drop, 'accepted': accepted, 'marker_on_disk_at_origin': marker_on_disk_at_origin,
            'log_reads_before_close': 0}

def c_burst(a):
    """cell: int (a registered cell is checked by the semantic stage); rounds: int 1..99; send_ts: dict whose keys are exactly the ints 0..rounds-1, values numbers >= 0; events: list of [t, chunk] pairs, t a number >= 0
    strictly increasing, chunk non-empty bytes; fixture_sha256: 64 lowercase hex (the fixture-registry digest the receipt was produced under)."""
    cell = v_int(a['cell'], 'cell')
    rounds = v_int(a['rounds'], 'rounds', 1, 99)
    st = v_present(a['send_ts'], 'send_ts')
    if type(st) is not dict: _rej('send_ts', f'expected dict, got {_tn(st)}')
    if set(st) != set(range(rounds)) or any(type(k) is not int for k in st): _rej('send_ts', f'keys must be exactly the ints 0..{rounds - 1}')
    st = {k: v_num(st[k], f'send_ts[{k}]') for k in st}
    def ev(x, q):
        v_present(x, q)
        if type(x) not in (list, tuple) or len(x) != 2: _rej(q, 'expected a [t, chunk] pair')
        return (v_num(x[0], f'{q}[0]'), v_bytes(x[1], f'{q}[1]', nonempty=True))
    events = v_list(a['events'], 'events', ev)
    v_strictly_increasing([t for t, _ in events], 'events.t')
    return {'events': events, 'send_ts': st, 'cell': cell, 'rounds': rounds, 'fixture_sha256': v_hex64(a['fixture_sha256'], 'fixture_sha256')}

def _burst_endpoints(events, rounds):
    """Arrival time of the chunk that completes the exact bytes line('done', kk) on the renderer stream, per round (None if it never arrives)."""
    buf = bytearray(); out = [None] * rounds
    for ts, chunk in events:
        before = len(buf); buf += chunk
        for kk in range(rounds):
            if out[kk] is None:
                needle = line('done', kk=f'{kk:02d}'); i = buf.find(needle)
                if i >= 0 and i + len(needle) > before: out[kk] = ts
    return out

def s_burst(c):
    """Frozen binding: fixture_sha256 is the frozen fixture-registry digest and the in-force FIXTURE_TOKENS/INPUTS/WORKLOAD still hash to it; fixture range:
    cell is a registered cell and rounds equals that cell's registered WORKLOAD burstRounds (cell 1: 40, cell 4: 10); temporal order: send times strictly increase by round; every round's endpoint is not earlier than
    that round's send; round kk+1 is not sent before round kk's endpoint (C5: the next burst starts when the previous DONE is observed); each round's exact
    endpoint occurs at most once on the stream (an ambiguous endpoint is not a measurement). An early endpoint makes the whole trial invalid; it never yields a latency."""
    s_binds(c['fixture_sha256'], 'fixture_registry_sha256', 'fixture_sha256'); s_frozen_current('fixture_registry_sha256', 'fixture registry')
    if c['cell'] not in CELLS: _srej('cell', f'{c["cell"]} is not a registered cell {CELLS}')
    if c['rounds'] != burst_rounds(c['cell']): _srej('rounds', f'{c["rounds"]} is not the registered burstRounds {burst_rounds(c["cell"])} of cell {c["cell"]}')
    st = c['send_ts']; s_order([(f'send_ts[{k}]', st[k]) for k in range(c['rounds'])], 'send_ts')
    full = b''.join(ch for _, ch in c['events'])
    for kk in range(c['rounds']):
        if full.count(line('done', kk=f'{kk:02d}')) > 1: _srej(f'round {kk:02d}', 'its exact endpoint occurs more than once on the renderer stream (ambiguous endpoint)')
    end = _burst_endpoints(c['events'], c['rounds'])
    for kk, t in enumerate(end):
        if t is not None and t < st[kk]: _srej(f'round {kk:02d}', f'endpoint at {t} precedes the round\'s send at {st[kk]} (a completion before the input was sent)')
        if t is not None and kk + 1 < c['rounds'] and st[kk + 1] < t: _srej(f'round {kk + 1:02d}', f'sent at {st[kk + 1]} before round {kk:02d} ended at {t}')

@checker(c_burst, s_burst)
def burst_round_latencies(events, send_ts, fixture_sha256, cell, rounds):
    """The burst latency waiter (finding F2): for round kk the endpoint is the arrival time of the chunk that completes the exact bytes line('done', kk) on the
    renderer stream; latency = endpoint - client send timestamp of 'burst r<kk>'. Verdict(True, 'ok', value=[latency or None per round]); a round whose exact
    endpoint never arrives yields None (its pair is invalid). The semantic stage guarantees no latency is negative."""
    return Verdict(True, 'ok', value=[None if t is None else t - send_ts[kk] for kk, t in enumerate(_burst_endpoints(events, rounds))])

def token_violations(text: str) -> list[str]:
    """Every BASELINE_<NAME> occurrence must use a registered name followed by its registered separator."""
    bad = []
    for m in re.finditer(r'BASELINE_[A-Z_]*[A-Z]', text):
        name = m.group(0); nxt = text[m.end():m.end() + 1]
        if name not in TOKEN_SEPARATOR: bad.append(f'unregistered token {name!r}'); continue
        if nxt != TOKEN_SEPARATOR[name] and nxt not in ('`', "'", '"', '<', ')', ',', '.', '\n', '/'): bad.append(f'{name} followed by {nxt!r} (expected {TOKEN_SEPARATOR[name]!r})')
    return bad

def c_loss(a):
    """binding: record {peer_id, session_id, cutoff_id: non-empty ids; variant: 'L1' | 'L1b-release-inside' | 'L1b-release-after' | 'L1c'; e1_sha256: 64 hex}; peer: non-empty bytes (the peer's post-origin output);
    pieces: list (known-empty [] allowed) of records {a: int, b: int, data: bytes, after_exit: bool} with 0 <= a < b <= len(peer), len(data) == b - a,
    strictly increasing and non-overlapping; renderer_record: bytes (b'' = known empty; missing/None rejected);
    taken_batch: record {peer_id, session_id, cutoff_id, in_flight_unknown: bool, pieces: strictly increasing non-overlapping non-empty intervals inside
    [0, len(peer)]}, bound to the same peer/session/cutoff as `binding`; pieces == [] iff in_flight_unknown is false;
    cutoff_k: None or int >= 0 (its variant-specific range is a semantic precondition)."""
    binding = v_record(a['binding'], 'binding', {'peer_id': v_id, 'session_id': v_id, 'cutoff_id': v_id, 'variant': lambda v, q: v_str(v, q, enum=LOSS_VARIANTS), 'e1_sha256': v_hex64})
    peer = v_bytes(a['peer'], 'peer', nonempty=True); n = len(peer)
    def piece(x, p):
        r = v_record(x, p, {'a': lambda v, q: v_int(v, q, 0, n), 'b': lambda v, q: v_int(v, q, 0, n), 'data': lambda v, q: v_bytes(v, q, nonempty=True), 'after_exit': v_bool})
        if r['b'] <= r['a']: _rej(p, f'empty or reversed interval [{r["a"]},{r["b"]})')
        if len(r['data']) != r['b'] - r['a']: _rej(p, f'{len(r["data"])} bytes for an interval of {r["b"] - r["a"]} (interval and bytes must correspond one to one)')
        return r
    pieces = v_list(a['pieces'], 'pieces', piece)
    v_strictly_increasing_intervals([(x['a'], x['b']) for x in pieces], 'pieces')
    record = v_bytes(a['renderer_record'], 'renderer_record')
    tb = v_record(a['taken_batch'], 'taken_batch', {'peer_id': v_id, 'session_id': v_id, 'cutoff_id': v_id, 'in_flight_unknown': v_bool,
                                                    'pieces': lambda v, q: v_intervals(v, q, 0, n)})
    for k in ('peer_id', 'session_id', 'cutoff_id'):
        if tb[k] != binding[k]: _rej(f'taken_batch.{k}', f'{tb[k]!r} does not match the observation binding {binding[k]!r} (a receipt for another peer, session or cutoff)')
    if tb['in_flight_unknown'] and not tb['pieces']: _rej('taken_batch.pieces', 'in_flight_unknown is true but no taken piece is listed')
    if not tb['in_flight_unknown'] and tb['pieces']: _rej('taken_batch.pieces', 'pieces listed although no batch was taken (in_flight_unknown is false)')
    ck = a['cutoff_k']
    if ck is not None: ck = v_int(ck, 'cutoff_k', 0)
    return {'binding': binding, 'peer': peer, 'pieces': pieces, 'renderer_record': record, 'taken_batch': tb, 'cutoff_k': ck}

LOSS_VARIANTS = ('L1', 'L1b-release-inside', 'L1b-release-after', 'L1c')
def s_loss(c):
    """Frozen binding: binding.e1_sha256 is the frozen E1 v8b hash. Variant ranges (E1 v8b L1, L1b, L1c; the variant is explicit in the binding): L1 and both
    L1b variants take no cutoff_k (supplying one is rejected); L1b-release-after requires a taken batch (in_flight_unknown true); L1c requires a taken batch
    of n >= 2 pieces and 1 <= cutoff_k < n, and cutoff_k must be supplied (omitting it is rejected, so an L1c call cannot skip the cutoff proof). The L1b
    variant whose sink never releases asserts no renderer receipt in E1 v8b and is not a variant of this byte check."""
    b, tb, k = c['binding'], c['taken_batch'], c['cutoff_k']
    s_binds(b['e1_sha256'], 'e1_sha256', 'binding.e1_sha256')
    n, v = len(tb['pieces']), b['variant']
    if v == 'L1c':
        if not tb['in_flight_unknown'] or n < 2: _srej('taken_batch.pieces', f'L1c needs a taken batch of n >= 2 pieces (E1 v8b: 0 < k < n), got n = {n}')
        if k is None: _srej('cutoff_k', 'L1c requires cutoff_k (omitting it would skip the cutoff proof)')
        if not 1 <= k < n: _srej('cutoff_k', f'L1c requires 1 <= k < n (at least one piece received and one pending at the cutoff), got k = {k}, n = {n}')
    else:
        if k is not None: _srej('cutoff_k', f'variant {v} takes no cutoff_k (got {k}); the cutoff proof is L1c only')
        if v == 'L1b-release-after' and not tb['in_flight_unknown']: _srej('taken_batch.in_flight_unknown', 'L1b-release-after requires a taken batch (in_flight_unknown true)')

@checker(c_loss, s_loss)
def loss_delivery_ok(binding, peer, pieces, renderer_record, taken_batch, cutoff_k=None):
    """E-sidecar-loss-uniform byte check for the at-most-once loss path of E1 v8b section 8.1 (L1, L1b, L1c), on the COMPLETE observation (collected until the
    test's delayed sink has been released and every drain task of the session has finished). Shapes are guaranteed by c_loss. PASS iff every piece's bytes
    equal the peer's bytes at its interval; the renderer record is exactly the concatenation of the pieces; no before-exit piece follows an after-exit piece;
    every piece that overlaps the taken batch coincides with exactly one taken-batch piece and distinct renderer pieces match distinct batch pieces (one to
    one); the delivered batch pieces (before and after the exit) are a contiguous prefix of the batch (v10: none skipped); every after-exit piece is a remaining piece of the already-taken batch (zero to all of them; nothing outside the batch); with cutoff_k (L1c) the batch
    pieces delivered before the exit are exactly the first k. Gaps and an unknown in-flight outcome are allowed: no conservation equation (E1 v8b 8.1)."""
    for i, pc in enumerate(pieces):
        if pc['data'] != peer[pc['a']:pc['b']]: return Verdict(False, f'piece {i}: bytes differ from the peer bytes at [{pc["a"]},{pc["b"]})')
    if renderer_record != b''.join(pc['data'] for pc in pieces): return Verdict(False, 'renderer record differs from the concatenation of the pieces (unaccounted or missing bytes)')
    seen_exit = False
    for i, pc in enumerate(pieces):
        if seen_exit and not pc['after_exit']: return Verdict(False, f'piece {i}: marked before exit after a piece marked after exit')
        seen_exit = seen_exit or pc['after_exit']
    batch = taken_batch['pieces']; matched = []
    for i, pc in enumerate(pieces):
        iv = (pc['a'], pc['b']); over = [j for j, (x, y) in enumerate(batch) if x < iv[1] and iv[0] < y]
        if not over:
            if pc['after_exit']: return Verdict(False, f'piece {i}: [{iv[0]},{iv[1]}) after the exit is not a remaining piece of the already-taken batch')
            continue
        if len(over) != 1 or batch[over[0]] != iv: return Verdict(False, f'piece {i}: [{iv[0]},{iv[1]}) does not coincide with exactly one taken-batch piece (partial or straddling)')
        matched.append((over[0], pc['after_exit']))
    js = [j for j, _ in matched]
    if len(set(js)) != len(js): return Verdict(False, 'two renderer pieces matched the same taken-batch piece (not one to one)')
    if js != list(range(len(js))): return Verdict(False, f'delivered taken-batch pieces {js} are not a contiguous prefix of the batch (E1 v8b: the drain emits its taken pieces in order; zero to all remaining pieces, none skipped)')
    pre = [j for j, after in matched if not after]
    if cutoff_k is not None and pre != list(range(cutoff_k)): return Verdict(False, f'L1c: distinct batch pieces before the exit are {pre}, expected exactly the first {cutoff_k}')
    if binding['variant'] == 'L1b-release-inside' and merge_intervals([(pc['a'], pc['b']) for pc in pieces]) != [(0, len(peer))]:
        return Verdict(False, 'L1b (sink released inside the bound): not every byte reached the renderer')
    return Verdict(True, 'ok')

CLOSE_ONCE_ORDER = {'L1e': ('t_freed_ms', 't_replacement_opened_ms', 't_sidecar_killed_ms', 't_checked_ms'),
                    'L1f': ('t_sidecar_killed_ms', 't_freed_ms', 't_replacement_opened_ms', 't_sink_released_ms', 't_checked_ms')}
def _close_once_fields(route):
    common = {'handle_kind': lambda v, q: v_str(v, q, enum=('fd', 'HANDLE')), 'freed_number': v_int, 'replacement_number': v_int, 'numbers_equal_harness_verified': v_bool,
              'runtime_lost_exits_for_session': lambda v, q: v_int(v, q, 0, 64), 'e1_sha256': v_hex64}
    if route == 'L1e':
        return {**common, **{t: v_num for t in CLOSE_ONCE_ORDER['L1e']}, 'precondition_retained_stream_closed_root_live': v_bool, 'loss_step2_raw_close_for_session': v_bool,
                'unrelated_resource_open_and_usable_after_loss': v_bool, 'identity_cleanup_proceeded': v_bool}
    return {**common, **{t: v_num for t in CLOSE_ONCE_ORDER['L1f']}, 'precondition_finisher_suspended_in_emit_ignoring_first_cancel': v_bool, 'precondition_loss_step2_closed_the_handle': v_bool,
            'resumed_finisher_stopped_at_fence': v_bool, 'resumed_finisher_raw_close': v_bool, 'resumed_finisher_tty_eof': v_bool, 'resumed_finisher_normal_exit': v_bool,
            'unrelated_resource_open_and_usable_after_resume': v_bool, 'after_exit_bytes_only_from_taken_batch': v_bool}

def c_close_once(a):
    """route: 'L1e' or 'L1f'; rec: record with exactly the route's fields — handle_kind ('fd'|'HANDLE'), freed_number and replacement_number (int >= 0; the freed
    fd/HANDLE number of the session and the number the OS gave the unrelated resource), numbers_equal_harness_verified (bool, the harness's own equality check),
    runtime_lost_exits_for_session (int 0..64), the route's precondition flag(s) and behaviour flags (bool), e1_sha256 (64 hex) and the route's event
    timestamps (numbers >= 0; L1e t_freed/t_replacement_opened/t_sidecar_killed/t_checked_ms, L1f t_sidecar_killed/t_freed/t_replacement_opened/t_sink_released/t_checked_ms)."""
    route = v_str(a['route'], 'route', enum=('L1e', 'L1f'))
    return {'route': route, 'rec': v_record(a['rec'], 'rec', _close_once_fields(route))}

def s_close_once(c):
    """Frozen binding: rec.e1_sha256 is the frozen E1 v8b hash. Fixture range: handle_kind is 'fd' (G1-E is macOS arm64 only; HANDLE receipts belong to E5 and
    need their own registration). Temporal order (strict): L1e t_freed < t_replacement_opened < t_sidecar_killed < t_checked (the number is reused before the
    loss and the resource is checked after it); L1f t_sidecar_killed < t_freed < t_replacement_opened < t_sink_released < t_checked (loss step 2 frees the
    number, the replacement takes it, then the suspended finisher is released)."""
    rec, route = c['rec'], c['route']
    s_binds(rec['e1_sha256'], 'e1_sha256', 'rec.e1_sha256')
    if rec['handle_kind'] != 'fd': _srej('rec.handle_kind', f'{rec["handle_kind"]!r} receipts belong to E5 (Windows); G1-E registers macOS fd receipts only')
    s_order([(t, rec[t]) for t in CLOSE_ONCE_ORDER[route]], f'rec ({route} event order)')

@checker(c_close_once, s_close_once)
def close_once_ok(route, rec):
    """E1 v8b L1e/L1f close-once and retirement-fence receipts. Decisive only if the descriptor-number reuse was actually obtained: freed_number ==
    replacement_number AND the harness verified the equality (a contradiction between the flag and the numbers fails), and the route's fixture precondition held
    (L1e: the session is retained stream-closed and root-live; L1f: the finisher was suspended inside _emit on a sink ignoring the first cancellation and loss
    step 2 closed the handle). Then L1e: no raw close for the session in loss step 2, the unrelated resource open and usable after the loss, no runtime_lost exit
    for this already-exited session, identity cleanup proceeded; L1f: the resumed finisher stopped at its fence with no raw close, no TTY_EOF and no normal exit,
    the unrelated resource open and usable after the resume, exactly one runtime_lost exit, after-exit bytes only from the taken batch."""
    eq_numbers = rec['freed_number'] == rec['replacement_number']
    if rec['numbers_equal_harness_verified'] != eq_numbers: return Verdict(False, f'{route}: harness equality flag contradicts the recorded numbers')
    if not eq_numbers: return Verdict(False, f'{route}: no descriptor-number reuse obtained (freed {rec["freed_number"]}, replacement {rec["replacement_number"]}); the test is not decisive')
    if route == 'L1e':
        if rec['precondition_retained_stream_closed_root_live'] is not True: return Verdict(False, 'L1e: fixture precondition (retained stream-closed, root-live session) not established')
        ok = (rec['loss_step2_raw_close_for_session'] is False and rec['unrelated_resource_open_and_usable_after_loss'] is True
              and rec['runtime_lost_exits_for_session'] == 0 and rec['identity_cleanup_proceeded'] is True)
    else:
        if rec['precondition_finisher_suspended_in_emit_ignoring_first_cancel'] is not True or rec['precondition_loss_step2_closed_the_handle'] is not True:
            return Verdict(False, 'L1f: fixture precondition (finisher suspended in _emit ignoring the first cancellation; loss step 2 closed the handle) not established')
        ok = (rec['resumed_finisher_stopped_at_fence'] is True and rec['resumed_finisher_raw_close'] is False and rec['resumed_finisher_tty_eof'] is False
              and rec['resumed_finisher_normal_exit'] is False and rec['unrelated_resource_open_and_usable_after_resume'] is True
              and rec['runtime_lost_exits_for_session'] == 1 and rec['after_exit_bytes_only_from_taken_batch'] is True)
    return Verdict(True, 'ok') if ok else Verdict(False, f'{route}: close-once/retirement-fence expectation violated')

def merge_intervals(iv: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out = []
    for a, b in sorted(iv):
        if b <= a: continue
        if out and a <= out[-1][1]: out[-1] = (out[-1][0], max(out[-1][1], b))
        else: out.append((a, b))
    return out

def interval_total(iv): return sum(b - a for a, b in iv)
def subtract(a_iv, b_iv):
    """a minus b, both merged interval lists."""
    out = []
    for a, b in a_iv:
        cur = a
        for c, d in b_iv:
            if d <= cur or c >= b: continue
            if c > cur: out.append((cur, c))
            cur = max(cur, d)
        if cur < b: out.append((cur, b))
    return out

def resize_ctx(arm, variant='S', t_slurp_end_ms=10.0, t_ack_ms=20.0, e1=None):
    """A resize receipt context (used by the tests and the mutation baselines)."""
    return {'arm': arm, 'variant': variant, 'e1_sha256': e1 or E1_BOUND_SHA256, 't_slurp_end_ms': t_slurp_end_ms, 't_ack_ms': t_ack_ms}
def v_resize_context(x, p):
    return v_record(x, p, {'arm': lambda v, q: v_str(v, q, enum=('python', 'rust-shell')), 'variant': lambda v, q: v_str(v, q, enum=('Q', 'S')), 'e1_sha256': v_hex64,
                           't_slurp_end_ms': v_num, 't_ack_ms': v_num})
def s_resize_common(c, need_after: bool):
    ctx = c['context']
    s_binds(ctx['e1_sha256'], 'e1_sha256', 'context.e1_sha256')
    s_order([('t_slurp_end_ms', ctx['t_slurp_end_ms']), ('t_ack_ms', ctx['t_ack_ms'])], 'context', strict=False)
    if ctx['variant'] == 'S' and not 0 < c['R'] < c['stream_end']: _srej('R', f'variant S needs bytes read before and after the slurp: 0 < R < stream_end, got R = {c["R"]}, stream_end = {c["stream_end"]}')
    if ctx['variant'] == 'Q':
        if c['R'] != c['stream_end']: _srej('R', f'variant Q (quiescent) reads everything by the slurp: R == stream_end, got R = {c["R"]}, stream_end = {c["stream_end"]}')
        if need_after and c.get('after_ack'): _srej('after_ack', 'variant Q (quiescent): nothing is read after the slurp, so nothing can be emitted after the acknowledgement')
def s_resize(c):
    """Frozen binding: context.e1_sha256 is the frozen E1 v8b hash. Temporal order: t_slurp_end_ms <= t_ack_ms. Fixture-variant ranges: S (flood with a
    captured slurp watermark) needs 0 < R < stream_end; Q (quiescent) needs R == stream_end and no byte after the acknowledgement. Arm binding: B2 is required
    exactly on the Rust arm (derived from context.arm, never from a caller flag)."""
    s_resize_common(c, need_after=True)

def c_resize(a):
    """stream_end: int >= 0 (the ledger close offset); R: int 0..stream_end; before_ack, after_ack, dropped: lists (known-empty [] allowed) of strictly increasing,
    non-overlapping, non-empty intervals inside [0, stream_end]; reported_dropped_bytes: int 0..stream_end (required); context: record {arm: 'python' |
    'rust-shell', variant: 'Q' | 'S', e1_sha256: 64 hex, t_slurp_end_ms, t_ack_ms: numbers >= 0}. B2 is required iff arm is 'rust-shell' (no caller flag)."""
    end = v_int(a['stream_end'], 'stream_end')
    return {'R': v_int(a['R'], 'R', 0, end), 'stream_end': end, 'before_ack': v_intervals(a['before_ack'], 'before_ack', 0, end),
            'after_ack': v_intervals(a['after_ack'], 'after_ack', 0, end), 'dropped': v_intervals(a['dropped'], 'dropped', 0, end),
            'reported_dropped_bytes': v_int(a['reported_dropped_bytes'], 'reported_dropped_bytes', 0, end), 'context': v_resize_context(a['context'], 'context')}

@checker(c_resize, s_resize)
def resize_slurp_assertion(R, stream_end, before_ack, after_ack, dropped, reported_dropped_bytes, context):
    """Byte-based E1 v8b section 9.4 B1-B3 (byte-identical to v6), in origin-relative byte offsets. Reads are sequential, so the bytes read by the end of the
    pre-resize slurp are exactly [0, R) and the bytes FIRST READ AFTER the slurp are exactly the offsets >= R. PASS iff (1) B1: every undropped byte of [0,R)
    is in before_ack (byte by byte) and none of [0,R) is in after_ack; (2) B2 (require_b2, the Rust arm): no byte at an offset >= R is in before_ack; (3) no byte
    is emitted twice (across the two lists) and none is both emitted and dropped; (4) the reported drop count equals the dropped intervals. N1 kept: nothing is
    inferred about the width a post-slurp byte was written for. Python arm: B2 not required; resize_b2_observed records it descriptively."""
    require_b2 = context['arm'] == 'rust-shell'
    pre = [(0, R)]
    b, a, d = merge_intervals(before_ack), merge_intervals(after_ack), merge_intervals(dropped)
    if subtract(b, subtract(b, a)) or subtract(b, subtract(b, d)) or subtract(a, subtract(a, d)): return Verdict(False, 'a byte is emitted twice (before and after the ack) or both emitted and dropped')
    if reported_dropped_bytes != interval_total(d): return Verdict(False, 'reported drop count differs from the dropped intervals')
    if any(max(x, 0) < min(y, R) for x, y in a): return Verdict(False, 'B1: a pre-slurp byte was emitted after the acknowledgement')
    missing = subtract(subtract(pre, d), b)
    if missing: return Verdict(False, f'B1: undropped pre-slurp bytes not emitted before the ack: {interval_total(missing)} bytes, first at {missing[0][0]}')
    early = [(max(x, R), y) for x, y in b if y > R]
    if require_b2 and early: return Verdict(False, f'B2: {interval_total(early)} byte(s) first read after the slurp emitted before the ack, first at {early[0][0]}')
    return Verdict(True, 'ok')

def c_resize_b2(a):
    """stream_end: int >= 0; R: int 0..stream_end; before_ack: strictly increasing, non-overlapping, non-empty intervals inside [0, stream_end]; context: as for
    resize_slurp_assertion."""
    end = v_int(a['stream_end'], 'stream_end')
    return {'R': v_int(a['R'], 'R', 0, end), 'stream_end': end, 'before_ack': v_intervals(a['before_ack'], 'before_ack', 0, end),
            'context': v_resize_context(a['context'], 'context')}
def s_resize_b2(c):
    """As resize_slurp_assertion (E1 hash, t_slurp_end <= t_ack, variant ranges) and arm binding: the descriptive B2 record exists only for the Python arm, on the
    S variant (on the Rust arm B2 is a gate in resize_slurp_assertion, not a description)."""
    if c['context']['arm'] != 'python': _srej('context.arm', 'B2 is recorded descriptively only on the Python arm; on the Rust arm it is a gate')
    if c['context']['variant'] != 'S': _srej('context.variant', 'B2 is only meaningful on the S variant (bytes read after the slurp)')
    s_resize_common(c, need_after=False)

@checker(c_resize_b2, s_resize_b2)
def resize_b2_observed(R, stream_end, before_ack, context):
    """Descriptive B2 result for the Python arm (never a gate there): Verdict(True, 'ok', value=<B2 held>)."""
    return Verdict(True, 'ok', value=not any(y > R for x, y in before_ack))

def c_log_final(a):
    """arm: 'python' | 'rust-shell'; sidecar_live: exactly None on the Python arm, a bool on the Rust arm; inventory: record whose keys are exactly 'backend'
    (+ 'sidecar' iff the Rust-arm sidecar is live), each value a real bool held state; reads: list of at least LOG_STABLE_READS records {t_ms: number >= 0,
    dev, ino, size, mtime_ns: int >= 0, sha256: 64 lowercase hex, regular: bool} with strictly increasing t_ms; t_exit_ms, t_inventory_ms: numbers >= 0."""
    arm = v_str(a['arm'], 'arm', enum=('python', 'rust-shell'))
    if arm == 'python':
        if a['sidecar_live'] is not None: _rej('sidecar_live', f'must be None on the Python arm (no sidecar exists), got {_tn(a["sidecar_live"])}')
        live, required = None, ('backend',)
    else:
        live = v_bool(a['sidecar_live'], 'sidecar_live'); required = ('backend', 'sidecar') if live else ('backend',)
    inventory = v_record(a['inventory'], 'inventory', {k: v_bool for k in required})
    read = lambda x, q: v_record(x, q, {'t_ms': v_num, 'dev': v_int, 'ino': v_int, 'size': v_int, 'mtime_ns': v_int, 'sha256': v_hex64, 'regular': v_bool})
    reads = v_list(a['reads'], 'reads', read, min_len=LOG_STABLE_READS)
    v_strictly_increasing([r['t_ms'] for r in reads], 'reads.t_ms')
    return {'arm': arm, 'sidecar_live': live, 'inventory': inventory, 'reads': reads, 't_exit_ms': v_num(a['t_exit_ms'], 't_exit_ms'), 't_inventory_ms': v_num(a['t_inventory_ms'], 't_inventory_ms')}

def s_log_final(c):
    """Frozen binding: the in-force log-policy constants (LOG_STABLE_READS, LOG_STABLE_GAP_MS, ...) still hash to the frozen digest. Temporal order: the
    inventory and the first read are taken at or after terminal.exit (t_exit_ms <= t_inventory_ms and t_exit_ms <= reads[0].t_ms)."""
    s_frozen_current('log_policy_sha256', 'log policy')
    s_order([('t_exit_ms', c['t_exit_ms']), ('t_inventory_ms', c['t_inventory_ms'])], 'inventory time', strict=False)
    s_order([('t_exit_ms', c['t_exit_ms']), ('reads[0].t_ms', c['reads'][0]['t_ms'])], 'read time', strict=False)

@checker(c_log_final, s_log_final)
def log_final_check(arm, sidecar_live, inventory, reads, t_exit_ms, t_inventory_ms):
    """Evidence that the compared log content is final; it does NOT show that close() succeeded (the Python path swallows close() errors,
    terminals.py:2036-2041). Shapes are guaranteed by c_log_final (complete per-arm inventory, real booleans, enough reads). PASS iff no inspected process holds
    the log, the reads are regular files at least LOG_STABLE_GAP_MS apart and agree on (dev, ino, size, mtime_ns, sha256)."""
    for proc in sorted(inventory):
        if inventory[proc]: return Verdict(False, f'log still open in {proc} after terminal.exit')
    if any(not r['regular'] for r in reads): return Verdict(False, 'log is not a regular file')
    if any(reads[i + 1]['t_ms'] - reads[i]['t_ms'] < LOG_STABLE_GAP_MS for i in range(len(reads) - 1)): return Verdict(False, 'reads closer than the registered gap')
    if len({(r['dev'], r['ino'], r['size'], r['mtime_ns'], r['sha256']) for r in reads}) != 1: return Verdict(False, 'log changed between the reads after terminal.exit (identity, size, mtime or content)')
    return Verdict(True, 'ok')

def poisson_binomial_pmf(ps: list[float]) -> list[float]:
    pmf = [1.0]
    for p in ps:
        new = [0.0] * (len(pmf) + 1)
        for i, v in enumerate(pmf): new[i] += v * (1 - p); new[i + 1] += v * p
        pmf = new
    return pmf

def pooled_null_check(trials: int = 200000, seed: int = 4) -> dict:
    """Numerically check the pooled-mixture null for NON-IDENTICAL independent repeat signs (vendor rotation): with mean exceed probability pbar >= 1/2,
    P(E <= 1) <= P(Bin(10, 1/2) <= 1) = 0.01074 (Hoeffding 1956, Thm 4: lower tails for counts <= mean - 1 are largest in the binomial with the same mean);
    with pbar <= 1/2, P(E = 10) = prod p_r <= pbar**10 <= 2**-10 (AM-GM). Randomised plus adversarial search; the bounds are proved, the search is a sanity check."""
    rng = random.Random(seed); b_par = sum(comb(10, k) for k in range(2)) / 1024; b_adv = 2.0 ** -10
    worst_par = worst_adv = 0.0
    for t in range(trials):
        kind = t % 4
        if kind == 0: ps = [rng.random() for _ in range(10)]
        elif kind == 1: ps = [rng.choice([0.0, 1.0, 0.5, rng.random()]) for _ in range(10)]
        elif kind == 2: ps = [min(1.0, max(0.0, 0.5 + rng.gauss(0, 0.3))) for _ in range(10)]
        else: ps = [0.5] * 5 + [rng.random() for _ in range(5)]
        mean = sum(ps) / 10
        if mean >= 0.5: worst_par = max(worst_par, sum(poisson_binomial_pmf(ps)[:2]))
        if mean <= 0.5: worst_adv = max(worst_adv, poisson_binomial_pmf(ps)[10])
    assert worst_par <= b_par + 1e-12 and worst_adv <= b_adv + 1e-12, (worst_par, b_par, worst_adv, b_adv)
    return {'worst_pass_error': worst_par, 'bound_pass': b_par, 'worst_adverse_error': worst_adv, 'bound_adverse': b_adv, 'draws': trials}

def _normal_cdf(x: float) -> float: return 0.5 * math.erfc(-x / math.sqrt(2))

def noisy_null_check(configs: int = 2000, seed: int = 14) -> dict:
    """v14-r2 T10, the sibling of `pooled_null_check` for the noisy-row rules (V11 item 3 and 5, V12). EXACT arithmetic over seeded random heterogeneous configurations, no simulation noise: ten independent
    repeats r with d_r | Python ~ Normal(mu_r, s_r) (s_r from 3 to 316 ms), Python values drawn per repeat from three support points with random weights, and a margin rule max(floor, share x Python) with
    a RELATIVE share (the margin is a function of the pair's Python value only, as for the R rows and the C1 row of V18). Checked:
      * P(E = 10) = prod_r P(d'_r > 0) <= 2^-10 whenever the mean of the p_r is <= 1/2 (all-ten-beyond-margin test, noisy or not; AM-GM);
      * P(all ten d_r < 0) = prod_r P(d_r < 0) <= 2^-10 whenever the mean of the P(d_r < 0) is <= 1/2 (a false `improved` of V12 option (b));
      * the band [v(2), v(9)] covers the pooled median of the ten distributions with probability >= 1 - 22/1024, for the raw d_r and for the excess d'_r (misses below and above are disjoint
        Poisson-binomial lower tails, each <= 11/1024, Hoeffding 1956)."""
    rng = random.Random(seed); two_10 = 2.0 ** -10; tail = BAND_TAIL
    worst_adv = worst_imp = worst_miss = 0.0; min_cov = 1.0; n_adv = n_imp = 0
    for _ in range(configs):
        floor, share = rng.uniform(0.0, 40.0), rng.choice([0.0, 0.10, 0.15])
        marg = lambda py: max(floor, share * py)
        reps = []
        for _r in range(10):
            s = 10 ** rng.uniform(0.5, 2.5); mu = rng.choice([0.0, rng.gauss(0, 1.5) * s, abs(rng.gauss(0, 1)) * s, -abs(rng.gauss(0, 1)) * s])
            pys = [rng.uniform(100.0, 1000.0) for _ in range(3)]; ws = [rng.random() + 1e-3 for _ in range(3)]; tot = sum(ws)
            reps.append((s, mu, [(marg(py), w / tot) for py, w in zip(pys, ws)]))
        p_excess = [sum(w * (1 - _normal_cdf((m - mu) / s)) for m, w in sup) for s, mu, sup in reps]        # P(d'_r > 0)
        q_below = [_normal_cdf(-mu / s) for s, mu, _sup in reps]                                          # P(d_r < 0)
        if sum(p_excess) / 10 <= 0.5: worst_adv = max(worst_adv, math.prod(p_excess)); n_adv += 1
        if sum(q_below) / 10 <= 0.5: worst_imp = max(worst_imp, math.prod(q_below)); n_imp += 1
        for cdf in (lambda x, s, mu, sup: sum(w * _normal_cdf((x + m - mu) / s) for m, w in sup),           # P(d'_r < x): the excess
                    lambda x, s, mu, sup: _normal_cdf((x - mu) / s)):                                     # P(d_r < x): the raw difference
            lo, hi = -1e5, 1e5
            for _i in range(200):
                mid = (lo + hi) / 2
                if sum(cdf(mid, s, mu, sup) for s, mu, sup in reps) / 10 < 0.5: lo = mid
                else: hi = mid
            q = [cdf(hi, s, mu, sup) for s, mu, sup in reps]
            miss = sum(poisson_binomial_pmf(q)[:2]) + sum(poisson_binomial_pmf([1 - x for x in q])[:2])
            worst_miss = max(worst_miss, miss); min_cov = min(min_cov, 1 - miss)
    assert worst_adv <= two_10 + 1e-12 and worst_imp <= two_10 + 1e-12 and worst_miss <= 2 * tail + 1e-9, (worst_adv, worst_imp, worst_miss)
    return {'configs': configs, 'worst_all_ten_adverse': worst_adv, 'worst_all_ten_improved_without_improvement': worst_imp, 'bound_two_pow_minus_10': two_10, 'adverse_configs_used': n_adv,
            'improved_configs_used': n_imp, 'worst_band_miss': worst_miss, 'min_band_coverage': min_cov, 'band_tail': tail, 'nominal_coverage': BAND_COVERAGE}

# ---- tests -------------------------------------------------------------------------------------------------------------------

def derive_safe(d: dict):
    try: return derive(d)
    except Exception: return None

def independent_continue_check(d: dict) -> bool:
    """Re-check a CONTINUE document straight from raw data, without using derive()."""
    if d['matrix']['evidenceInvalidTrials'] or d['matrix']['missingSlots'] or d['matrix']['integrityFailures']: return False
    for rid, row in d['rows'].items():
        if len(row['pairs']) != 10: return False
        for p in row['pairs']:
            if not p['valid'] or p['rust'] is None or not math.isfinite(p['rust']): return False
            if row['source'] != 'rust-value' and (p['python'] is None or not math.isfinite(p['python'])): return False
    noisy = set(d['preregistration']['calibration']['noisyRows'])      # v14-r2 V13: a noisy row may have up to 8 exceeding pairs; a noisy C1 needs all ten differences below zero
    for rid, row in d['rows'].items():
        m = row['margin']; ex = 0
        for p in row['pairs']:
            if row['source'] == 'rust-value': e = p['rust'] - m['value']
            else:
                diff = p['rust'] - p['python']; e = diff - (m['value'] if m['mode'] == 'absolute' else max(m['floor'], m['value'] * p['python']))
            ex += e > 0
        if ex > (8 if rid in noisy else 1): return False
        if row['kind'] == 'beat' and sum(1 for p in row['pairs'] if p['rust'] - p['python'] >= 0) > (0 if rid in noisy else 1): return False
    if any(v['status'] not in ('pass-current', 'not-required') or ('receipt' not in v and v['status'] == 'pass-current') for v in d['correctness'].values()): return False
    if d['preregistration']['options']['readerRequired']:
        m0 = d['readerBenchmark']['m0']
        if not all(m0[a][b] for a in m0 for b in m0[a]) or set(d['readerBenchmark']['rows']) != set(READER_ROWS): return False
        for row in d['readerBenchmark']['rows'].values():
            if sum(1 for p in row['pairs'] if p['rust'] - p['python'] - row['margin']['value'] > 0) > 1 or not all(p['valid'] for p in row['pairs']): return False
    if any(c['trip'] is not False for c in d['independence']['canaries']) or d['locality']['violation'] or not d['maintainability']['complete']: return False
    if d['claudeObservation']['state'] != 'ok' or d['survivors']['rustArm'] or d['isolation']['breach'] or d['baselineIdentity']['status'] == 'changed': return False
    if d['preregistration']['calibration']['uncalibratedRows']: return False
    return True


def schema_text() -> str:
    """The exact bytes of the frozen schema file (what `build_schema()` generates; the file's SHA-256 is `FROZEN['schema_sha256']`)."""
    return json.dumps(build_schema(), indent=1, sort_keys=True) + '\n'

if __name__ == '__main__':
    # `python -m tests.cli_regression.support.g1e.reference --write-schema` regenerates the frozen schema file from `build_schema()` and prints its SHA-256
    # (to be copied into FROZEN['schema_sha256'] when the schema is deliberately changed); `--print-schema-sha256` prints the digest of the text without writing.
    if sys.argv[1:] == ['--write-schema']:
        FROZEN_SCHEMA_PATH.write_text(schema_text(), encoding='utf-8')
    elif sys.argv[1:] != ['--print-schema-sha256']:
        raise SystemExit('usage: reference --write-schema | --print-schema-sha256')
    print(hashlib.sha256(schema_text().encode('utf-8')).hexdigest())
