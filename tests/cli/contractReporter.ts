import { createHash } from 'node:crypto'
import { appendFileSync, readFileSync, mkdirSync, statSync, writeFileSync } from 'node:fs'
import { isAbsolute, join, resolve } from 'node:path'
import { types } from 'node:util'
import type { Reporter, TestModule } from 'vitest/node'
import { MANAGED_LINEAGE_GATES } from './opencodeManagedLineage'

const ERROR_CLASSES = ['Error', 'TypeError', 'RangeError', 'SyntaxError', 'ReferenceError', 'AggregateError', 'AssertionError', 'unknown'] as const
type ErrorClass = typeof ERROR_CLASSES[number]

function ownData(value: unknown, key: string): unknown {
  if ((typeof value !== 'object' && typeof value !== 'function') || value === null || types.isProxy(value)) return undefined
  const descriptor = Object.getOwnPropertyDescriptor(value, key)
  return descriptor && Object.hasOwn(descriptor, 'value') ? descriptor.value : undefined
}

/** No unknown getter, proxy trap, constructor, message or stack is evaluated. */
export function lifecycleErrorClass(error: unknown): ErrorClass {
  if ((typeof error !== 'object' && typeof error !== 'function') || error === null || types.isProxy(error)) return 'unknown'
  const descriptor = Object.getOwnPropertyDescriptor(error, 'name')
  if (descriptor) {
    if (!Object.hasOwn(descriptor, 'value')) return 'unknown'
    return ERROR_CLASSES.find((name) => name === descriptor.value) ?? 'unknown'
  }
  const prototype = Object.getPrototypeOf(error)
  const native: ReadonlyArray<readonly [object, ErrorClass]> = [[Error.prototype, 'Error'], [TypeError.prototype, 'TypeError'],
    [RangeError.prototype, 'RangeError'], [SyntaxError.prototype, 'SyntaxError'], [ReferenceError.prototype, 'ReferenceError'],
    [AggregateError.prototype, 'AggregateError']]
  for (const [known, name] of native) if (prototype === known) return name
  return 'unknown'
}

export interface NativeDifferenceLifecycle {
  callback: 'not-started' | 'running' | 'returned' | 'failed'
  finalization: 'not-started' | 'running' | 'returned' | 'failed'
  case_tail: 'not-started' | 'running' | 'returned' | 'failed'
  callback_error_class: ErrorClass
  finalization_error_class: ErrorClass
  case_tail_error_class: ErrorClass
  location?: { stage: number; gate: string; source_function: string }
}

const PRINCIPAL_GATES = ['bootstrap-self', 'case-setup', 'endpoint', 'fixture-identities', 'client-connect',
  'manual-pane', 'resume-command', 'restart-project', 'session-id', 'session-exists',
  'checkpoint', 'restart-totals', 'terminal-create', 'child-ready', 'child-provenance',
  'producer-os-await', 'producer-os-identity', 'runtime-receipts', 'native-owner-root',
  'backend-os-present', 'runtime-os-present', 'backend-parent', 'backend-executable',
  'backend-image', 'backend-argv', 'runtime-parent', 'runtime-executable',
  'runtime-image', 'runtime-argv', 'producer-parent', 'pty-root-pid', 'birth-order',
  'pty-group-start', 'producer-image', 'runtime-generation', 'shell-resolution',
  'lineage-export', 'vendor-command', 'no-early-session', 'stage-input',
  'rollout-boundary', 'delayed-identity', 'completion-boundary', 'completion-turn-text', 'completion-child-event', 'completion-child-order',
  'completion-top-level-attribution', 'completion-pty-vocabulary', 'activity-vocabulary',
  'complete-terminal', 'full-terminal-equality', 'shutdown-flush', 'durable-totals',
  'native-lifecycle', 'principal-export', 'driver-exit', ...MANAGED_LINEAGE_GATES] as const
const PRINCIPAL_FUNCTIONS = ['withBackend', 'nativeCodexCase', 'nativeOpenCodeCase', 'case/setup'] as const

function principalLocation(metadata: unknown): { stage: 0 | 1 | 2 | 'unknown'; gate: string; source_function: string } {
  const location = ownData(metadata, 'location')
  if (typeof location !== 'object' || location === null || types.isProxy(location) || Array.isArray(location)) {
    return { stage: 'unknown', gate: 'unknown', source_function: 'unknown' }
  }
  const stage = ownData(location, 'stage')
  const gate = ownData(location, 'gate')
  const sourceFunction = ownData(location, 'source_function')
  return { stage: stage === 0 || stage === 1 || stage === 2 ? stage : 'unknown',
    gate: PRINCIPAL_GATES.find((value) => value === gate) ?? 'unknown',
    source_function: PRINCIPAL_FUNCTIONS.find((value) => value === sourceFunction) ?? 'unknown' }
}

function nativeModule(module: unknown): boolean {
  const file = ownData(module, 'moduleId')
  return typeof file === 'string' && file.replaceAll('\\', '/').endsWith('/tests/cli/nativeDifferences.test.ts')
}

function nativeCase(test: unknown): boolean {
  const name = ownData(test, 'name')
  return (name === 'native Codex production-client delayed identification and resume'
    || name === 'native OpenCode production-client WAL SQLite and resume syntax variant')
    && nativeModule(ownData(test, 'module'))
}

function errorCounts(errors: unknown): { count: number; unavailable: number; classes: Record<ErrorClass, number> } {
  const classes = Object.fromEntries(ERROR_CLASSES.map((name) => [name, 0])) as Record<ErrorClass, number>
  const length = ownData(errors, 'length')
  if (types.isProxy(errors) || !Array.isArray(errors) || typeof length !== 'number') {
    return { count: 0, unavailable: errors === undefined ? 0 : 1, classes }
  }
  for (let index = 0; index < length; index++) classes[lifecycleErrorClass(ownData(errors, String(index)))]++
  return { count: length, unavailable: 0, classes }
}

export interface ContractResult {
  name: string
  state: string
  receipt: unknown
}

/** Gate on successful execution, not filenames or a declaration in a catalog.
 * The receipt is emitted only after the real contract assertions finish. */
export function missingContracts(vendors: string[], results: ContractResult[]): string[] {
  return vendors.filter((vendor) => {
    const matches = results.filter((result) => result.name === `frontend contract ${vendor}`)
    return matches.length !== 1 || matches[0].state !== 'passed' || matches[0].receipt !== vendor
  })
}

export interface RuntimeReference { id: string; file: string; name: string }
export interface RuntimeResult extends ContractResult { file: string }

export function missingRuntimeReferences(cases: RuntimeReference[], results: RuntimeResult[]): string[] {
  return cases.filter((entry) => {
    const matches = results.filter((result) => result.file.replaceAll('\\', '/').endsWith('/' + entry.file) && result.name === entry.name)
    return matches.length !== 1 || matches[0].state !== 'passed' || matches[0].receipt !== entry.id
  }).map((entry) => entry.id)
}

/** Test-only writer binding. Both variables or neither; a partial setting never falls back to the legacy path.
 * The controller observes this process through the OS before it writes the exact release; no body byte precedes it. */
const BINDING_DIR_ENV = 'NAVIDE_T04_WRITER_BINDING_DIR'
const BINDING_RUN_ENV = 'NAVIDE_T04_WRITER_RUN_ID'
const BINDING_WAIT_MS = 2000
const BINDING_POLL_MS = 20

const BINDING_CODES = ['config', 'header-write', 'release-timeout', 'release-mismatch', 'release-read', 'output-write'] as const
type BindingCode = typeof BINDING_CODES[number]

export class WriterBindingError extends Error {
  readonly code: BindingCode
  constructor(code: BindingCode) {
    super('writer binding failed: ' + code)
    this.name = 'WriterBindingError'
    this.code = code
  }
}

interface WriterBinding { dir: string; runId: string; header: string; digest: string; output: string }

function bindingFromEnvironment(): WriterBinding | null {
  const dir = process.env[BINDING_DIR_ENV]
  const runId = process.env[BINDING_RUN_ENV]
  if (dir === undefined && runId === undefined) return null
  if (dir === undefined || runId === undefined || !isAbsolute(dir) || resolve(dir) !== dir || !/^[0-9a-f]{32}$/.test(runId)) {
    throw new WriterBindingError('config')
  }
  const output = join(dir, 'lifecycle.jsonl')
  const header = JSON.stringify({ scope: 't04-writer-binding', header_version: 1, run_id: runId, writer_pid: process.pid, output }) + '\n'
  return { dir, runId, header, digest: createHash('sha256').update(header).digest('hex'), output }
}

/** Fixed supplementary metadata only; it never replaces the thrown diagnostic. */
function recordBindingFailure(binding: WriterBinding | null, code: BindingCode): void {
  if (!binding) return
  try {
    writeFileSync(join(binding.dir, 'binding-failure.json'), JSON.stringify({ scope: 't04-writer-binding', header_version: 1,
      run_id: binding.runId, writer_pid: process.pid, code }) + '\n', { flag: 'wx' })
  } catch { /* the original binding diagnostic is thrown regardless */ }
}

export default class CliContractReporter implements Reporter {
  private nativeSeen = false
  private hookStarted = 0
  private hookReturned = 0
  private binding: 'unresolved' | 'legacy' | 'bound' | WriterBindingError = 'unresolved'
  private writerBinding: WriterBinding | null = null
  private bodyStarted = false

  private bind(): void {
    if (this.binding !== 'unresolved') return
    let binding: WriterBinding | null = null
    let code: BindingCode | undefined
    try {
      binding = bindingFromEnvironment()
      if (!binding) { this.binding = 'legacy'; return }
      try {
        writeFileSync(join(binding.dir, 'writer-header.json'), binding.header, { flag: 'wx', mode: 0o600 })
      } catch { code = 'header-write'; throw new WriterBindingError(code) }
      const wait = new Int32Array(new SharedArrayBuffer(4))
      const started = performance.now()
      const expected = 'RELEASE ' + binding.digest
      for (;;) {
        let present = true
        let content = ''
        try {
          if (statSync(join(binding.dir, 'release')).size > expected.length) { code = 'release-mismatch'; throw new WriterBindingError(code) }
          content = readFileSync(join(binding.dir, 'release'), 'utf8')
        } catch (error) {
          if (error instanceof WriterBindingError) throw error
          if ((error as NodeJS.ErrnoException).code === 'ENOENT') present = false
          else { code = 'release-read'; throw new WriterBindingError(code) }
        }
        if (present) {
          if (content === expected) break
          code = 'release-mismatch'
          throw new WriterBindingError(code)
        }
        if (performance.now() - started >= BINDING_WAIT_MS) { code = 'release-timeout'; throw new WriterBindingError(code) }
        Atomics.wait(wait, 0, 0, BINDING_POLL_MS)
      }
      this.writerBinding = binding
      this.binding = 'bound'
    } catch (error) {
      const failure = error instanceof WriterBindingError ? error : new WriterBindingError('config')
      recordBindingFailure(binding, failure.code)
      this.binding = failure
    }
  }

  private snapshot(event: 'case-result-snapshot' | 'run-end-snapshot', fields: Record<string, unknown>): void {
    this.bind()
    if (this.binding instanceof WriterBindingError) throw this.binding
    const line = JSON.stringify({
      scope: 't04-native-codex', event, hook_started: this.hookStarted, hook_returned: this.hookReturned,
      hook_incomplete: Math.max(0, this.hookStarted - this.hookReturned), ...fields,
    }) + '\n'
    if (this.binding === 'bound' && this.writerBinding) {
      try {
        // The first body write is exclusive: a stale or foreign output is refused, never appended to.
        writeFileSync(this.writerBinding.output, line, { flag: this.bodyStarted ? 'a' : 'wx', mode: 0o600 })
        this.bodyStarted = true
      } catch {
        this.binding = new WriterBindingError('output-write')
        recordBindingFailure(this.writerBinding, 'output-write')
        throw this.binding
      }
      return
    }
    mkdirSync(resolve(process.cwd(), 'test-results/ci'), { recursive: true })
    appendFileSync(resolve(process.cwd(), 'test-results/ci/native-codex-lifecycle.jsonl'), line)
  }

  onHookStart(context: Parameters<NonNullable<Reporter['onHookStart']>>[0]): void {
    const entity = ownData(context, 'entity')
    const name = ownData(context, 'name')
    if (['beforeAll', 'afterAll', 'beforeEach', 'afterEach'].includes(name as string)
      && (nativeCase(entity) || nativeModule(entity))) this.hookStarted++
  }

  onHookEnd(context: Parameters<NonNullable<Reporter['onHookEnd']>>[0]): void {
    const entity = ownData(context, 'entity')
    const name = ownData(context, 'name')
    if (['beforeAll', 'afterAll', 'beforeEach', 'afterEach'].includes(name as string)
      && (nativeCase(entity) || nativeModule(entity))) this.hookReturned++
  }

  onTestCaseResult(test: Parameters<NonNullable<Reporter['onTestCaseResult']>>[0]): void {
    if (!nativeCase(test)) return
    this.nativeSeen = true
    // Pinned 3.2.7 stores task as an own data field (cli-api:7915–7919).
    const task = ownData(test, 'task')
    const result = ownData(task, 'result')
    const state = ownData(result, 'state')
    // These two cases are file-root tasks: pinned Vitest fullName equals task.name.
    // Do not evaluate an unknown getter or bind a nested/different task.
    const taskName = ownData(task, 'name')
    const fullName = ownData(task, 'suite') === undefined && taskName === ownData(test, 'name') ? taskName : 'unknown'
    const metadata = ownData(ownData(task, 'meta'), 'nativeDifferenceLifecycle')
    const phase = (key: string) => {
      const value = ownData(metadata, key)
      return ['not-started', 'running', 'returned', 'failed'].find((name) => name === value) ?? 'unknown'
    }
    const classification = (key: string) => ERROR_CLASSES.find((name) => name === ownData(metadata, key)) ?? 'unknown'
    const errors = errorCounts(ownData(result, 'errors'))
    this.snapshot('case-result-snapshot', {
      case_full_name: fullName,
      // Pinned @vitest/runner 3.2.7 runTest (dist/chunk-hooks.js:1531-1535) returns for a `-t`-filtered case (mode 'skip', set by
      // interpretTaskModes :1009-1035) without assigning a result, so its own `result` stays undefined: only that exact shape is 'skipped'.
      case_state: state === 'pass' ? 'passed' : state === 'fail' ? 'failed' : state === 'skip' ? 'skipped'
        : result === undefined && ownData(task, 'mode') === 'skip' ? 'skipped' : 'unknown',
      principal_location: principalLocation(metadata),
      callback_state: phase('callback'), callback_returned: phase('callback') === 'returned' ? 1 : 0,
      finalization_state: phase('finalization'), case_tail_state: phase('case_tail'),
      callback_error_class: classification('callback_error_class'),
      finalization_error_class: classification('finalization_error_class'),
      case_tail_error_class: classification('case_tail_error_class'),
      case_error_count: errors.count, case_errors_unavailable: errors.unavailable, case_error_classes: errors.classes,
    })
  }

  onTestRunEnd(modules: ReadonlyArray<TestModule>, unhandledErrors: ReadonlyArray<unknown> = [], reason: 'passed' | 'failed' | 'interrupted' = 'passed'): void {
    let diagnosticFailed = false
    let diagnosticError: unknown
    if (this.nativeSeen) {
      try {
        const errors = errorCounts(unhandledErrors)
        this.snapshot('run-end-snapshot', { run_reason: ['passed', 'failed', 'interrupted'].find((value) => value === reason) ?? 'unknown',
          unhandled_count: errors.count, unhandled_unavailable: errors.unavailable, unhandled_classes: errors.classes })
      } catch (error) { diagnosticFailed = true; diagnosticError = error }
    }
    let coverageFailed = false
    try {
    if (process.env.NAVIDE_CLI_COVERAGE_REQUIRED !== '1') return
    const catalog = JSON.parse(readFileSync(resolve('tests/fixtures/cli-regression/catalog.json'), 'utf8'))
    const vendors = Object.keys(catalog.vendors)
    if (!vendors.length) throw new Error('CLI coverage catalog has no vendors')
    const results = modules
      .filter((module) => module.moduleId.replaceAll('\\', '/').endsWith('/tests/cli/vendorContracts.test.ts'))
      .flatMap((module) => [...module.children.allTests()].map((test) => ({
        name: test.name, state: test.result().state, receipt: test.meta().cliContract,
      })))
    const missing = missingContracts(vendors, results)
    const references = Object.entries(catalog.runtimeReferences ?? {}).map(([vendor, declaration]: [string, any]) => ({
      vendor, ...declaration, applicable: declaration.platforms.includes(process.platform),
    }))
    const runtimeResults = modules.flatMap((module) => [...module.children.allTests()].map((test) => ({
      file: module.moduleId, name: test.name, state: test.result().state, receipt: test.meta().cliRuntimeReference,
    }))).filter((result) => references.some((reference) => reference.cases.some((entry: RuntimeReference) =>
      result.file.replaceAll('\\', '/').endsWith('/' + entry.file) && result.name === entry.name)))
    const missingRuntime = missingRuntimeReferences(references.filter((reference) => reference.applicable).flatMap((reference) => reference.cases), runtimeResults)
    mkdirSync(resolve('test-results/ci'), { recursive: true })
    writeFileSync(resolve('test-results/ci/cli-contracts.json'), JSON.stringify({ vendors, results, missing,
      runtimeReferences: { platform: process.platform, declarations: references, results: runtimeResults, missing: missingRuntime },
    }, null, 2) + '\n')
    if (missing.length || missingRuntime.length) throw new Error(`CLI contracts did not execute successfully: ${[...missing, ...missingRuntime].join(', ')}`)
    } catch (error) { coverageFailed = true; throw error }
    finally {
      // Other reporters run concurrently. This snapshot is never an acceptance verdict.
      if (diagnosticFailed && !coverageFailed) throw diagnosticError
    }
  }
}
