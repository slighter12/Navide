import { describe, expect, it } from 'vitest'
import {
  checkResourceCoverage, checkRuntimeProof, coldToFirstUsable, matchedOrder, mergeCoverage, type ProofInput, type ProofProcess,
} from './runtimeMatched'

const SHA = 'a'.repeat(64)
const BACKEND = { pid: 100, created: 1000.5 }
const EXE = '/repo/native/navide-cli-runtime/target/release/navide-cli-runtime'
const ROOT = '/tmp/navide-runtime-baseline-X'
const PROJECTS = `${ROOT}/home/.claude/projects`
const GEN = 'gen-1'
const PANE = 'pane-claude'

const proc = (pid: number, ppid: number, extra: Partial<ProofProcess> = {}): ProofProcess =>
  ({ pid, created: 1000 + pid, category: 'runtime', component: 'helper', ppid, executable: '/py', ...extra })

type Spec = { tid: string; pane: string; vendor: string; label: string; root: number }
const SPECS: Spec[] = [
  { tid: 's1', pane: 'pane-shell-1', vendor: 'terminal', label: 'first_shell', root: 301 },
  { tid: 's2', pane: 'pane-shell-2', vendor: 'terminal', label: 'warm_shell', root: 302 },
  { tid: 's3', pane: PANE, vendor: 'claude', label: 'agent', root: 303 },
]

function rustInput(): ProofInput {
  const receipts: ProofInput['receipts'] = [
    { event: 'ready', owner_pid: 100, pid: 200, protocol: 1, runtime: 'navide-cli-runtime', pty_backend: 'posix', version: '0.1.0', executable: EXE, executableSha256: SHA, runtime_generation: GEN },
  ]
  for (const spec of SPECS) {
    receipts.push({ event: 'owned', session_id: spec.tid, session_generation: `g-${spec.tid}`, pane_id: spec.pane, runtime_generation: GEN, root: { pid: spec.root, pgid: spec.root, start_kind: 'posix-lstart', start_value: `t${spec.root}` } })
    receipts.push({ event: 'cleaned', session_id: spec.tid, session_generation: `g-${spec.tid}`, pane_id: spec.pane, runtime_generation: GEN, root_reaped: true, cleanup_complete: true, survivors: [], unverifiable: [] })
  }
  for (const [mode, pane] of [['activity', PANE], ['usage', PANE]] as const) {
    receipts.push({ event: 'observed', parser: 'rust-claude-jsonl-v1', mode, pane_id: mode === 'activity' ? pane : null, session_id: mode === 'activity' ? 's3' : null, session_generation: mode === 'activity' ? 'g-s3' : null, runtime_generation: GEN, native_session_id: pane, path: `${PROJECTS}/-enc/${pane}.jsonl`, event_count: 2 })
  }
  receipts.push({ event: 'observed', parser: 'rust-opencode-sqlite-v1', mode: 'activity', pane_id: null, runtime_generation: GEN, path: `${ROOT}/share/opencode/opencode.db`, event_count: 0 })
  return {
    mode: 'rust-shell', backend: BACKEND, expectedSha256: SHA, recomputedSha256: SHA, receipts,
    terminals: SPECS.map((spec) => ({ tid: spec.tid, pane: spec.pane, vendor: spec.vendor, label: spec.label, createPid: spec.root, peerPid: spec.root })),
    claude: [{ pane: PANE, tid: 's3', activityPassed: true, liveTotalsPassed: true }],
    claudeProjectsDir: PROJECTS,
    samples: [{ wallMs: 1, processes: [proc(100, 1, { component: 'backend' }), proc(200, 100, { component: 'sidecar', executable: EXE }), proc(303, 200, { category: 'controlled_child' })] }],
  }
}

const sampled = (input: ProofInput): ProofProcess[] => input.samples[0].processes
const receipt = (input: ProofInput, predicate: (entry: Record<string, any>) => boolean): Record<string, any> => input.receipts.find(predicate)!
const failureText = (input: ProofInput): string => checkRuntimeProof(input).failures.join('\n')

describe('matchedOrder', () => {
  it('interleaves modes by repeat, alternating which mode goes first, counts innermost', () => {
    expect(matchedOrder(2, [1, 4], ['python', 'rust-shell'])).toEqual([
      { repeat: 0, mode: 'python', count: 1 }, { repeat: 0, mode: 'python', count: 4 },
      { repeat: 0, mode: 'rust-shell', count: 1 }, { repeat: 0, mode: 'rust-shell', count: 4 },
      { repeat: 1, mode: 'rust-shell', count: 1 }, { repeat: 1, mode: 'rust-shell', count: 4 },
      { repeat: 1, mode: 'python', count: 1 }, { repeat: 1, mode: 'python', count: 4 },
    ])
  })

  it('has 20 attempts for the 5-repeat matrix with five of each mode/count', () => {
    const order = matchedOrder(5, [1, 4], ['python', 'rust-shell'])
    expect(order).toHaveLength(20)
    for (const mode of ['python', 'rust-shell']) for (const count of [1, 4]) {
      expect(order.filter((entry) => entry.mode === mode && entry.count === count).map((entry) => entry.repeat)).toEqual([0, 1, 2, 3, 4])
    }
  })

  it('runs a single requested mode without reordering its repeats', () => {
    expect(matchedOrder(1, [1, 4], ['rust-shell'])).toEqual([
      { repeat: 0, mode: 'rust-shell', count: 1 }, { repeat: 0, mode: 'rust-shell', count: 4 },
    ])
  })
})

describe('coldToFirstUsable', () => {
  it('adds the cold backend readiness and the first shell READY of the same trial', () => {
    expect(coldToFirstUsable({ cold_backend_ready_ms: [450.5], first_shell_ready_ms: [40.25] })).toBeCloseTo(490.75)
  })

  it('is null rather than a partial value when either part is missing', () => {
    expect(coldToFirstUsable({ cold_backend_ready_ms: [450] })).toBeNull()
    expect(coldToFirstUsable({ first_shell_ready_ms: [40] })).toBeNull()
  })
})

describe('checkRuntimeProof (Rust arm)', () => {
  it('accepts a complete bundle where every created terminal is bound to one native root even if its shell is never sampled', () => {
    const verdict = checkRuntimeProof(rustInput())
    expect(verdict.failures).toEqual([])
    expect(verdict.facts).toMatchObject({ sidecarPids: [200], ownedReceipts: 3, cleanedReceipts: 3, createdTerminals: 3, readyReceipts: 1 })
  })

  it.each([
    ['no ready receipt', (input: ProofInput) => { input.receipts = input.receipts.filter((entry) => entry.event !== 'ready') }, 'ready'],
    ['two ready receipts for the backend', (input: ProofInput) => { input.receipts.push({ ...input.receipts[0] }) }, 'ready'],
    ['ready owned by another pid', (input: ProofInput) => { input.receipts[0].owner_pid = 999 }, 'ready'],
    ['ready hash differs from expected build', (input: ProofInput) => { input.expectedSha256 = 'b'.repeat(64) }, 'expected build'],
    ['recomputed file hash differs from ready hash', (input: ProofInput) => { input.recomputedSha256 = 'c'.repeat(64) }, 'recomputed'],
    ['unknown expected hash', (input: ProofInput) => { input.expectedSha256 = null }, 'unknown'],
    ['protocol mismatch', (input: ProofInput) => { input.receipts[0].protocol = 2 }, 'protocol'],
    ['sampled sidecar pid is not the ready pid', (input: ProofInput) => { sampled(input)[1].pid = 201 }, 'sidecar'],
    ['sidecar not a child of the backend', (input: ProofInput) => { sampled(input)[1].ppid = 1 }, 'not the backend'],
    ['second sampled sidecar', (input: ProofInput) => { sampled(input).push(proc(210, 100, { component: 'sidecar', executable: EXE })) }, 'sidecar'],
  ])('rejects %s', (_name, mutate, fragment) => {
    const input = rustInput()
    mutate(input)
    const verdict = checkRuntimeProof(input)
    expect(verdict.ok).toBe(false)
    expect(verdict.failures.join('\n').toLowerCase()).toContain(fragment.toLowerCase())
  })
})

describe('checkRuntimeProof ownership binding (Rust arm)', () => {
  it('rejects two cleaned receipts for one root while another root has none', () => {
    const input = rustInput()
    const second = input.receipts.findIndex((entry) => entry.event === 'cleaned' && entry.session_id === 's2')
    input.receipts[second] = { ...input.receipts[second - 0], session_id: 's1' }
    const text = failureText(input)
    expect(text).toContain('cleaned: 2 receipts for session s1')
    expect(text).toContain('cleaned: no receipt for owned session s2')
  })

  it('rejects an owned root with no cleaned receipt', () => {
    const input = rustInput()
    input.receipts = input.receipts.filter((entry) => !(entry.event === 'cleaned' && entry.session_id === 's3'))
    expect(failureText(input)).toContain('cleaned: no receipt for owned session s3')
  })

  it('rejects a cleaned receipt for a session that was never owned', () => {
    const input = rustInput()
    input.receipts.push({ event: 'cleaned', session_id: 'ghost', root_reaped: true, cleanup_complete: true, survivors: [], unverifiable: [], runtime_generation: GEN })
    expect(failureText(input)).toContain('unowned session ghost')
  })

  it('rejects duplicate owned receipts for one session', () => {
    const input = rustInput()
    input.receipts.push({ ...receipt(input, (entry) => entry.event === 'owned' && entry.session_id === 's1') })
    expect(failureText(input)).toContain('owned: duplicate receipt for session s1')
  })

  it('rejects two sessions sharing one root birth identity', () => {
    const input = rustInput()
    receipt(input, (entry) => entry.event === 'owned' && entry.session_id === 's2').root = { pid: 301, start_kind: 'posix-lstart', start_value: 't301' }
    expect(failureText(input)).toContain('root identity 301|t301 owns more than one session')
  })

  it('rejects an owned root without a usable birth identity', () => {
    const input = rustInput()
    delete receipt(input, (entry) => entry.event === 'owned' && entry.session_id === 's1').root.start_value
    expect(failureText(input)).toContain('no usable root birth identity')
  })

  it('rejects a cleanup that is incomplete, unreaped or leaves survivors', () => {
    for (const patch of [{ root_reaped: false }, { cleanup_complete: false }, { survivors: [{ pid: 9 }] }, { unverifiable: [{ pid: 9 }] }]) {
      const input = rustInput()
      Object.assign(receipt(input, (entry) => entry.event === 'cleaned' && entry.session_id === 's2'), patch)
      expect(failureText(input)).toContain('not a complete reaped cleanup')
    }
  })

  it('rejects a cleaned receipt whose generation or pane differs from its owned receipt', () => {
    const input = rustInput()
    receipt(input, (entry) => entry.event === 'cleaned' && entry.session_id === 's1').session_generation = 'other'
    expect(failureText(input)).toContain('does not match its owned generation/pane')
  })

  it('rejects a receipt from another runtime generation', () => {
    const input = rustInput()
    receipt(input, (entry) => entry.event === 'owned' && entry.session_id === 's1').runtime_generation = 'gen-0'
    expect(failureText(input)).toContain('runtime generation gen-0')
  })

  it('rejects a created terminal (even a never-sampled shell) with no native owned receipt', () => {
    const input = rustInput()
    input.terminals.push({ tid: 's4', pane: 'pane-shell-4', vendor: 'terminal', label: 'extra_shell', createPid: 399, peerPid: 399 })
    expect(failureText(input)).toContain('terminal extra_shell (s4) has no native owned receipt')
  })

  it('rejects a native root that no driver create produced', () => {
    const input = rustInput()
    input.terminals = input.terminals.filter((terminal) => terminal.tid !== 's2')
    expect(failureText(input)).toContain('native root for session s2 was not created by a driver terminal.create')
  })

  it('rejects a create reply pid that is not the native root pid', () => {
    const input = rustInput()
    input.terminals[0].createPid = 777
    expect(failureText(input)).toContain('create reply pid 777 is not the native root 301')
  })

  it('rejects a terminal owned for a different pane', () => {
    const input = rustInput()
    input.terminals[1].pane = 'other-pane'
    expect(failureText(input)).toContain('owned for pane pane-shell-2, created for other-pane')
  })

  it('rejects a child pid that is neither the native root nor sampled below it (a Python PTY child shape)', () => {
    const input = rustInput()
    input.terminals[0].peerPid = 555
    expect(failureText(input)).toContain('child of terminal first_shell (pid 555) is neither the native root 301 nor sampled below it')
  })

  it('accepts a child that is sampled below its native root', () => {
    const input = rustInput()
    input.terminals[0].peerPid = 556
    sampled(input).push(proc(301, 200), proc(556, 301, { category: 'controlled_child' }))
    expect(checkRuntimeProof(input).failures).toEqual([])
  })

  it('rejects a sampled native root that is not a child of the sidecar', () => {
    const input = rustInput()
    sampled(input).push(proc(301, 100))
    expect(failureText(input)).toContain('sampled native root 301 is not a child of the sidecar')
  })

  it('rejects a controlled child attached directly to the backend (Python PTY shape)', () => {
    const input = rustInput()
    sampled(input).push(proc(303 + 100, 100, { category: 'controlled_child' }))
    expect(failureText(input)).toContain('ancestry: controlled child 403')
  })

  it('rejects a controlled child whose chain never reaches a native root', () => {
    const input = rustInput()
    sampled(input).push(proc(404, 1, { category: 'controlled_child' }))
    expect(failureText(input)).toContain('ancestry: controlled child 404')
  })

  it('rejects a trial that created no terminal', () => {
    const input = rustInput()
    input.terminals = []
    input.claude = []
    expect(failureText(input)).toContain('created no terminals')
  })
})

describe('checkRuntimeProof version-probe exemption', () => {
  it('exempts only the backend\'s own discovery child and reports it', () => {
    const input = rustInput()
    sampled(input).push(proc(306, 100, { category: 'controlled_child', versionProbe: true }))
    const verdict = checkRuntimeProof(input)
    expect(verdict.failures).toEqual([])
    expect(verdict.facts).toMatchObject({ versionProbes: [{ pid: 306, ppid: 100, exempt: true }], controlledChildren: 1 })
  })

  it('rejects and reports a probe-shaped process under any other parent', () => {
    const input = rustInput()
    sampled(input).push(proc(307, 200, { category: 'controlled_child', versionProbe: true }))
    const verdict = checkRuntimeProof(input)
    expect(verdict.ok).toBe(false)
    expect(verdict.failures.join('\n')).toContain('version-probe-shaped process 307 (parent 200)')
    expect(verdict.facts).toMatchObject({ versionProbes: [{ pid: 307, ppid: 200, exempt: false }] })
  })

  it('rejects a probe-shaped process whose pid is a created terminal\'s child', () => {
    const input = rustInput()
    sampled(input).push(proc(303, 100, { category: 'controlled_child', versionProbe: true }))
    sampled(input).splice(2, 1)
    expect(failureText(input)).toContain('version-probe-shaped process 303')
  })

  it('does not require probes to exist and counts several honest ones', () => {
    const input = rustInput()
    sampled(input).push(proc(308, 100, { category: 'controlled_child', versionProbe: true }), proc(309, 100, { category: 'controlled_child', versionProbe: true }))
    expect(checkRuntimeProof(input).failures).toEqual([])
    expect((checkRuntimeProof(input).facts.versionProbes as unknown[]).length).toBe(2)
  })
})

describe('checkRuntimeProof native Claude observation', () => {
  const claudeReceipts = (input: ProofInput) => input.receipts.filter((entry) => entry.parser === 'rust-claude-jsonl-v1')

  it.each([
    ['wrong parser', (entry: Record<string, any>) => { entry.parser = 'rust-opencode-sqlite-v1' }, 'no native Claude activity receipt'],
    ['wrong pane', (entry: Record<string, any>) => { entry.pane_id = 'other' }, 'no native Claude activity receipt'],
    ['wrong session', (entry: Record<string, any>) => { entry.session_id = 'other' }, 'no native Claude activity receipt'],
    ['wrong session generation', (entry: Record<string, any>) => { entry.session_generation = 'other' }, 'no native Claude activity receipt'],
    ['wrong runtime generation', (entry: Record<string, any>) => { entry.runtime_generation = 'gen-0' }, 'no native Claude activity receipt'],
    ['different native session id', (entry: Record<string, any>) => { entry.native_session_id = 'other' }, 'no native Claude activity receipt'],
    ['zero events', (entry: Record<string, any>) => { entry.event_count = 0 }, 'no native Claude activity receipt'],
    ['wrong mode', (entry: Record<string, any>) => { entry.mode = 'usage' }, 'no native Claude activity receipt'],
    ['path of another file', (entry: Record<string, any>) => { entry.path = `${PROJECTS}/-enc/other.jsonl` }, 'no native Claude activity receipt'],
    ['path outside the owned projects dir', (entry: Record<string, any>) => { entry.path = `/home/user/.claude/projects/-enc/${PANE}.jsonl` }, 'outside the owned projects dir'],
  ])('rejects an activity receipt with %s', (_name, mutate, fragment) => {
    const input = rustInput()
    mutate(claudeReceipts(input).find((entry) => entry.mode === 'activity')!)
    expect(failureText(input)).toContain(fragment)
  })

  it('does not let an unrelated OpenCode or other-pane native activity receipt satisfy a Claude peer', () => {
    const input = rustInput()
    input.receipts = input.receipts.filter((entry) => entry.parser !== 'rust-claude-jsonl-v1')
    input.receipts.push({ event: 'observed', parser: 'rust-opencode-sqlite-v1', mode: 'activity', pane_id: PANE, session_id: 's3', session_generation: 'g-s3', runtime_generation: GEN, native_session_id: PANE, path: `${PROJECTS}/-enc/${PANE}.jsonl`, event_count: 5 })
    expect(failureText(input)).toContain('no native Claude activity receipt')
  })

  it('requires a usage receipt when live totals passed, but not when they did not', () => {
    const input = rustInput()
    input.receipts = input.receipts.filter((entry) => !(entry.parser === 'rust-claude-jsonl-v1' && entry.mode === 'usage'))
    expect(failureText(input)).toContain('no native Claude usage receipt')
    input.claude[0].liveTotalsPassed = false
    expect(checkRuntimeProof(input).failures).toEqual([])
  })

  it('does not require Claude receipts when no Claude outcome passed', () => {
    const input = rustInput()
    input.claude[0].activityPassed = false
    input.claude[0].liveTotalsPassed = false
    input.receipts = input.receipts.filter((entry) => entry.parser !== 'rust-claude-jsonl-v1')
    expect(checkRuntimeProof(input).failures).toEqual([])
  })
})

describe('checkRuntimeProof (Python arm)', () => {
  const pythonInput = (): ProofInput => ({
    mode: 'python', backend: BACKEND, expectedSha256: null, recomputedSha256: null, receipts: [],
    terminals: SPECS.map((spec) => ({ tid: spec.tid, pane: spec.pane, vendor: spec.vendor, label: spec.label, createPid: spec.root, peerPid: spec.root })),
    claude: [{ pane: PANE, tid: 's3', activityPassed: true, liveTotalsPassed: true }], claudeProjectsDir: PROJECTS,
    samples: [{ wallMs: 1, processes: [proc(100, 1, { component: 'backend' }), proc(300, 100), proc(303, 300, { category: 'controlled_child' })] }],
  })

  it('accepts no sidecar, no receipts, children below the backend', () => {
    expect(checkRuntimeProof(pythonInput()).failures).toEqual([])
  })

  it('rejects any sidecar process', () => {
    const input = pythonInput()
    sampled(input).push(proc(200, 100, { component: 'sidecar', executable: EXE }))
    expect(failureText(input)).toContain('sidecar')
  })

  it.each([
    ['ready', { event: 'ready' }],
    ['a sidecar-owned root', { event: 'owned', session_id: 's1', root: { pid: 301 } }],
    ['a native observation', { event: 'observed', parser: 'rust-claude-jsonl-v1' }],
  ])('rejects %s', (_name, entry) => {
    const input = pythonInput()
    input.receipts = [entry]
    expect(failureText(input)).toContain('runtime receipt')
  })

  it('rejects a controlled child that is not a descendant of the backend', () => {
    const input = pythonInput()
    sampled(input).push(proc(305, 1, { category: 'controlled_child' }))
    expect(failureText(input)).toContain('ancestry')
  })

  it('applies the same narrowed probe rule', () => {
    const input = pythonInput()
    sampled(input).push(proc(310, 100, { category: 'controlled_child', versionProbe: true }), proc(311, 300, { category: 'controlled_child', versionProbe: true }))
    const verdict = checkRuntimeProof(input)
    expect(verdict.failures.join('\n')).toContain('process 311 (parent 300)')
    expect(verdict.failures.join('\n')).not.toContain('process 310')
  })
})

describe('structured ownership facts (the only basis for the S3 decision)', () => {
  const ownership = (input: ProofInput) => checkRuntimeProof(input)

  it('reports an all-empty ownership record and no ownership failure on a clean Rust trial', () => {
    const verdict = ownership(rustInput())
    expect(verdict.ownershipFailure).toBe(false)
    expect(verdict.ownership).toEqual({
      missingCleaned: [], duplicateCleaned: [], orphanCleaned: [], duplicateOwned: [], noRootIdentity: [], sharedRootIdentity: [],
      mismatchedCleaned: [], incompleteCleanup: [], unownedTerminals: [], unclaimedRoots: [],
    })
  })

  it.each([
    ['a missing cleaned receipt', (i: ProofInput) => { i.receipts = i.receipts.filter((e) => !(e.event === 'cleaned' && e.session_id === 's3')) }, { missingCleaned: ['s3'] }],
    ['a duplicate cleaned receipt', (i: ProofInput) => { i.receipts.push({ ...receipt(i, (e) => e.event === 'cleaned' && e.session_id === 's1') }) }, { duplicateCleaned: ['s1'] }],
    ['an orphan cleaned receipt', (i: ProofInput) => { i.receipts.push({ event: 'cleaned', session_id: 'ghost', runtime_generation: GEN, root_reaped: true, cleanup_complete: true, survivors: [], unverifiable: [] }) }, { orphanCleaned: ['ghost'] }],
    ['a duplicate owned receipt', (i: ProofInput) => { i.receipts.push({ ...receipt(i, (e) => e.event === 'owned' && e.session_id === 's1') }) }, { duplicateOwned: ['s1'] }],
    ['an owned root without a birth identity', (i: ProofInput) => { delete receipt(i, (e) => e.event === 'owned' && e.session_id === 's1').root.start_value }, { noRootIdentity: ['s1'] }],
    ['two sessions sharing one root identity', (i: ProofInput) => { receipt(i, (e) => e.event === 'owned' && e.session_id === 's2').root = { pid: 301, start_kind: 'posix-lstart', start_value: 't301' } }, { sharedRootIdentity: ['301|t301'] }],
    ['a cleaned receipt of another generation/pane', (i: ProofInput) => { receipt(i, (e) => e.event === 'cleaned' && e.session_id === 's1').session_generation = 'other' }, { mismatchedCleaned: ['s1'] }],
    ['a survivor listed in a cleanup', (i: ProofInput) => { receipt(i, (e) => e.event === 'cleaned' && e.session_id === 's2').survivors = [{ pid: 9 }] }, { incompleteCleanup: ['s2'] }],
    ['an unreaped root', (i: ProofInput) => { receipt(i, (e) => e.event === 'cleaned' && e.session_id === 's2').root_reaped = false }, { incompleteCleanup: ['s2'] }],
    ['an unverifiable cleanup', (i: ProofInput) => { receipt(i, (e) => e.event === 'cleaned' && e.session_id === 's2').unverifiable = [{ pid: 9 }] }, { incompleteCleanup: ['s2'] }],
    ['a created terminal with no owned root', (i: ProofInput) => { i.terminals.push({ tid: 's4', pane: 'p4', vendor: 'terminal', label: 'extra_shell', createPid: 399, peerPid: 399 }) }, { unownedTerminals: ['s4'] }],
    ['an owned root no driver create produced', (i: ProofInput) => { i.terminals = i.terminals.filter((t) => t.tid !== 's2') }, { unclaimedRoots: ['s2'] }],
  ])('flags %s structurally as an ownership failure', (_name, mutate, expected) => {
    const input = rustInput()
    mutate(input)
    const verdict = ownership(input)
    expect(verdict.ok).toBe(false)
    expect(verdict.ownershipFailure).toBe(true)
    expect(verdict.ownership).toMatchObject(expected)
  })

  it.each([
    ['a wrong expected build hash', (i: ProofInput) => { i.expectedSha256 = 'b'.repeat(64) }],
    ['an unreadable/unknown recomputed hash', (i: ProofInput) => { i.recomputedSha256 = null }],
    ['a create reply pid that is not the native root', (i: ProofInput) => { i.terminals[0].createPid = 777 }],
    ['a terminal bound to another pane', (i: ProofInput) => { i.terminals[1].pane = 'other' }],
    ['a controlled child with no native root above it', (i: ProofInput) => { sampled(i).push(proc(404, 1, { category: 'controlled_child' })) }],
    ['a Claude receipt for another pane', (i: ProofInput) => { receipt(i, (e) => e.parser === 'rust-claude-jsonl-v1' && e.mode === 'activity').pane_id = 'other' }],
    ['a probe-shaped process below the sidecar', (i: ProofInput) => { sampled(i).push(proc(307, 200, { category: 'controlled_child', versionProbe: true })) }],
  ])('keeps %s an invalid-evidence failure that is NOT an ownership (S3) failure', (_name, mutate) => {
    const input = rustInput()
    mutate(input)
    const verdict = ownership(input)
    expect(verdict.ok).toBe(false)
    expect(verdict.ownershipFailure).toBe(false)
  })

  it('has no ownership record in the Python arm and never an ownership failure there', () => {
    const python: ProofInput = { ...rustInput(), mode: 'python', receipts: [{ event: 'owned', session_id: 's1', root: { pid: 301 } }] }
    const verdict = ownership(python)
    expect(verdict.ok).toBe(false)
    expect(verdict.ownership).toBeNull()
    expect(verdict.ownershipFailure).toBe(false)
  })

  it('has no ownership record when there is no ready receipt (nothing to bind to) and is not S3', () => {
    const input = rustInput()
    input.receipts = input.receipts.filter((e) => e.event !== 'ready')
    const verdict = ownership(input)
    expect(verdict.ok).toBe(false)
    expect(verdict.ownership).toBeNull()
    expect(verdict.ownershipFailure).toBe(false)
  })
})

describe('fail-closed receipt reading (review 3 sweep)', () => {
  it('rejects a Claude receipt path that climbs out of the projects dir through a dot-dot segment', () => {
    const input = rustInput()
    receipt(input, (e) => e.parser === 'rust-claude-jsonl-v1' && e.mode === 'activity').path = `${PROJECTS}/../../elsewhere/${PANE}.jsonl`
    expect(failureText(input)).toContain('outside the owned projects dir')
    const dotted = rustInput()
    for (const entry of dotted.receipts.filter((e) => e.parser === 'rust-claude-jsonl-v1')) entry.path = `${PROJECTS}/./-enc/${PANE}.jsonl`
    expect(failureText(dotted)).toContain('outside the owned projects dir')
  })

  it.each([
    ['survivors key absent', (e: Record<string, any>) => { delete e.survivors }],
    ['unverifiable key absent', (e: Record<string, any>) => { delete e.unverifiable }],
    ['survivors not an array', (e: Record<string, any>) => { e.survivors = null }],
    ['unverifiable not an array', (e: Record<string, any>) => { e.unverifiable = 'none' }],
    ['root_reaped missing', (e: Record<string, any>) => { delete e.root_reaped }],
    ['cleanup_complete missing', (e: Record<string, any>) => { delete e.cleanup_complete }],
  ])('treats a cleaned receipt with %s as an incomplete cleanup, never as a clean one', (_name, mutate) => {
    const input = rustInput()
    mutate(receipt(input, (e) => e.event === 'cleaned' && e.session_id === 's2'))
    const verdict = checkRuntimeProof(input)
    expect(verdict.ok).toBe(false)
    expect(verdict.ownership?.incompleteCleanup).toEqual(['s2'])
    expect(verdict.ownershipFailure).toBe(true)
  })

  it('treats a non-array receipt stream or a missing ready handshake field as a failure, not an exception', () => {
    const input = rustInput()
    receipt(input, (e) => e.event === 'ready').version = ''
    expect(checkRuntimeProof(input).ok).toBe(false)
  })
})

describe('checkResourceCoverage (backend and sidecar presence per eligible phase)', () => {
  const PHASES = ['empty_idle', 'live_idle', 'input_output', 'burst', 'empty_started_idle']
  const phases = PHASES.map((name, index) => ({ name, wallMs: index * 1000 }))
  const outcomes = Object.fromEntries(PHASES.map((name) => [name, 'passed']))
  const row = (wallMs: number, components: string[]) => ({ wallMs, processes: components.map((component, index) => ({ component, pid: index + 1 })) })
  const rows = (phaseIndex: number, count: number, components: string[]) => Array.from({ length: count }, (_, i) => row(phaseIndex * 1000 + i * 50, components))
  const good = (mode: 'python' | 'rust-shell') => PHASES.flatMap((_, index) => rows(index, 12, mode === 'rust-shell' ? ['backend', 'sidecar', 'helper'] : ['backend', 'helper']))
  const check = (mode: 'python' | 'rust-shell', samples: ReturnType<typeof good>, out: Record<string, string> = outcomes) => checkResourceCoverage({ mode, samples, phases, phaseOutcomes: out })

  it('accepts a complete trial in both arms', () => {
    expect(check('rust-shell', good('rust-shell'))).toEqual([])
    expect(check('python', good('python'))).toEqual([])
  })

  it('requires the backend in every row of every eligible decision resource phase', () => {
    for (const index of [0, 1, 2, 4]) {
      const samples = good('rust-shell')
      const target = samples.findIndex((sample) => sample.wallMs >= index * 1000 && sample.wallMs < (index + 1) * 1000)
      samples[target] = row(samples[target].wallMs, ['sidecar', 'helper'])
      expect(check('rust-shell', samples).join('\n')).toContain(`backend is missing from rows of the eligible ${PHASES[index]} phase`)
    }
  })

  it('does not require coverage in a phase with fewer than ten samples or one that did not pass, nor in non-decision phases', () => {
    const few = [...good('rust-shell').filter((s) => s.wallMs < 2000 || s.wallMs >= 3000), ...rows(2, 9, ['sidecar'])]
    expect(check('rust-shell', few)).toEqual([])
    const noBackendActive = [...good('rust-shell').filter((s) => s.wallMs < 2000 || s.wallMs >= 3000), ...rows(2, 12, ['sidecar'])]
    expect(check('rust-shell', noBackendActive, { ...outcomes, input_output: 'failed' })).toEqual([])
    const burst = [...good('rust-shell').filter((s) => s.wallMs < 3000 || s.wallMs >= 4000), ...rows(3, 12, ['helper'])]
    expect(check('rust-shell', burst)).toEqual([])
  })

  it('requires the Rust sidecar in every row of the eligible active phase and forbids any sidecar in the Python arm', () => {
    const samples = good('rust-shell').map((sample) => (sample.wallMs >= 2000 && sample.wallMs < 3000 ? row(sample.wallMs, ['backend', 'helper']) : sample))
    expect(check('rust-shell', samples).join('\n')).toContain('sidecar is missing from rows of the eligible input_output phase')
    const idleOnly = good('rust-shell').map((sample) => (sample.wallMs < 1000 ? row(sample.wallMs, ['backend']) : sample))
    expect(check('rust-shell', idleOnly)).toEqual([])
    const python = good('python')
    python[3] = row(python[3].wallMs, ['backend', 'sidecar'])
    expect(check('python', python).join('\n')).toContain('python arm shows a sidecar component')
  })

  it('rejects unknown or blank component names anywhere', () => {
    for (const bad of ['gpu', '', 'Backend']) {
      const samples = good('rust-shell')
      samples[5] = row(samples[5].wallMs, ['backend', 'sidecar', bad])
      expect(check('rust-shell', samples).join('\n')).toContain('unknown component')
    }
  })

  it('mergeCoverage turns coverage failures into a failed proof and leaves a clean proof untouched', () => {
    const verdict = { ok: true, failures: [], facts: { mode: 'rust-shell' }, ownership: null, ownershipFailure: false }
    expect(mergeCoverage(verdict, [])).toBe(verdict)
    const merged = mergeCoverage(verdict, ['resources: x'])
    expect(merged.ok).toBe(false)
    expect(merged.failures).toEqual(['resources: x'])
    expect(merged.ownershipFailure).toBe(false)
  })
})
