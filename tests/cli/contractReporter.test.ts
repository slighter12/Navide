import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CliContractReporter, { lifecycleErrorClass, missingContracts, missingRuntimeReferences } from './contractReporter'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { types } from 'node:util'
import { AGENT_SPECS } from '../../src/renderer/src/platform/plugin-shell/agents'
import { buildResumeCommand } from '../../src/renderer/src/platform/plugin-shell/lib/resume-command'

describe('native lifecycle metadata classification', () => {
  it('never invokes an own or inherited name getter', () => {
    let calls = 0
    const prototype = Object.defineProperty({}, 'name', { get() { calls++; throw new Error('synthetic') } })
    for (const value of [prototype, Object.create(prototype)]) expect(lifecycleErrorClass(value)).toBe('unknown')
    expect(calls).toBe(0)
  })
  it('rejects live and revoked proxies before reflection', () => {
    let calls = 0
    const live = new Proxy({}, { get() { calls++; throw 1 }, getOwnPropertyDescriptor() { calls++; throw 1 }, getPrototypeOf() { calls++; throw 1 } })
    const revoked = Proxy.revocable({}, {}); revoked.revoke()
    expect(types.isProxy(live)).toBe(true)
    expect(lifecycleErrorClass(live)).toBe('unknown')
    expect(lifecycleErrorClass(revoked.proxy)).toBe('unknown')
    expect(calls).toBe(0)
  })
  it('emits only fixed classes without reading payload fields', () => {
    let calls = 0
    const value = { name: 'AssertionError', get message() { calls++; throw 1 }, get stack() { calls++; throw 1 }, get constructor() { calls++; throw 1 } }
    expect(lifecycleErrorClass(value)).toBe('AssertionError')
    expect(lifecycleErrorClass({ name: 'unlisted synthetic class' })).toBe('unknown')
    expect(lifecycleErrorClass('synthetic primitive')).toBe('unknown')
    expect(calls).toBe(0)
  })
  it.each([Error, TypeError, RangeError, SyntaxError, ReferenceError, AggregateError])('classifies a known native prototype', (constructor) => {
    expect(lifecycleErrorClass(Object.create(constructor.prototype))).toBe(constructor.name)
  })
  it('retains the existing reporter entry point', () => {
    const reporter = new CliContractReporter()
    expect(typeof reporter.onTestCaseResult).toBe('function')
    expect(typeof reporter.onTestRunEnd).toBe('function')
  })
})

describe('native reference execution coverage invariant', () => {
  const entry = { id: 'claude-checkpoint-resume', file: 'tests/cli/claudeRuntime.test.ts', name: 'native Claude checkpoint' }
  const passed = { file: '/repo/' + entry.file, name: entry.name, state: 'passed', receipt: entry.id }
  it('requires the actual declared test and its post-assertion receipt', () => {
    expect(missingRuntimeReferences([entry], [passed])).toEqual([])
    expect(missingRuntimeReferences([entry], [])).toEqual([entry.id])
    expect(missingRuntimeReferences([entry], [{ ...passed, receipt: undefined }])).toEqual([entry.id])
    expect(missingRuntimeReferences([entry], [{ ...passed, file: '/repo/tests/cli/shellRuntime.test.ts' }])).toEqual([entry.id])
    expect(missingRuntimeReferences([entry], [passed, passed])).toEqual([entry.id])
  })
  it.each(['skipped', 'pending', 'failed'])('rejects a %s native reference case', (state) => {
    expect(missingRuntimeReferences([entry], [{ ...passed, state }])).toEqual([entry.id])
  })
})

describe('frontend CLI execution coverage invariant', () => {
  const passed = { name: 'frontend contract claude', state: 'passed', receipt: 'claude' }
  it('accepts a successfully executed contract and rejects a newly registered uncovered vendor', () => {
    expect(missingContracts(['claude'], [passed])).toEqual([])
    expect(missingContracts(['claude', 'new-vendor'], [passed])).toEqual(['new-vendor'])
  })
  it('rejects an uncollected suite or removed contract', () => {
    expect(missingContracts(['claude'], [])).toEqual(['claude'])
  })
  it.each(['skipped', 'pending', 'failed'])('rejects a %s contract', (state) => {
    expect(missingContracts(['claude'], [{ ...passed, state }])).toEqual(['claude'])
  })
  it('rejects an empty passing test without the post-assertion execution receipt', () => {
    expect(missingContracts(['claude'], [{ ...passed, receipt: undefined }])).toEqual(['claude'])
  })
  it('rejects duplicate identities instead of hiding one failed case behind a pass', () => {
    expect(missingContracts(['claude'], [passed, { ...passed, state: 'failed' }])).toEqual(['claude'])
  })
})

describe('test-only OpenCode resume declaration variant', () => {
  it.each([false, true])('uses the unchanged builder and restores the original reference (throw=%s)', (fail) => {
    const index = AGENT_SPECS.findIndex((spec) => spec.agentKey === 'opencode')
    expect(index).toBeGreaterThanOrEqual(0)
    const original = AGENT_SPECS[index]
    const failure = new Error('OWN synthetic declaration restoration control')
    const id = 'ses_owned_control'
    expect(buildResumeCommand('opencode', id)).toBe(`opencode --session ${id}`)
    try {
      AGENT_SPECS[index] = { ...original, resumeArgs: (session) => `-s ${session}` }
      expect(buildResumeCommand('opencode', id)).toBe(`opencode -s ${id}`)
      if (fail) throw failure
    } catch (error) {
      expect(fail).toBe(true)
      expect(error).toBe(failure)
    } finally { AGENT_SPECS[index] = original }
    expect(AGENT_SPECS[index]).toBe(original)
    expect(buildResumeCommand('opencode', id)).toBe(`opencode --session ${id}`)
  })
})

describe('test-only lifecycle writer binding', () => {
  const RUN_ID = 'a1b2c3d4e5f60718293a4b5c6d7e8f90'
  const nativeCase = { name: 'native Codex production-client delayed identification and resume',
    module: { moduleId: '/repo/tests/cli/nativeDifferences.test.ts' }, task: { result: { state: 'pass', errors: [] }, meta: {} } }
  const coverageRequired = process.env.NAVIDE_CLI_COVERAGE_REQUIRED
  let root = ''
  let dir = ''
  beforeEach(() => {
    root = mkdtempSync(join(tmpdir(), 'navide-writer-binding-'))
    dir = join(root, 'binding')
    // The legacy path is cwd-relative; never let a test touch the repository's real lifecycle output.
    vi.spyOn(process, 'cwd').mockReturnValue(root)
    delete process.env.NAVIDE_T04_WRITER_BINDING_DIR
    delete process.env.NAVIDE_T04_WRITER_RUN_ID
    delete process.env.NAVIDE_CLI_COVERAGE_REQUIRED
  })
  afterEach(() => {
    vi.restoreAllMocks()
    delete process.env.NAVIDE_T04_WRITER_BINDING_DIR
    delete process.env.NAVIDE_T04_WRITER_RUN_ID
    if (coverageRequired === undefined) delete process.env.NAVIDE_CLI_COVERAGE_REQUIRED
    else process.env.NAVIDE_CLI_COVERAGE_REQUIRED = coverageRequired
    rmSync(root, { recursive: true, force: true })
  })
  const bind = () => {
    mkdirSync(dir)
    process.env.NAVIDE_T04_WRITER_BINDING_DIR = dir
    process.env.NAVIDE_T04_WRITER_RUN_ID = RUN_ID
  }
  const headerLiteral = () => JSON.stringify({ scope: 't04-writer-binding', header_version: 1, run_id: RUN_ID,
    writer_pid: process.pid, output: join(dir, 'lifecycle.jsonl') }) + '\n'
  const release = (content: string) => writeFileSync(join(dir, 'release'), content)

  const locationSnapshot = (metadata: unknown) => {
    new CliContractReporter().onTestCaseResult({ ...nativeCase,
      task: { result: { state: 'fail', errors: [] }, meta: { nativeDifferenceLifecycle: metadata } } } as never)
    const lines = readFileSync(join(root, 'test-results/ci/native-codex-lifecycle.jsonl'), 'utf8').trimEnd().split('\n')
    return JSON.parse(lines[lines.length - 1]).principal_location
  }
  const unknownLocation = { stage: 'unknown', gate: 'unknown', source_function: 'unknown' }
  const fixedLocation = { stage: 1, gate: 'delayed-identity', source_function: 'nativeCodexCase' }

  it('recognizes only the exact OpenCode case and finite source location', () => {
    const metadata = { callback: 'returned', finalization: 'returned', case_tail: 'returned',
      location: { stage: 2, gate: 'resume-command', source_function: 'nativeOpenCodeCase' } }
    const reporter = new CliContractReporter()
    reporter.onTestCaseResult({ ...nativeCase, name: 'native OpenCode production-client WAL SQLite and resume syntax variant',
      task: { result: { state: 'pass', errors: [] }, meta: { nativeDifferenceLifecycle: metadata } } } as never)
    const output = join(root, 'test-results/ci/native-codex-lifecycle.jsonl')
    expect(existsSync(output)).toBe(true)
    const row = JSON.parse(readFileSync(output, 'utf8').trimEnd())
    expect(row.principal_location).toEqual(metadata.location)
    expect(row.callback_state).toBe('returned')
    expect(row.case_error_count).toBe(0)
  })

  it.each([
    { stage: 0, gate: 'bootstrap-self', source_function: 'withBackend' },
    { stage: 0, gate: 'case-setup', source_function: 'case/setup' },
    { stage: 1, gate: 'delayed-identity', source_function: 'nativeCodexCase' },
    { stage: 2, gate: 'durable-totals', source_function: 'nativeCodexCase' },
  ])('snapshots only the fixed scalar location (%j)', (location) => {
    expect(locationSnapshot({ location })).toEqual(location)
  })
  it.each([undefined, null, 'synthetic', [], { stage: 3, gate: 'synthetic', source_function: 'synthetic' }])(
    'uses explicit unknown for missing or malformed location (%j)', (location) => {
      expect(locationSnapshot({ location })).toEqual(unknownLocation)
    })
  it('rejects inherited fields and never invokes location or field accessors', () => {
    let calls = 0
    const accessor = { get() { calls++; throw new Error('synthetic') } }
    expect(locationSnapshot(Object.defineProperty({}, 'location', accessor))).toEqual(unknownLocation)
    expect(locationSnapshot({ location: Object.create(fixedLocation) })).toEqual(unknownLocation)
    for (const key of ['stage', 'gate', 'source_function'] as const) {
      const location = Object.defineProperty({ ...fixedLocation }, key, accessor)
      expect(locationSnapshot({ location })).toEqual({ ...fixedLocation, [key]: 'unknown' })
    }
    expect(calls).toBe(0)
  })
  it.each([false, true])('rejects live/revoked location proxies before reflection (%s)', (revoke) => {
    let calls = 0
    const pair = Proxy.revocable({ ...fixedLocation }, { get() { calls++; throw 1 },
      getOwnPropertyDescriptor() { calls++; throw 1 }, getPrototypeOf() { calls++; throw 1 } })
    if (revoke) pair.revoke()
    expect(locationSnapshot({ location: pair.proxy })).toEqual(unknownLocation)
    expect(locationSnapshot(pair.proxy)).toEqual(unknownLocation)
    expect(calls).toBe(0)
  })
  it('never coerces malformed scalar objects or reads their payload fields', () => {
    let calls = 0
    const scalar = { toString() { calls++; throw 1 }, valueOf() { calls++; throw 1 } }
    const location = { stage: scalar, gate: scalar, source_function: scalar,
      get message() { calls++; throw 1 }, get stack() { calls++; throw 1 }, get payload() { calls++; throw 1 } }
    expect(locationSnapshot({ location })).toEqual(unknownLocation)
    expect(calls).toBe(0)
  })

  it('keeps the legacy cwd-relative body when no binding is configured', () => {
    new CliContractReporter().onTestCaseResult(nativeCase as never)
    const lines = readFileSync(join(root, 'test-results/ci/native-codex-lifecycle.jsonl'), 'utf8').trimEnd().split('\n')
    expect(lines).toHaveLength(1)
    expect(JSON.parse(lines[0]).event).toBe('case-result-snapshot')
    expect(existsSync(join(dir, 'writer-header.json'))).toBe(false)
  })
  it('writes the fixed header, then the body only after the exact digest release', () => {
    bind()
    release('RELEASE ' + createHash('sha256').update(headerLiteral()).digest('hex'))
    new CliContractReporter().onTestCaseResult(nativeCase as never)
    expect(readFileSync(join(dir, 'writer-header.json'), 'utf8')).toBe(headerLiteral())
    const lines = readFileSync(join(dir, 'lifecycle.jsonl'), 'utf8').trimEnd().split('\n')
    expect(lines).toHaveLength(1)
    expect(JSON.parse(lines[0]).scope).toBe('t04-native-codex')
    expect(existsSync(join(root, 'test-results/ci/native-codex-lifecycle.jsonl'))).toBe(false)
  })
  it('fails the binding, not the body, when no release arrives within two seconds', () => {
    bind()
    const started = performance.now()
    expect(() => new CliContractReporter().onTestCaseResult(nativeCase as never)).toThrow('writer binding failed: release-timeout')
    const elapsed = performance.now() - started
    expect(elapsed).toBeGreaterThanOrEqual(1900)
    expect(elapsed).toBeLessThan(4000)
    expect(existsSync(join(dir, 'writer-header.json'))).toBe(true)
    expect(existsSync(join(dir, 'lifecycle.jsonl'))).toBe(false)
    expect(JSON.parse(readFileSync(join(dir, 'binding-failure.json'), 'utf8')).code).toBe('release-timeout')
    expect(existsSync(join(root, 'test-results/ci/native-codex-lifecycle.jsonl'))).toBe(false)
  })
  it.each(['RELEASE ' + '0'.repeat(64), 'RELEASE', ''])('rejects a malformed or mismatched release immediately', (content) => {
    bind()
    release(content)
    const started = performance.now()
    expect(() => new CliContractReporter().onTestCaseResult(nativeCase as never)).toThrow('writer binding failed: release-mismatch')
    expect(performance.now() - started).toBeLessThan(1000)
    expect(existsSync(join(dir, 'lifecycle.jsonl'))).toBe(false)
  })
  it.each([
    ['directory only', { NAVIDE_T04_WRITER_BINDING_DIR: '/absolute/dir' }],
    ['run id only', { NAVIDE_T04_WRITER_RUN_ID: RUN_ID }],
    ['relative directory', { NAVIDE_T04_WRITER_BINDING_DIR: 'relative/dir', NAVIDE_T04_WRITER_RUN_ID: RUN_ID }],
    ['malformed run id', { NAVIDE_T04_WRITER_BINDING_DIR: '/absolute/dir', NAVIDE_T04_WRITER_RUN_ID: 'short' }],
  ])('rejects a partially configured binding (%s) without falling back to the legacy body', (_label, env) => {
    Object.assign(process.env, env)
    expect(() => new CliContractReporter().onTestCaseResult(nativeCase as never)).toThrow('writer binding failed: config')
    expect(existsSync(join(root, 'test-results/ci/native-codex-lifecycle.jsonl'))).toBe(false)
  })
  it('refuses a pre-existing header and a pre-existing body', () => {
    bind()
    writeFileSync(join(dir, 'writer-header.json'), 'stale')
    expect(() => new CliContractReporter().onTestCaseResult(nativeCase as never)).toThrow('writer binding failed: header-write')
    rmSync(join(dir, 'writer-header.json'))
    writeFileSync(join(dir, 'lifecycle.jsonl'), 'stale\n')
    release('RELEASE ' + createHash('sha256').update(headerLiteral()).digest('hex'))
    expect(() => new CliContractReporter().onTestCaseResult(nativeCase as never)).toThrow('writer binding failed: output-write')
    expect(readFileSync(join(dir, 'lifecycle.jsonl'), 'utf8')).toBe('stale\n')
  })
  it('never delays or binds an ordinary case', () => {
    bind()
    const started = performance.now()
    new CliContractReporter().onTestCaseResult({ name: 'ordinary', module: { moduleId: '/repo/tests/cli/other.test.ts' }, task: {} } as never)
    expect(performance.now() - started).toBeLessThan(500)
    expect(existsSync(join(dir, 'writer-header.json'))).toBe(false)
  })
  it('keeps the case errors and surfaces the binding failure after the coverage gate at run end', () => {
    bind()
    const reporter = new CliContractReporter()
    const original = new TypeError('primary case error')
    const failing = { ...nativeCase, task: { result: { state: 'fail', errors: [original] }, meta: {} } }
    release('RELEASE ' + '1'.repeat(64))
    expect(() => reporter.onTestCaseResult(failing as never)).toThrow('writer binding failed: release-mismatch')
    expect(failing.task.result.errors[0]).toBe(original)
    expect(() => reporter.onTestRunEnd([], [], 'failed')).toThrow('writer binding failed: release-mismatch')
    expect(existsSync(join(dir, 'lifecycle.jsonl'))).toBe(false)
  })
})
