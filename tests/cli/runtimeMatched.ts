// Matched Python/Rust baseline study (method 8) helpers: ordering, derived
// startup metric and the per-trial proof that each arm ran the runtime it
// claims. Test-only; no runtime selection, inference or fallback lives here.
export type RuntimeMode = 'python' | 'rust-shell'

export type MatchedAttempt = { repeat: number; mode: RuntimeMode; count: number }

/** Repeat-major order: even repeats run the first mode first, odd repeats the last, so drift is spread across arms. */
export function matchedOrder(repeats: number, counts: number[], modes: RuntimeMode[]): MatchedAttempt[] {
  const attempts: MatchedAttempt[] = []
  for (let repeat = 0; repeat < repeats; repeat++) {
    const ordered = repeat % 2 === 0 ? modes : [...modes].reverse()
    for (const mode of ordered) for (const count of counts) attempts.push({ repeat, mode, count })
  }
  return attempts
}

/** Process-cold backend readiness plus the first ordinary-shell READY of the same trial; null unless both exist. */
export function coldToFirstUsable(latencies: Record<string, number[]>): number | null {
  const cold = latencies.cold_backend_ready_ms?.[0]
  const first = latencies.first_shell_ready_ms?.[0]
  return cold === undefined || first === undefined ? null : cold + first
}

export type ProofProcess = { pid: number; created: number; category: string; component: string; ppid: number; executable: string; versionProbe?: boolean }
export type ProofSample = { wallMs: number; processes: ProofProcess[] }
export type Receipt = Record<string, any>
/** One `terminal.create` the driver made: what the WS reply and the child's own READY line reported. */
export type ProofTerminal = { tid: string; pane: string; vendor: string; label: string; createPid: number; peerPid: number }
export type ProofClaude = { pane: string; tid: string; activityPassed: boolean; liveTotalsPassed: boolean }
export type ProofInput = {
  mode: RuntimeMode
  backend: { pid: number; created: number }
  receipts: Receipt[]
  samples: ProofSample[]
  expectedSha256: string | null
  recomputedSha256: string | null
  terminals: ProofTerminal[]
  claude: ProofClaude[]
  claudeProjectsDir: string
}
/** Structured ownership/cleanup facts: the only basis for the S3 decision (never message text). Lists hold session ids (or root identities). */
export type OwnershipFacts = {
  missingCleaned: string[]; duplicateCleaned: string[]; orphanCleaned: string[]; duplicateOwned: string[]; noRootIdentity: string[]
  sharedRootIdentity: string[]; mismatchedCleaned: string[]; incompleteCleanup: string[]; unownedTerminals: string[]; unclaimedRoots: string[]
}
export type ProofVerdict = { ok: boolean; failures: string[]; facts: Record<string, unknown>; ownership: OwnershipFacts | null; ownershipFailure: boolean }

export const OWNERSHIP_KEYS = ['missingCleaned', 'duplicateCleaned', 'orphanCleaned', 'duplicateOwned', 'noRootIdentity', 'sharedRootIdentity',
  'mismatchedCleaned', 'incompleteCleanup', 'unownedTerminals', 'unclaimedRoots'] as const
const emptyOwnership = (): OwnershipFacts => ({
  missingCleaned: [], duplicateCleaned: [], orphanCleaned: [], duplicateOwned: [], noRootIdentity: [], sharedRootIdentity: [],
  mismatchedCleaned: [], incompleteCleanup: [], unownedTerminals: [], unclaimedRoots: [],
})
const hasOwnershipFailure = (facts: OwnershipFacts | null): boolean => !!facts && Object.values(facts).some((list) => list.length > 0)

const MAX_CHAIN = 16

/** True only for a normalised path strictly inside `dir`: no `.`/`..`/empty segments after the directory prefix. */
function insideDir(path: unknown, dir: string): path is string {
  if (typeof path !== 'string' || !path.startsWith(`${dir}/`)) return false
  return path.slice(dir.length + 1).split('/').every((segment) => segment !== '' && segment !== '.' && segment !== '..')
}
const CLAUDE_PARSER = 'rust-claude-jsonl-v1'

function firstSeen(samples: ProofSample[]): Map<number, ProofProcess> {
  const seen = new Map<number, ProofProcess>()
  for (const sample of samples) for (const process of sample.processes) if (!seen.has(process.pid)) seen.set(process.pid, process)
  return seen
}

/** True when an ancestor of `pid` is in `targets`; stops (false) on reaching a blocker first. */
function reaches(seen: Map<number, ProofProcess>, pid: number, targets: Set<number>, blockers: Set<number>): boolean {
  let current = seen.get(pid)?.ppid
  for (let depth = 0; current !== undefined && depth < MAX_CHAIN; depth++) {
    if (targets.has(current)) return true
    if (blockers.has(current)) return false
    current = seen.get(current)?.ppid
  }
  return false
}

const byKey = <T>(items: T[], key: (item: T) => string): Map<string, T[]> => {
  const grouped = new Map<string, T[]>()
  for (const item of items) grouped.set(key(item), [...(grouped.get(key(item)) ?? []), item])
  return grouped
}

/** Rust arm: every created terminal is bound to exactly one native-owned root, and each root to exactly one cleanup. */
function checkOwnership(input: ProofInput, handshake: Receipt, seen: Map<number, ProofProcess>, failures: string[], facts: Record<string, unknown>): { ownedBySession: Map<string, Receipt>; ownership: OwnershipFacts } {
  const ownership = emptyOwnership()
  const generation = handshake.runtime_generation
  const owned = input.receipts.filter((entry) => entry.event === 'owned')
  const cleaned = input.receipts.filter((entry) => entry.event === 'cleaned')
  facts.ownedReceipts = owned.length
  facts.cleanedReceipts = cleaned.length
  facts.createdTerminals = input.terminals.length
  for (const entry of input.receipts) {
    if (entry.event !== 'ready' && entry.runtime_generation !== generation) failures.push(`receipt ${entry.event} belongs to runtime generation ${entry.runtime_generation}, not the handshake generation`)
  }
  const ownedBySession = new Map<string, Receipt>()
  const identities = new Set<string>()
  for (const entry of owned) {
    const root = entry.root ?? {}
    if (typeof entry.session_id !== 'string' || !entry.session_id) { failures.push('owned receipt without a session id'); continue }
    if (ownedBySession.has(entry.session_id)) { failures.push(`owned: duplicate receipt for session ${entry.session_id}`); ownership.duplicateOwned.push(entry.session_id); continue }
    ownedBySession.set(entry.session_id, entry)
    if (!Number.isInteger(root.pid) || root.pid <= 0 || root.start_kind !== 'posix-lstart' || typeof root.start_value !== 'string' || !root.start_value) {
      failures.push(`owned: session ${entry.session_id} has no usable root birth identity`)
      ownership.noRootIdentity.push(entry.session_id)
      continue
    }
    const identity = `${root.pid}|${root.start_value}`
    if (identities.has(identity)) { failures.push(`owned: root identity ${identity} owns more than one session`); ownership.sharedRootIdentity.push(identity) }
    identities.add(identity)
  }
  const cleanedBySession = byKey(cleaned, (entry) => String(entry.session_id))
  for (const [session, entries] of cleanedBySession) {
    if (!ownedBySession.has(session)) { failures.push(`cleaned: receipt for unowned session ${session}`); ownership.orphanCleaned.push(session) }
    if (entries.length > 1) { failures.push(`cleaned: ${entries.length} receipts for session ${session}`); ownership.duplicateCleaned.push(session) }
  }
  const rootPids = new Set<number>()
  for (const [session, entry] of ownedBySession) {
    const done = cleanedBySession.get(session)?.[0]
    if (!done) { failures.push(`cleaned: no receipt for owned session ${session}`); ownership.missingCleaned.push(session); continue }
    if (done.session_generation !== entry.session_generation || done.pane_id !== entry.pane_id) { failures.push(`cleaned: session ${session} receipt does not match its owned generation/pane`); ownership.mismatchedCleaned.push(session) }
    // Missing or non-array survivors/unverifiable fields are an incomplete cleanup, never an empty one.
    if (done.root_reaped !== true || done.cleanup_complete !== true || !Array.isArray(done.survivors) || !Array.isArray(done.unverifiable) || done.survivors.length || done.unverifiable.length) { failures.push(`cleaned: session ${session} is not a complete reaped cleanup`); ownership.incompleteCleanup.push(session) }
    if (Number.isInteger(entry.root?.pid)) rootPids.add(entry.root.pid)
  }
  const bound = new Set<string>()
  for (const terminal of input.terminals) {
    const entry = ownedBySession.get(terminal.tid)
    if (!entry) { failures.push(`binding: terminal ${terminal.label} (${terminal.tid}) has no native owned receipt`); ownership.unownedTerminals.push(terminal.tid); continue }
    bound.add(terminal.tid)
    if (entry.pane_id !== terminal.pane) failures.push(`binding: terminal ${terminal.label} owned for pane ${entry.pane_id}, created for ${terminal.pane}`)
    const root = entry.root?.pid
    if (terminal.createPid !== root) failures.push(`binding: terminal ${terminal.label} create reply pid ${terminal.createPid} is not the native root ${root}`)
    if (terminal.peerPid !== root && !reaches(seen, terminal.peerPid, new Set([root]), new Set([input.backend.pid]))) failures.push(`binding: child of terminal ${terminal.label} (pid ${terminal.peerPid}) is neither the native root ${root} nor sampled below it`)
  }
  for (const session of ownedBySession.keys()) if (!bound.has(session)) { failures.push(`binding: native root for session ${session} was not created by a driver terminal.create`); ownership.unclaimedRoots.push(session) }
  const sidecars = [...seen.values()].filter((process) => process.component === 'sidecar')
  const sidecarPids = new Set(sidecars.map((process) => process.pid))
  for (const pid of rootPids) {
    const sampled = seen.get(pid)
    if (sampled && !sidecarPids.has(sampled.ppid)) failures.push(`binding: sampled native root ${pid} is not a child of the sidecar`)
  }
  for (const child of [...seen.values()].filter((process) => process.category === 'controlled_child' && !process.versionProbe)) {
    if (!rootPids.has(child.pid) && !reaches(seen, child.pid, rootPids, new Set([input.backend.pid]))) failures.push(`ancestry: controlled child ${child.pid} is not a native root or below one`)
  }
  return { ownedBySession, ownership }
}

/** Rust arm: the native Claude receipts must be the ones for the driver's Claude peers, not any other native read. */
function checkClaudeObservation(input: ProofInput, handshake: Receipt, ownedBySession: Map<string, Receipt>, failures: string[], facts: Record<string, unknown>): void {
  const claudeReceipts = input.receipts.filter((entry) => entry.event === 'observed' && entry.parser === CLAUDE_PARSER)
  facts.claudeObservationReceipts = claudeReceipts.length
  for (const entry of claudeReceipts) {
    if (!insideDir(entry.path, input.claudeProjectsDir)) failures.push(`observation: Claude receipt path is outside the owned projects dir`)
  }
  const bindings: Record<string, unknown>[] = []
  for (const peer of input.claude) {
    if (!peer.activityPassed && !peer.liveTotalsPassed) continue
    const owned = ownedBySession.get(peer.tid)
    const matches = (entry: Receipt, mode: string, requirePane: boolean): boolean =>
      entry.event === 'observed' && entry.parser === CLAUDE_PARSER && entry.mode === mode && Number(entry.event_count) > 0
      && entry.runtime_generation === handshake.runtime_generation && entry.native_session_id === peer.pane
      && insideDir(entry.path, input.claudeProjectsDir) && entry.path.endsWith(`/${peer.pane}.jsonl`)
      && (requirePane ? entry.pane_id === peer.pane && entry.session_id === peer.tid && !!owned && entry.session_generation === owned.session_generation
        : entry.pane_id == null || (entry.pane_id === peer.pane && entry.session_id === peer.tid && !!owned && entry.session_generation === owned.session_generation))
    if (peer.activityPassed) {
      const found = claudeReceipts.filter((entry) => matches(entry, 'activity', true)).length
      bindings.push({ pane: peer.pane, kind: 'activity', receipts: found })
      if (!found) failures.push(`observation: no native Claude activity receipt bound to pane ${peer.pane}, session ${peer.tid}, generation and path although its activity passed`)
    }
    if (peer.liveTotalsPassed) {
      const found = claudeReceipts.filter((entry) => matches(entry, 'usage', false)).length
      bindings.push({ pane: peer.pane, kind: 'usage', receipts: found })
      if (!found) failures.push(`observation: no native Claude usage receipt for ${peer.pane} although its live totals passed`)
    }
  }
  facts.claudeBindings = bindings
}

export function checkRuntimeProof(input: ProofInput): ProofVerdict {
  const failures: string[] = []
  const seen = firstSeen(input.samples)
  const processes = [...seen.values()]
  const sidecars = processes.filter((process) => process.component === 'sidecar')
  const backend = new Set([input.backend.pid])
  // Only the backend's own startup `--version` discovery shim, a direct child of the backend pid, is exempt from ancestry.
  const marked = processes.filter((process) => process.versionProbe)
  const terminalPids = new Set(input.terminals.flatMap((terminal) => [terminal.createPid, terminal.peerPid]))
  const probes = marked.map((process) => ({ pid: process.pid, ppid: process.ppid, exempt: process.ppid === input.backend.pid && !terminalPids.has(process.pid) }))
  for (const probe of probes) if (!probe.exempt) failures.push(`probe: version-probe-shaped process ${probe.pid} (parent ${probe.ppid}) is not the backend's own discovery child`)
  const exemptPids = new Set(probes.filter((probe) => probe.exempt).map((probe) => probe.pid))
  const controlled = processes.filter((process) => process.category === 'controlled_child' && !exemptPids.has(process.pid))
  const observations = input.receipts.filter((entry) => entry.event === 'observed')
  const facts: Record<string, unknown> = {
    mode: input.mode, sidecarPids: sidecars.map((process) => process.pid), controlledChildren: controlled.length, versionProbes: probes,
    observationReceipts: observations.length, createdTerminals: input.terminals.length,
  }
  if (!input.terminals.length) failures.push('binding: the trial created no terminals')

  if (input.mode === 'python') {
    if (sidecars.length) failures.push(`python arm sampled a sidecar process: ${sidecars.map((process) => process.pid).join(',')}`)
    if (input.receipts.length) failures.push(`python arm has ${input.receipts.length} runtime receipt(s) (including sidecar-owned roots)`)
    for (const child of controlled) if (!reaches(seen, child.pid, backend, new Set())) failures.push(`ancestry: controlled child ${child.pid} is not below the backend`)
    return { ok: failures.length === 0, failures, facts, ownership: null, ownershipFailure: false }
  }

  const ready = input.receipts.filter((entry) => entry.event === 'ready' && entry.owner_pid === input.backend.pid)
  facts.readyReceipts = ready.length
  if (ready.length !== 1) failures.push(`expected exactly one ready receipt with the backend as owner, got ${ready.length}`)
  const [handshake] = ready
  if (!handshake) {
    for (const child of controlled) failures.push(`ancestry: controlled child ${child.pid} cannot be attributed without a ready receipt`)
    return { ok: false, failures, facts, ownership: null, ownershipFailure: false }
  }
  if (handshake.protocol !== 1 || handshake.runtime !== 'navide-cli-runtime' || handshake.pty_backend !== 'posix' || !handshake.version) {
    failures.push(`protocol/identity mismatch in ready receipt: ${JSON.stringify({ protocol: handshake.protocol, runtime: handshake.runtime, pty_backend: handshake.pty_backend, version: handshake.version })}`)
  }
  if (!input.expectedSha256) failures.push('expected sidecar sha256 unknown')
  else if (handshake.executableSha256 !== input.expectedSha256) failures.push('ready executableSha256 differs from the expected build sha256')
  if (!input.recomputedSha256 || handshake.executableSha256 !== input.recomputedSha256) failures.push('sha256 recomputed from the executable file differs from the ready receipt')
  facts.sidecarSha256 = handshake.executableSha256
  facts.sidecarExecutable = handshake.executable
  if (sidecars.length !== 1 || sidecars[0].pid !== handshake.pid) failures.push(`sampled sidecar pids ${JSON.stringify(sidecars.map((process) => process.pid))} are not exactly the ready pid ${handshake.pid}`)
  for (const sidecar of sidecars) {
    if (sidecar.ppid !== input.backend.pid) failures.push(`sidecar ${sidecar.pid} parent is ${sidecar.ppid}, not the backend`)
    if (sidecar.executable !== handshake.executable) failures.push(`sidecar ${sidecar.pid} executable differs from the ready receipt`)
  }
  const { ownedBySession, ownership } = checkOwnership(input, handshake, seen, failures, facts)
  checkClaudeObservation(input, handshake, ownedBySession, failures, facts)
  return { ok: failures.length === 0, failures, facts, ownership, ownershipFailure: hasOwnershipFailure(ownership) }
}

const KNOWN_COMPONENTS = ['backend', 'sidecar', 'helper']
const DECISION_RESOURCE_PHASES = ['empty_idle', 'live_idle', 'empty_started_idle', 'input_output']
const ACTIVE_RESOURCE_PHASES = ['input_output']
const MIN_RESOURCE_SAMPLES = 10

export type CoverageSample = { wallMs: number; processes: Array<{ component: string }> }

/**
 * A required component cannot silently be absent from a trial's resource samples: the backend must be sampled in every row of each eligible
 * (passed, >= 10 samples) decision phase, the Rust sidecar in every row of the eligible active phase, and the Python arm may never show a sidecar.
 * Component names outside the known set are rejected anywhere.
 */
export function checkResourceCoverage(input: { mode: RuntimeMode; samples: CoverageSample[]; phases: Array<{ name: string; wallMs: number }>; phaseOutcomes: Record<string, string> }): string[] {
  const failures: string[] = []
  const rowsOf = (name: string): CoverageSample[] => {
    const index = input.phases.findIndex((phase) => phase.name === name)
    if (index < 0) return []
    const start = input.phases[index].wallMs
    const stop = index + 1 < input.phases.length ? input.phases[index + 1].wallMs : Number.POSITIVE_INFINITY
    return input.samples.filter((sample) => sample.wallMs >= start && sample.wallMs < stop)
  }
  if (input.samples.some((sample) => sample.processes.some((process) => !KNOWN_COMPONENTS.includes(process.component)))) failures.push('resources: unknown component name in a sample')
  if (input.mode === 'python' && input.samples.some((sample) => sample.processes.some((process) => process.component === 'sidecar'))) failures.push('resources: the python arm shows a sidecar component')
  for (const name of DECISION_RESOURCE_PHASES) {
    if (input.phaseOutcomes[name] !== 'passed') continue
    const rows = rowsOf(name)
    if (rows.length < MIN_RESOURCE_SAMPLES) continue
    if (rows.some((row) => !row.processes.some((process) => process.component === 'backend'))) failures.push(`resources: the backend is missing from rows of the eligible ${name} phase`)
    if (input.mode === 'rust-shell' && ACTIVE_RESOURCE_PHASES.includes(name) && rows.some((row) => !row.processes.some((process) => process.component === 'sidecar'))) failures.push(`resources: the sidecar is missing from rows of the eligible ${name} phase`)
  }
  return failures
}

/** Folds resource-coverage failures into a proof verdict (a failed proof makes the evidence invalid); an unchanged verdict is returned as is. */
export function mergeCoverage(verdict: ProofVerdict, coverageFailures: string[]): ProofVerdict {
  return coverageFailures.length === 0 ? verdict : { ...verdict, ok: false, failures: [...verdict.failures, ...coverageFailures] }
}
