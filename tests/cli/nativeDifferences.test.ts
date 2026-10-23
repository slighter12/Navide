import { spawn, spawnSync } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import { appendFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { buildAgentPaneCommand } from '../../src/main/plugins/agentPaneCommand'
import { buildResumeCommand } from '../../src/renderer/src/platform/plugin-shell/lib/resume-command'
import { AGENT_SPECS } from '../../src/renderer/src/platform/plugin-shell/agents'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { redactDiagnostic } from './baselineArtifacts'
import { completeRuntimeReceipts, differenceReady } from './nativeDifferenceRecords'
import { lifecycleErrorClass, type NativeDifferenceLifecycle } from './contractReporter'
import { matchesUsageObservation } from './opencodeUsageObservation'
import { verifyManagedOpenCodeLineage, type ManagedLineageGate } from './opencodeManagedLineage'

// Type-only: the reporter reads this task meta entry by name (contractReporter.ts); vitest's TaskMeta does not declare it.
declare module 'vitest' {
  interface TaskMeta { nativeDifferenceLifecycle?: NativeDifferenceLifecycle }
}

type PrincipalGate = 'bootstrap-self' | 'case-setup' | 'endpoint' | 'fixture-identities' | 'client-connect'
  | 'manual-pane' | 'resume-command' | 'restart-project' | 'session-id' | 'session-exists'
  | 'checkpoint' | 'restart-totals' | 'terminal-create' | 'child-ready' | 'child-provenance'
  | 'producer-os-await' | 'producer-os-identity' | 'runtime-receipts' | 'native-owner-root'
  | 'backend-os-present' | 'runtime-os-present' | 'backend-parent' | 'backend-executable'
  | 'backend-image' | 'backend-argv' | 'runtime-parent' | 'runtime-executable'
  | 'runtime-image' | 'runtime-argv' | 'producer-parent' | 'pty-root-pid' | 'birth-order'
  | 'pty-group-start' | 'producer-image' | 'runtime-generation' | 'shell-resolution'
  | 'lineage-export' | 'vendor-command' | 'no-early-session' | 'stage-input'
  | 'rollout-boundary' | 'delayed-identity' | 'completion-boundary' | 'activity-vocabulary'
  | 'complete-terminal' | 'full-terminal-equality' | 'shutdown-flush' | 'durable-totals'
  | 'native-lifecycle' | 'principal-export' | 'driver-exit' | 'native-usage-prefix'
  | 'pending-usage-observation' | 'paused-usage-observation' | 'completion-turn-text' | 'completion-child-event' | 'completion-child-order'
  | 'completion-top-level-attribution' | 'completion-pty-vocabulary' | ManagedLineageGate
type PrincipalLocation = { stage: number; gate: PrincipalGate; source_function: 'withBackend' | 'nativeCodexCase' | 'nativeOpenCodeCase' | 'case/setup' }

function structuralErrorClass(error: unknown): string {
  return lifecycleErrorClass(error)
}

async function until(predicate: () => boolean | Promise<boolean>, message: string, timeout = 15_000): Promise<void> {
  const deadline = Date.now() + timeout
  while (!(await predicate())) {
    if (Date.now() >= deadline) throw new Error(message)
    await delay(10)
  }
}

async function withBackend(vendor: string, prepare: (root: string) => void, run: (root: string, driver: ReturnType<typeof spawn>, endpoint: (stage: number) => Promise<any>, reportDiagnostic: (error: unknown) => void, markGate: (stage: number, gate: PrincipalGate) => void) => Promise<void>, lifecycle?: NativeDifferenceLifecycle): Promise<void> {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-native-difference-')))
  writeFileSync(join(root, 'fixture-marker'), `SYNTHETIC_T04_${randomUUID()}`)
  prepare(root)
  const driver = spawn(resolve('backend/.venv/bin/python'), ['-I', '-S', resolve('backend/tests/cli_regression/support/difference_entry.py'), 'driver', root, vendor], {
    cwd: resolve('backend'), stdio: ['pipe', 'pipe', 'pipe'],
    env: { HOME: join(root, 'home'), USERPROFILE: join(root, 'home'), TMPDIR: root,
      PATH: '/usr/bin:/bin', LANG: 'C.UTF-8' },
  })
  let log = ''
  let code: number | null | undefined
  driver.stdout!.on('data', (data) => { log += data })
  driver.stderr!.on('data', (data) => { log += data })
  driver.on('error', (error) => { log += structuralErrorClass(error); code = -1 })
  driver.on('exit', (value) => { code = value })
  async function endpoint(stage: number): Promise<any> {
    await until(() => {
      if (code !== undefined) throw new Error(redactDiagnostic(log))
      return existsSync(join(root, `endpoint-${stage}.json`))
    }, 'difference backend readiness', 35_000)
    return JSON.parse(readFileSync(join(root, `endpoint-${stage}.json`), 'utf8'))
  }
  const errors: unknown[] = []
  const errorOrder: string[] = []
  let location: PrincipalLocation = { stage: 0, gate: 'bootstrap-self', source_function: 'withBackend' }
  let principalFailed = false
  function mirrorLocation(): void {
    if (lifecycle) lifecycle.location = { stage: location.stage, gate: location.gate, source_function: location.source_function }
  }
  mirrorLocation()
  function markGate(stage: number, gate: PrincipalGate): void {
    location = { stage, gate, source_function: gate === 'case-setup' ? 'case/setup' : vendor === 'opencode' ? 'nativeOpenCodeCase' : 'nativeCodexCase' }
    if (!principalFailed && !errors.length) mirrorLocation()
  }
  function recordStructure(error: unknown, label: 'principal' | 'driver-exit'): void {
    const point: PrincipalLocation = label === 'principal' ? location
      : { stage: 0, gate: 'driver-exit', source_function: 'withBackend' }
    try {
      appendFileSync(join(root, 'principal-structure.jsonl'), JSON.stringify({
        ...point, label, error_class: structuralErrorClass(error),
      }) + '\n')
    } catch (secondary) { failure(secondary, 'structure-append') }
  }
  function failure(error: unknown, label: 'periodic-capture' | 'final-capture' | 'driver-cleanup-wait' | 'fallback-reap' | 'receipt-export' | 'driver-exit' | 'stage2-append' | 'structure-append' | 'driver-stdin'): void {
    if (!errors.length) mirrorLocation()
    errors.push(error); errorOrder.push(label)
    if (label === 'driver-exit') recordStructure(error, label)
  }
  driver.stdin!.on('error', (error) => failure(error, 'driver-stdin'))
  let stdinClosed = false
  driver.stdin!.once('close', () => { stdinClosed = true })
  function control(mode: 'capture' | 'cleanup'): void {
    if (!driver.pid) return
    const result = spawnSync(resolve('backend/.venv/bin/python'), ['-I', '-S',
      resolve('backend/tests/cli_regression/support/difference_cleanup.py'), mode, root,
      String(driver.pid), String(process.pid)], { env: { HOME: join(root, 'home'),
        PATH: '/usr/bin:/bin', LANG: 'C.UTF-8' }, timeout: 10_000, encoding: 'utf8' })
    if (result.status !== 0) throw new Error(`owned ${mode}: ${redactDiagnostic(result.stderr)}`)
  }
  let observer: ReturnType<typeof setInterval> | undefined
  try {
    await until(() => {
      if (!existsSync(join(root, `self-admission-${driver.pid}.json`)) || !existsSync(join(root, `self-process-${driver.pid}.json`))) return false
      control('capture')
      return true
    }, 'fixed bootstrap SELF admission')
    observer = setInterval(() => {
      try { control('capture') } catch (error) { failure(error, 'periodic-capture') }
    }, 500)
    if (lifecycle) lifecycle.callback = 'running'
    await run(root, driver, endpoint, (error) => failure(error, 'stage2-append'), markGate)
    if (lifecycle) lifecycle.callback = 'returned'
  } catch (error) {
    principalFailed = true
    mirrorLocation()
    if (lifecycle && lifecycle.callback === 'running') { lifecycle.callback = 'failed'; lifecycle.callback_error_class = lifecycleErrorClass(error) }
    errors.unshift(error); errorOrder.push('principal')
    recordStructure(error, 'principal')
  }
  finally {
    if (lifecycle) lifecycle.finalization = 'running'
    try {
    if (observer) clearInterval(observer)
    try { control('capture') } catch (error) { failure(error, 'final-capture') }
    if (!driver.stdin!.destroyed) driver.stdin!.end()
    try { await until(() => code !== undefined && stdinClosed, 'difference driver cleanup', 25_000) }
    catch (error) {
      failure(error, 'driver-cleanup-wait')
      try { control('cleanup'); await until(() => code !== undefined && stdinClosed, 'owned fallback reap', 10_000) }
      catch (fallback) { failure(fallback, 'fallback-reap') }
    }
    const reports = resolve('test-results/ci/native-differences', `${vendor}-${basename(root)}`)
    mkdirSync(reports, { recursive: true })
    writeFileSync(join(reports, 'driver.log'), redactDiagnostic(log))
    for (const name of readdirSync(root)) {
      if (/^(stage-[12]-(backend\.log|scenario\.json|durable\.json|process-identities\.jsonl|process-current\.json|process-disappeared\.jsonl|cleanup\.json|closed|bin)|stage-[12]-lineage\.json|stage-[12]-native-usage-(pending|paused)\.json|stage-[12]-activity\.json|stage-[12]\.bin|stage-[12]-terminal\.bin|runtime-receipts\.jsonl|child-\d+\.json|principal\.json|shell-resolution-\d+\.json|managed-(branch|intent)-\d+\.json|refusals\.jsonl|origin-refusals\.jsonl|exec-refusals\.jsonl|exec-identities\.json|guard-identities\.json|guard-profile\.sbpl|metadata-origins\.jsonl|credential-provider\.json|controller-(ownership|cleanup)\.json|stage-2-assertions\.jsonl|process-env-refusals-\d+\.jsonl|self-(admission|process)-\d+\.json|new-child-process-\d+\.json|fixture-marker)$/.test(name)) {
        try { writeFileSync(join(reports, name), redactDiagnostic(readFileSync(join(root, name), 'utf8'))) }
        catch (error) { failure(error, 'receipt-export') }
      }
    }
    if (code !== 0) failure(new Error(`difference driver exit ${code}`), 'driver-exit')
    try {
      const structure = join(root, 'principal-structure.jsonl')
      if (existsSync(structure)) writeFileSync(join(reports, 'principal-structure.jsonl'), readFileSync(structure, 'utf8'))
    } catch (error) { failure(error, 'receipt-export') }
    writeFileSync(join(reports, 'failure-order.json'), JSON.stringify({ order: errorOrder }))
    writeFileSync(join(reports, 'delivery.json'), JSON.stringify({ root, code,
      errors: errors.map(structuralErrorClass), retained: errors.length > 0 }, null, 2))
    if (!errors.length) rmSync(root, { recursive: true, force: true })
    if (lifecycle) lifecycle.finalization = 'returned'
    } catch (error) {
      if (lifecycle) { lifecycle.finalization = 'failed'; lifecycle.finalization_error_class = lifecycleErrorClass(error) }
      if (!errors.length) throw error
      errors.push(error); errorOrder.push('finalization')
    }
  }
  if (errors.length) throw new AggregateError(errors, 'native difference failed')
}

// Controller-bound principal cases: their correctness depends on a prepared epoch, admission and binding, so they run only
// when a ticket-04 principal controller provides its writer binding (the env read by contractReporter.ts); otherwise skipped.
const controllerBound = process.platform === 'darwin' && process.env.NAVIDE_T04_WRITER_BINDING_DIR !== undefined

it.runIf(controllerBound)('native Codex production-client delayed identification and resume', async ({ task }) => {
  const lifecycle: NativeDifferenceLifecycle = { callback: 'not-started', finalization: 'not-started', case_tail: 'not-started',
    callback_error_class: 'unknown', finalization_error_class: 'unknown', case_tail_error_class: 'unknown' }
  task.meta.nativeDifferenceLifecycle = lifecycle
  try {
  await withBackend('codex', (root) => {
    const token = (input: number, output: number) => ({ type: 'event_msg', timestamp: '2026-10-04T00:00:00Z', payload: {
      type: 'token_count', info: { total_token_usage: { input_tokens: input, cached_input_tokens: 2, output_tokens: output, reasoning_output_tokens: 1 } },
    } })
    const oldPrompt = { type: 'event_msg', payload: { type: 'user_message', message: 'SYNTHETIC T04 Old prompt café' } }
    const newPrompt = { type: 'event_msg', payload: { type: 'item_completed', item: { type: 'UserMessage', content: [{ type: 'text', text: 'SYNTHETIC T04 New prompt 世界' }] } } }
    writeFileSync(join(root, 'stage-1.bin'), [oldPrompt, token(10, 3), { type: 'event_msg', payload: { type: 'agent_message', message: 'SYNTHETIC T04 First reply' } }, { type: 'event_msg', payload: { type: 'task_complete' } }].map((r) => JSON.stringify(r) + '\n').join(''))
    writeFileSync(join(root, 'stage-2.bin'), [newPrompt, token(17, 8), { type: 'event_msg', payload: { type: 'agent_message', message: 'SYNTHETIC T04 Second reply' } }, { type: 'event_msg', payload: { type: 'task_complete' } }].map((r) => JSON.stringify(r) + '\n').join(''))
  }, async (root, driver, endpoint, reportDiagnostic, markGate) => {
    const tids: string[] = []
    const lineage: any[] = []
    let previousUsage: any
    type Stage2Label = 'session-id' | 'session-exists' | 'checkpoint' | 'totals'
    markGate(0, 'case-setup')
    const source = readFileSync(resolve('tests/cli/nativeDifferences.test.ts'), 'utf8').split('\n')
    function stage2Check(label: Stage2Label, assertion: () => void): void {
      const sourceLine = source.findIndex((line) => line.includes(`stage2Check('${label}'`)) + 1
      if (!sourceLine) throw new Error('missing frozen stage2 assertion source')
      const record = (status: 'started' | 'passed' | 'failed') => appendFileSync(join(root, 'stage-2-assertions.jsonl'),
        JSON.stringify({ label, source_line: sourceLine, status }) + '\n')
      const secondary: unknown[] = []
      const write = (status: 'started' | 'passed' | 'failed') => {
        try { record(status) } catch (error) { secondary.push(error) }
      }
      write('started')
      try { assertion() } catch (error) {
        write('failed')
        secondary.forEach(reportDiagnostic)
        throw error
      }
      write('passed')
      if (secondary.length) throw new AggregateError(secondary, 'stage2 diagnostic append failed')
    }
    for (const stage of [1, 2]) {
      markGate(stage, 'endpoint')
      const wire = await endpoint(stage)
      markGate(stage, 'fixture-identities')
      const marker = readFileSync(join(root, 'fixture-marker'), 'utf8')
      const identities = JSON.parse(readFileSync(join(root, 'exec-identities.json'), 'utf8'))
      expect(JSON.parse(readFileSync(join(root, 'credential-provider.json'), 'utf8'))).toEqual({
        provider: 'synthetic substituted external account store', actual_account_queried: false,
        actual_account_absence_measured: false,
      })
      const client = createWsClient()
      const output = new Map<string, string>()
      const decoder = new TextDecoder()
      const activity: any[] = []
      const detected: any[] = []
      client.on('terminal.output', (payload) => {
        const frame = payload as TerminalOutputFrame
        output.set(frame.terminal_session_id, (output.get(frame.terminal_session_id) ?? '') + decoder.decode(frame.data, { stream: true }))
      })
      client.on('agent.activity', (payload) => activity.push(payload))
      client.on('session.detected', (payload) => detected.push(payload))
      async function request(type: string, payload: Record<string, unknown>): Promise<any> {
        const response = await client.send(type, payload, 15_000)
        expect(response.ok, JSON.stringify(response)).toBe(true)
        return response.payload!
      }
      try {
        markGate(stage, 'client-connect')
        client.connect(wire.url)
        if (stage === 1) {
          markGate(stage, 'manual-pane')
          await request('manual_pane.spawn', { workspace_path: wire.workspace, pane_id: 'difference-codex', agent: 'codex', command: 'codex' })
        }
        markGate(stage, 'resume-command')
        const base = stage === 1 ? 'codex' : buildResumeCommand('codex', wire.sessionId)
        if (stage === 2) {
          expect(base).toBe(`codex resume ${wire.sessionId}`)
          markGate(stage, 'restart-project')
          const project = await request('project.get', { workspace_path: wire.workspace })
          markGate(stage, 'session-id')
          stage2Check('session-id', () => expect(project.project.panes.find((p: any) => p.pane_id === 'difference-codex').session_id).toBe(wire.sessionId))
          markGate(stage, 'session-exists')
          const session = await request('agent.session_exists', { agent: 'codex', workspace_path: wire.workspace, session_id: wire.sessionId })
          stage2Check('session-exists', () => expect(session.exists).toBe(true))
          markGate(stage, 'checkpoint')
          stage2Check('checkpoint', () => expect(wire.restartCheckpoint).toEqual(previousUsage))
          markGate(stage, 'restart-totals')
          const persisted = await request('tokens.snapshot', { workspace_path: wire.workspace })
          stage2Check('totals', () => expect([persisted.global.all_time.input, persisted.global.all_time.output]).toEqual([12, 4]))
        }
        markGate(stage, 'terminal-create')
        const created = await request('terminal.create', { pane_id: 'difference-codex', agent_key: 'codex', cwd: wire.workspace,
          command: buildAgentPaneCommand(wire.shell, [base]), create_generation: `difference-${stage}` })
        const tid = created.terminal_session_id
        tids.push(tid)
        markGate(stage, 'child-ready')
        await until(() => differenceReady(output.get(tid) ?? '') !== undefined, 'complete actual native child READY')
        const child = differenceReady(output.get(tid)!)!
        markGate(stage, 'child-provenance')
        expect(child).toMatchObject({ cwd: wire.workspace, home: wire.home, isatty: true, ctty: true,
          fixture_marker: marker, script_sha256: identities.script.sha256, stdout_producer: 'synthetic-fixture-script' })
        const observedProcesses = () => JSON.parse(readFileSync(join(root, `stage-${stage}-process-current.json`), 'utf8'))
        markGate(stage, 'producer-os-await')
        await until(() => observedProcesses().some((p: any) => p.pid === child.pid && p.argv_phase === 'self-producer' && Array.isArray(p.argv)), 'independent OS producer identity')
        markGate(stage, 'producer-os-identity')
        const process = observedProcesses().find((p: any) => p.pid === child.pid)
        expect(process).toMatchObject({ parent: child.parent_claim, exe: identities.interpreter.path,
          argv: [identities.interpreter.path, '-I', '-S', '-B', '-u', identities.script.path, root, 'codex', ...child.argv] })
        markGate(stage, 'runtime-receipts')
        const receiptsNow = () => completeRuntimeReceipts(readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8'))
        const runtime = receiptsNow().find((row) => row.event === 'ready' && row.owner_pid === wire.backend.pid)
        const ownedRoot = receiptsNow().find((row) => row.event === 'owned' && row.session_id === tid)
        markGate(stage, 'native-owner-root')
        expect(runtime && ownedRoot, 'missing actual native owner/root receipts').toBeTruthy()
        const sample = observedProcesses()
        const backendProcess = sample.find((row: any) => row.pid === wire.backend.pid && row.created === wire.backend.created)
        const nativeProcess = sample.find((row: any) => row.pid === runtime.pid && row.created === runtime.created)
        const guard = JSON.parse(readFileSync(join(root, 'guard-identities.json'), 'utf8'))
        markGate(stage, 'backend-os-present')
        expect(backendProcess, 'missing actual backend OS identity').toBeTruthy()
        markGate(stage, 'runtime-os-present')
        expect(nativeProcess, 'missing actual runtime OS identity').toBeTruthy()
        markGate(stage, 'backend-parent')
        expect(backendProcess.parent).toBe(wire.driver.pid)
        markGate(stage, 'backend-executable')
        expect(backendProcess.exe).toBe(identities.interpreter.path)
        markGate(stage, 'backend-image')
        expect(backendProcess.image.sha256).toBe(guard.files.interpreter.sha256)
        markGate(stage, 'backend-argv')
        expect(backendProcess.argv.slice(1, 6)).toEqual(['-I', '-S', guard.files['account_bootstrap'].path, 'backend', root])
        markGate(stage, 'runtime-parent')
        expect(nativeProcess.parent).toBe(backendProcess.pid)
        markGate(stage, 'runtime-executable')
        expect(nativeProcess.exe).toBe(guard.files.runtime.path)
        markGate(stage, 'runtime-image')
        expect(nativeProcess.image.sha256).toBe(runtime.executableSha256)
        expect(nativeProcess.image.sha256).toBe(guard.files.runtime.sha256)
        markGate(stage, 'runtime-argv')
        // Sidecar argv (cli_runtime.py `_start`): `--wire 2 --runtime-generation G --owner-pid P --ownership-journal J --control-fd N`.
        expect(nativeProcess.argv.slice(0, 7)).toEqual([guard.files.runtime.path, '--wire', '2', '--runtime-generation', runtime.runtime_generation, '--owner-pid', String(backendProcess.pid)])
        expect(nativeProcess.argv.slice(7, 8)).toEqual(['--ownership-journal'])
        expect(nativeProcess.argv.slice(9, 10)).toEqual(['--control-fd'])
        expect(nativeProcess.argv).toHaveLength(11)
        markGate(stage, 'producer-parent')
        expect(process.parent).toBe(nativeProcess.pid)
        markGate(stage, 'pty-root-pid')
        expect(process.pid).toBe(ownedRoot.root.pid)
        markGate(stage, 'birth-order')
        expect(process.created).toBeGreaterThanOrEqual(nativeProcess.created)
        expect(nativeProcess.created).toBeGreaterThanOrEqual(backendProcess.created)
        markGate(stage, 'pty-group-start')
        expect(process.pgid).toBe(ownedRoot.root.pgid)
        expect(process.legacy_start).toBe(ownedRoot.root.start_value)
        expect(ownedRoot.root.start_kind).toBe('posix-lstart')
        markGate(stage, 'producer-image')
        expect(process.image.sha256).toBe(guard.files.interpreter.sha256)
        markGate(stage, 'runtime-generation')
        expect(ownedRoot.runtime_generation).toBe(runtime.runtime_generation)
        markGate(stage, 'shell-resolution')
        const shellResolution = JSON.parse(readFileSync(join(root, `shell-resolution-${child.pid}.json`), 'utf8'))
        expect(shellResolution).toEqual({ shell_pid: process.pid, parent: nativeProcess.pid,
          path: guard.shell_path, resolved: identities.shim.path, PS1_empty: true, TERM_PROGRAM_empty: true })
        expect(identities.profile).toBeTruthy()
        markGate(stage, 'lineage-export')
        const bound = { stage, fixture_marker: marker, script_sha256: identities.script.sha256,
          shell_resolution: shellResolution, shell_birth: process.created,
          shell_profile_sha256: identities.profile.sha256, os_startup: guard.os_startup, os_selector: guard.os_selector,
          backend: backendProcess, runtime: nativeProcess, runtime_generation: runtime.runtime_generation,
          native_pty_root: ownedRoot.root, producer: process,
          enforced_not_sampled: ['registered shim exec step; frozen forwarding body and kernel executable allowlist'],
          credential_provider: 'synthetic substituted external account store; no actual-account measurements' }
        writeFileSync(join(root, `stage-${stage}-lineage.json`), JSON.stringify(bound, null, 2))
        lineage.push(bound)
        markGate(stage, 'vendor-command')
        expect(child.argv.includes('--session-id')).toBe(false)
        if (stage === 1) {
          markGate(stage, 'no-early-session')
          await delay(500)
          expect(detected).toEqual([])
          const project = await request('project.get', { workspace_path: wire.workspace })
          expect(project.project.panes.find((p: any) => p.pane_id === 'difference-codex').session_id ?? '').toBe('')
        } else expect(child.argv.slice(0, 2)).toEqual(['resume', wire.sessionId])
        markGate(stage, 'stage-input')
        await request('terminal.input', { terminal_session_id: tid, data: `stage ${stage}\r` })
        markGate(stage, 'rollout-boundary')
        await until(() => (output.get(tid) ?? '').includes(`DIFFERENCE_STAGE ${stage}`), 'external rollout written')
        markGate(stage, 'delayed-identity')
        if (stage === 1) await until(() => detected.some((e) => e.session_id === wire.sessionId), 'delayed native identity persisted')
        markGate(stage, 'completion-boundary')
        await until(() => activity.some((e) => e.event_type === 'turn_complete'), 'native completion boundary')
        markGate(stage, 'activity-vocabulary')
        expect(activity.filter((e) => e.event_type === 'turn_complete').map((e) => e.text)).toEqual([stage === 1 ? 'SYNTHETIC T04 First reply' : 'SYNTHETIC T04 Second reply'])
        expect(activity.every((e) => e.pane_id === 'difference-codex' && e.workspace_path === wire.workspace)).toBe(true)
        expect(detected.every((e) => e.session_id === wire.sessionId)).toBe(true)
        expect(activity.every((e) => !e.text || ['SYNTHETIC T04 Old prompt café', 'SYNTHETIC T04 New prompt 世界',
          'SYNTHETIC T04 First reply', 'SYNTHETIC T04 Second reply'].includes(e.text))).toBe(true)
        // The existing 300s deferred ingestion is flushed by normal shutdown.
        // Keep the pane registered until then; verify exact durable totals below.
        markGate(stage, 'complete-terminal')
        await until(() => (output.get(tid) ?? '').endsWith(`DIFFERENCE_STAGE ${stage}\r\n`),
          'complete synthetic stage PTY line')
        markGate(stage, 'full-terminal-equality')
        const allFrames = output.get(tid) ?? ''
        writeFileSync(join(root, `stage-${stage}-terminal.bin`), allFrames)
        const readyBody = readFileSync(join(root, `child-${child.pid}.json`), 'utf8')
        const expectedFrames = `DIFFERENCE_READY ${readyBody}\r\n`
          + (child.echo_enabled ? `stage ${stage}\r\n` : '') + `DIFFERENCE_STAGE ${stage}\r\n`
        // No filtering/stripping and no raw unexpected content in assertions.
        expect(allFrames === expectedFrames, 'unexpected terminal frame vocabulary/source').toBe(true)
        markGate(stage, 'shutdown-flush')
        driver.stdin!.write(stage === 1 ? 'restart\n' : 'stop\n')
        await until(() => existsSync(join(root, `stage-${stage}-closed`)), 'durable shutdown', 25_000)
      } finally { client.dispose('difference stage complete') }
      markGate(stage, 'durable-totals')
      const durable = JSON.parse(readFileSync(join(root, `stage-${stage}-durable.json`), 'utf8'))
      expect([durable.totals.input, durable.totals.output]).toEqual(stage === 1 ? [12, 4] : [19, 9])
      expect(durable.verified_synthetic_sources).toHaveLength(1)
      expect(durable.sources[0].usage).toMatchObject({ kind: 'jsonl', input_total: stage === 1 ? 12 : 19, output_total: stage === 1 ? 4 : 9 })
      if (stage === 2) expect(durable.sources[0].usage.offset).toBeGreaterThan(previousUsage.offset)
      previousUsage = durable.sources[0].usage
    }
    markGate(0, 'native-lifecycle')
    const receipts = readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
    const ready = receipts.filter((r) => r.event === 'ready')
    expect(ready).toHaveLength(2)
    expect(new Set(ready.map((r) => r.runtime_generation)).size).toBe(2)
    const sha = createHash('sha256').update(readFileSync(resolve('native/navide-cli-runtime/target/release/navide-cli-runtime'))).digest('hex')
    expect(ready.every((r) => r.executableSha256 === sha)).toBe(true)
    const owned = receipts.filter((r) => r.event === 'owned')
    expect(owned.map((r) => r.session_id)).toEqual(tids)
    const cleaned = receipts.filter((r) => r.event === 'cleaned')
    expect(cleaned.map((r) => r.session_id)).toEqual(tids)
    expect(cleaned.every((r) => r.cleanup_complete && r.root_reaped && !r.survivors.length && !r.unverifiable.length)).toBe(true)
    const observed = receipts.filter((r) => r.event === 'observed')
    expect(observed.some((r) => r.parser === 'rust-codex-jsonl-v1' && r.mode === 'usage')).toBe(true)
    markGate(0, 'principal-export')
    writeFileSync(join(root, 'principal.json'), JSON.stringify({ seam: 'production TS WS client / authenticated Python / native Rust PTY / durable stores', tids, ready, lineage,
      credential_provider: 'synthetic substituted external account store; no actual-account measurements',
      assertions: ['delayed ID after real input', 'subcommand resume', 'old/new prompt format', 'normal shutdown token flush', 'authenticated restart totals', 'cumulative checkpoint restart', 'all terminal frames exact synthetic/PTY echo vocabulary', 'correlated actual backend/native/PTY/producer identities', 'metadata creator bound to observed producer', 'owned cleanup'] }, null, 2))
  }, lifecycle)
  lifecycle.case_tail = 'running'
  task.meta.cliRuntimeReference = 'codex-delayed-resume'
  lifecycle.case_tail = 'returned'
  } catch (error) {
    if (lifecycle.case_tail === 'running') { lifecycle.case_tail = 'failed'; lifecycle.case_tail_error_class = lifecycleErrorClass(error) }
    throw error
  }
}, 120_000)

it.runIf(controllerBound)('native OpenCode production-client WAL SQLite and resume syntax variant', async ({ task }) => {
  const lifecycle: NativeDifferenceLifecycle = { callback: 'not-started', finalization: 'not-started', case_tail: 'not-started',
    callback_error_class: 'unknown', finalization_error_class: 'unknown', case_tail_error_class: 'unknown' }
  task.meta.nativeDifferenceLifecycle = lifecycle
  try {
    await withBackend('opencode', (root) => {
      for (const stage of [1, 2]) writeFileSync(join(root, `stage-${stage}.bin`), JSON.stringify({
        prompt: `SYNTHETIC T04 Prompt ${stage} [at-pane:difference-opencode]`,
        reply: `SYNTHETIC T04 SQLite reply ${stage}`, completed: 1_700_000_000_000 + stage,
        tokens: { input: stage === 1 ? 10 : 7, output: stage === 1 ? 5 : 3, cache: { read: 2, write: 3 }, reasoning: 1 },
      }))
    }, async (root, driver, endpoint, _reportDiagnostic, markGate) => {
      const tids: string[] = []
      const lineage: any[] = []
      let previousUsage: any
      const usageObservations: any[] = []
      for (const stage of [1, 2]) {
        markGate(stage, 'endpoint')
        const wire = await endpoint(stage)
        const identities = JSON.parse(readFileSync(join(root, 'exec-identities.json'), 'utf8'))
        const guard = JSON.parse(readFileSync(join(root, 'guard-identities.json'), 'utf8'))
        const marker = readFileSync(join(root, 'fixture-marker'), 'utf8')
        const client = createWsClient()
        const output = new Map<string, string>()
        const decoder = new TextDecoder()
        const activity: any[] = []
        const detected: any[] = []
        client.on('terminal.output', (payload) => {
          const frame = payload as TerminalOutputFrame
          output.set(frame.terminal_session_id, (output.get(frame.terminal_session_id) ?? '') + decoder.decode(frame.data, { stream: true }))
        })
        client.on('agent.activity', (payload) => activity.push(payload))
        client.on('session.detected', (payload) => detected.push(payload))
        async function request(type: string, payload: Record<string, unknown>): Promise<any> {
          const response = await client.send(type, payload, 15_000)
          expect(response.ok).toBe(true)
          return response.payload!
        }
        try {
          markGate(stage, 'client-connect')
          client.connect(wire.url)
          let base = 'opencode'
          if (stage === 1) {
            markGate(stage, 'manual-pane')
            await request('manual_pane.spawn', { workspace_path: wire.workspace, pane_id: 'difference-opencode', agent: 'opencode', command: 'opencode' })
          } else {
            markGate(stage, 'resume-command')
            const index = AGENT_SPECS.findIndex((spec) => spec.agentKey === 'opencode')
            expect(index).toBeGreaterThanOrEqual(0)
            const original = AGENT_SPECS[index]
            expect(buildResumeCommand('opencode', wire.sessionId)).toBe(`opencode --session ${wire.sessionId}`)
            try {
              AGENT_SPECS[index] = { ...original, resumeArgs: (id) => `-s ${id}` }
              base = buildResumeCommand('opencode', wire.sessionId)
              expect(base).toBe(`opencode -s ${wire.sessionId}`)
            } finally { AGENT_SPECS[index] = original }
            expect(AGENT_SPECS[index]).toBe(original)
            expect(buildResumeCommand('opencode', wire.sessionId)).toBe(`opencode --session ${wire.sessionId}`)
            markGate(stage, 'restart-project')
            const project = await request('project.get', { workspace_path: wire.workspace })
            expect(project.project.panes.find((p: any) => p.pane_id === 'difference-opencode').session_id).toBe(wire.sessionId)
            expect((await request('agent.session_exists', { agent: 'opencode', workspace_path: wire.workspace, session_id: wire.sessionId })).exists).toBe(true)
            markGate(stage, 'checkpoint')
            expect(wire.restartCheckpoint).toEqual(previousUsage)
            const persisted = await request('tokens.snapshot', { workspace_path: wire.workspace })
            expect([persisted.global.all_time.input, persisted.global.all_time.output]).toEqual([15, 6])
          }
          markGate(stage, 'terminal-create')
          // Renderer metadata for this case: EVERY field spawnPane sends in terminal.create metadata (App.vue:6601-6613, fields 6602-6612), for a manual OpenCode pane.
          // roleKey/stageId: manual spawn card (ControlPane.vue:2061-2062, pickedRole default '' at :1445) and the resume path (App.vue:6990-6991) -> ''.
          // stage_id: alias of stageId -> ''. origin 'manual' (App.vue:6717 / :6997). workspace_path: opts.workspacePath ?? ''.
          // explicit_session_id: pinnedSessionId || explicitSessionId (:6607); fresh pane: pinnedSessionId undefined, explicitSessionId '' (OpenCode
          // does not pin an id, sessionHeal.ts:127-130) -> ''; resume pane: pinnedSessionId = resumeSessionId (:6405-6409) -> the resume id.
          // session_marker: at-pane:<pane id> for a fresh non-login marker pane, '' for a resume pane (:6450-6453; opencode.ts:32 needsSessionMarker).
          // session_home_id: '' (OpenCode has no needsSessionHome; resume-command.ts:103-120). slot_label/profile_id/run_group_id: '' (:6610-6612).
          const metadata = stage === 1
            ? { roleKey: '', stageId: '', stage_id: '', origin: 'manual', workspace_path: wire.workspace, explicit_session_id: '',
                session_marker: 'at-pane:difference-opencode', session_home_id: '', slot_label: '', profile_id: '', run_group_id: '' }
            : { roleKey: '', stageId: '', stage_id: '', origin: 'manual', workspace_path: wire.workspace, explicit_session_id: wire.sessionId,
                session_marker: '', session_home_id: '', slot_label: '', profile_id: '', run_group_id: '' }
          const created = await request('terminal.create', { pane_id: 'difference-opencode', agent_key: 'opencode', cwd: wire.workspace,
            command: buildAgentPaneCommand(wire.shell, [base]), create_generation: `difference-${stage}`, metadata })
          const tid = created.terminal_session_id
          tids.push(tid)
          markGate(stage, 'child-ready')
          await until(() => differenceReady(output.get(tid) ?? '') !== undefined, 'complete actual SQLite fixture READY')
          const child = differenceReady(output.get(tid)!)!
          expect(child).toMatchObject({ cwd: wire.workspace, home: wire.home, isatty: true, ctty: true,
            fixture_marker: marker, script_sha256: identities.script.sha256, stdout_producer: 'synthetic-fixture-script' })
          const processes = () => JSON.parse(readFileSync(join(root, `stage-${stage}-process-current.json`), 'utf8'))
          markGate(stage, 'producer-os-await')
          await until(() => processes().some((p: any) => p.pid === child.pid && p.argv_phase === 'self-producer' && Array.isArray(p.argv)), 'SQLite producer SELF/OS correlation')
          const producer = processes().find((p: any) => p.pid === child.pid)
          markGate(stage, 'producer-os-identity')
          expect(producer).toMatchObject({ parent: child.parent_claim, exe: identities.interpreter.path,
            argv: [identities.interpreter.path, '-I', '-S', '-B', '-u', identities.script.path, root, 'opencode', ...child.argv] })
          markGate(stage, 'runtime-receipts')
          const receipts = completeRuntimeReceipts(readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8'))
          const ready = receipts.find((row) => row.event === 'ready' && row.owner_pid === wire.backend.pid)!
          const owned = receipts.find((row) => row.event === 'owned' && row.session_id === tid)!
          expect(ready && owned).toBeTruthy()
          const sample = processes()
          const backend = sample.find((row: any) => row.pid === wire.backend.pid && row.created === wire.backend.created)
          const runtime = sample.find((row: any) => row.pid === ready.pid && row.created === ready.created)
          expect(backend).toMatchObject({ parent: wire.driver.pid, exe: identities.interpreter.path })
          expect(backend.image.sha256).toBe(guard.files.interpreter.sha256)
          expect(backend.argv.slice(1, 6)).toEqual(['-I', '-S', guard.files.account_bootstrap.path, 'backend', root])
          expect(runtime).toMatchObject({ parent: backend.pid, exe: guard.files.runtime.path })
          expect(runtime.image.sha256).toBe(ready.executableSha256)
          expect(runtime.image.sha256).toBe(guard.files.runtime.sha256)
          expect(runtime.argv.slice(0, 7)).toEqual([guard.files.runtime.path, '--wire', '2', '--runtime-generation', ready.runtime_generation, '--owner-pid', String(backend.pid)])
          expect([runtime.argv[7], runtime.argv[9], runtime.argv.length]).toEqual(['--ownership-journal', '--control-fd', 11])
          markGate(stage, 'shell-resolution')
          const shell = JSON.parse(readFileSync(join(root, `shell-resolution-${owned.root.pid}.json`), 'utf8'))
          markGate(stage, 'managed-lineage-read')
          const branch = JSON.parse(readFileSync(join(root, `managed-branch-${owned.root.pid}.json`), 'utf8'))
          const intent = JSON.parse(readFileSync(join(root, `managed-intent-${backend.pid}.json`), 'utf8'))
          const self = JSON.parse(readFileSync(join(root, `self-admission-${producer.pid}.json`), 'utf8'))
          const selfRow = JSON.parse(readFileSync(join(root, `self-process-${producer.pid}.json`), 'utf8'))
          const descriptor = JSON.parse(readFileSync(join(root, 'managed-opencode.json'), 'utf8'))
          const managed = verifyManagedOpenCodeLineage({ stage, root, child, producer, backend, runtime, ready, owned,
            identities, guard, shell, branch, intent, self, selfRow, sample, descriptor,
            guardSha256: createHash('sha256').update(readFileSync(join(root, 'guard-identities.json'))).digest('hex') },
            (gate) => markGate(stage, gate))
          markGate(stage, 'lineage-export')
          expect(JSON.parse(readFileSync(join(root, 'credential-provider.json'), 'utf8'))).toEqual({
            provider: 'synthetic substituted external account store', actual_account_queried: false, actual_account_absence_measured: false })
          const bound = { stage, fixture_marker: marker, script_sha256: identities.script.sha256, producer, backend, runtime,
            runtime_generation: ready.runtime_generation, native_pty_root: owned.root, shell_resolution: shell,
            shell_birth: managed.root_process.created, managed_lineage: managed,
            shell_profile_sha256: identities.profile.sha256, os_startup: guard.os_startup, os_selector: guard.os_selector }
          writeFileSync(join(root, `stage-${stage}-lineage.json`), JSON.stringify(bound, null, 2))
          lineage.push(bound)
          markGate(stage, 'vendor-command')
          if (stage === 2) expect(child.argv.slice(0, 2)).toEqual(['-s', wire.sessionId])
          else expect(child.argv.some((arg: string) => arg === '-s' || arg === '--session')).toBe(false)
          if (stage === 1) {
            markGate(stage, 'no-early-session')
            await delay(500)
            expect(detected).toEqual([])
            expect((await request('project.get', { workspace_path: wire.workspace })).project.panes.find((p: any) => p.pane_id === 'difference-opencode').session_id ?? '').toBe('')
          }
          // The admitted, read-only support probe copies actual native receipts;
          // it never calls a parser, drains ingestion, or changes the 300s policy.
          const nativeUsage = (phase: 'pending' | 'paused') => {
            const probe = spawnSync(resolve('backend/.venv/bin/python'), ['-I', '-S', '-B',
              resolve('backend/tests/cli_regression/support/difference_cleanup.py'), 'usage-evidence', root,
              String(driver.pid), String(process.pid)], { env: { HOME: join(root, 'home'),
                PATH: '/usr/bin:/bin', LANG: 'C.UTF-8' }, timeout: 10_000, encoding: 'utf8' })
            if (probe.status !== 0) throw new Error(`native usage evidence: ${redactDiagnostic(probe.stderr)}`)
            const evidence = JSON.parse(probe.stdout)
            if (!evidence) return undefined
            const pending = [3 * stage]
            const row_id = 3 * stage + (phase === 'paused' ? 1 : 0)
            const anchor = phase === 'paused' ? `msg_child_${stage}|ses_child_${stage}` : `msg_${stage}_assistant|ses_native_difference`
            const expectation = { generation: ready.runtime_generation, path: join(root, 'share/opencode/opencode.db'),
              dev: evidence.database.dev, ino: evidence.database.ino,
              identity: `${evidence.database.dev}:${evidence.database.ino}`, row_id, anchor, pending, event_count: 0 }
            const observation = evidence.usage.find(({ record }: any) => matchesUsageObservation(record, expectation))
            if (!observation) return undefined
            return { stage, phase, scope: 'native reader observation plus WS totals; not app-store commit',
              expectation, observation, source: evidence.source, database: evidence.database,
              candidate_sha256: ready.executableSha256 }
          }
          for (const [action, phase] of [['stage', 'pending'], ['pause', 'paused'], ['settle', 'settled']]) {
            markGate(stage, 'stage-input')
            await request('terminal.input', { terminal_session_id: tid, data: `${action} ${stage}\r` })
            await until(() => (output.get(tid) ?? '').includes(`DIFFERENCE_SQLITE ${stage} ${phase}`), 'fixed SQLite logical phase')
            if (phase !== 'settled') {
              await delay(500)
              expect(activity.filter((event) => event.event_type === 'turn_complete')).toEqual([])
              markGate(stage, phase === 'pending' ? 'pending-usage-observation' : 'paused-usage-observation')
              if (stage === 2) {
                // Source-derived expectation (native-difference finding F2; NOT verified here as a live negative): a resumed explicit-id
                // shared-database pane has no live scan until a token event supplies a file hint, so credit is expected only at the shutdown flush.
                // What this run verifies: WS totals stay [15,6] through these phases, then durable totals are [27,10] after the shutdown flush.
                const unchanged = await request('tokens.snapshot', { workspace_path: wire.workspace })
                expect([unchanged.global.all_time.input, unchanged.global.all_time.output]).toEqual([15, 6])
                continue
              }
              let proof: any
              // A snapshot schedules the genuine live scan; only its native receipt releases this gate.
              await until(async () => {
                await request('tokens.snapshot', { workspace_path: wire.workspace })
                return Boolean(proof = nativeUsage(phase as 'pending' | 'paused'))
              }, 'selected native current-phase zero usage observation')
              const totals = await request('tokens.snapshot', { workspace_path: wire.workspace })
              proof.ws_totals = [totals.global.all_time.input, totals.global.all_time.output]
              expect(proof.ws_totals).toEqual([0, 0])
              usageObservations.push(proof)
              writeFileSync(join(root, `stage-${stage}-native-usage-${phase}.json`), JSON.stringify(proof, null, 2))
            }
          }
          markGate(stage, 'delayed-identity')
          if (stage === 1) await until(() => detected.some((event) => event.session_id === wire.sessionId), 'SQLite marker identity persisted')
          markGate(stage, 'completion-boundary')
          try {
            await until(() => activity.some((event) => event.event_type === 'turn_complete'), 'SQLite top-level settlement')
          } finally {
            // Evidence only: the delivered agent.activity events exactly as the assertions below see them (finite fields; text bounded).
            const bounded = (value: unknown) => typeof value === 'string' ? value.slice(0, 200) : null
            writeFileSync(join(root, `stage-${stage}-activity.json`), JSON.stringify(activity.map((event, arrival_index) => ({ arrival_index,
              event_type: bounded(event.event_type), session_id: bounded(event.session_id), detail: bounded(event.detail), pane_id: bounded(event.pane_id),
              workspace_path: bounded(event.workspace_path), text: bounded(event.text) })), null, 2))
          }
          markGate(stage, 'completion-turn-text')
          expect(activity.filter((event) => event.event_type === 'turn_complete').map((event) => event.text)).toEqual([`SYNTHETIC T04 SQLite reply ${stage}`])
          // The child (sub-agent) session's completion must actually have been delivered, exactly once, before the top-level completion, with the
          // BASELINE attribution (native-difference finding F1: attribution.py _lookup_pane_for has no owner for a child session id).
          markGate(stage, 'completion-child-event')
          const childEvents = activity.filter((event) => event.session_id === `ses_child_${stage}`)
          expect(childEvents.map((event) => ({ event_type: event.event_type, detail: event.detail, pane_id: event.pane_id, workspace_path: event.workspace_path })))
            .toEqual([{ event_type: 'agent_active', detail: 'subagent:done', pane_id: '', workspace_path: wire.workspace }])
          markGate(stage, 'completion-child-order')
          expect(activity.indexOf(childEvents[0])).toBeLessThan(activity.findIndex((event) => event.event_type === 'turn_complete'))
          // Top-level session events attribute to the pane; every other event is that child event.
          markGate(stage, 'completion-top-level-attribution')
          expect(activity.every((event) => event.workspace_path === wire.workspace && (event.session_id === wire.sessionId
            ? event.pane_id === 'difference-opencode'
            : event.session_id === `ses_child_${stage}` && event.pane_id === '' && event.detail === 'subagent:done'))).toBe(true)
          markGate(stage, 'completion-pty-vocabulary')
          // Production bytes before the CLI starts on the managed route's shell-check-unverified branch (exit 76): the backend refuses GO for an
          // unverified store (credential_store.py:71-72 StoreUnverified -> ws_handlers.py:7464 release(False)), and the wrapper's helper then
          // prints this notice on the pane's stderr (credential_path_helper.py:85-86) before returning 76 (:91-92); the fixture shim is never a
          // standard executable (:23-45), so this branch always reports unverified. Proven byte-exact by E/opencode-ws-ptynotice-34.json.
          const credentialNotice = branch.branch === 'shell-check-unverified'
            ? 'Navide: credential store is unverified; account changes are unavailable for this pane.\r\n' : ''
          const expected = credentialNotice + `DIFFERENCE_READY ${readFileSync(join(root, `child-${child.pid}.json`), 'utf8')}\r\n`
            + [['stage', 'pending'], ['pause', 'paused'], ['settle', 'settled']].map(([action, phase]) =>
              (child.echo_enabled ? `${action} ${stage}\r\n` : '') + `DIFFERENCE_SQLITE ${stage} ${phase}\r\n`).join('')
          try {
            await until(() => (output.get(tid) ?? '').endsWith(`DIFFERENCE_SQLITE ${stage} settled\r\n`), 'complete SQLite phase PTY record')
          } finally {
            // Evidence only, as the Codex branch does before its equality: the unfiltered accumulated PTY frames.
            writeFileSync(join(root, `stage-${stage}-terminal.bin`), output.get(tid) ?? '')
          }
          expect(output.get(tid) === expected, 'exact SQLite synthetic terminal vocabulary').toBe(true)
          markGate(stage, 'shutdown-flush')
          driver.stdin!.write(stage === 1 ? 'restart\n' : 'stop\n')
          await until(() => existsSync(join(root, `stage-${stage}-closed`)), 'SQLite durable shutdown', 25_000)
        } finally { client.dispose('SQLite stage complete') }
        markGate(stage, 'durable-totals')
        const durable = JSON.parse(readFileSync(join(root, `stage-${stage}-durable.json`), 'utf8'))
        expect([durable.totals.input, durable.totals.output]).toEqual(stage === 1 ? [15, 6] : [27, 10])
        expect(durable.verified_synthetic_sources).toHaveLength(1)
        expect(durable.sources[0].usage).toMatchObject({ kind: 'sqlite' })
        if (stage === 2) expect(durable.sources[0].usage.row_id).toBeGreaterThan(previousUsage.row_id)
        previousUsage = durable.sources[0].usage
      }
      markGate(0, 'native-lifecycle')
      const receipts = completeRuntimeReceipts(readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8'))
      const ready = receipts.filter((row) => row.event === 'ready')
      expect(ready).toHaveLength(2)
      expect(new Set(ready.map((row) => row.runtime_generation)).size).toBe(2)
      expect(receipts.filter((row) => row.event === 'owned').map((row) => row.session_id)).toEqual(tids)
      const cleaned = receipts.filter((row) => row.event === 'cleaned')
      expect(cleaned.map((row) => row.session_id)).toEqual(tids)
      expect(cleaned.every((row) => row.cleanup_complete && row.root_reaped && !row.survivors.length && !row.unverifiable.length)).toBe(true)
      expect(receipts.some((row) => row.event === 'observed' && row.parser === 'rust-opencode-sqlite-v1' && row.mode === 'usage')).toBe(true)
      markGate(0, 'principal-export')
      expect(usageObservations).toHaveLength(2)
      writeFileSync(join(root, 'principal.json'), JSON.stringify({ seam: 'production TS WS client / authenticated Python / native Rust PTY / durable SQLite stores', tids, ready, lineage,
        pending_usage: usageObservations,
        assertions: ['SQLite logical producer origins', 'no early session', 'pending/tool/child not top-level completion', 'stage-1 native current-phase zero usage observation plus WS totals (not store commit)', 'stage-2 WS totals unchanged [15,6] through intermediate phases, then durable [27,10] after shutdown flush', 'child-session activity baseline attribution', 'top-level settlement', 'test-only declaration syntax variant restored', 'durable restart dedup', 'exact terminal vocabulary', 'owned cleanup'] }, null, 2))
    }, lifecycle)
    lifecycle.case_tail = 'running'
    task.meta.cliRuntimeReference = 'opencode-sqlite-resume-variant'
    lifecycle.case_tail = 'returned'
  } catch (error) {
    if (lifecycle.case_tail === 'running') { lifecycle.case_tail = 'failed'; lifecycle.case_tail_error_class = lifecycleErrorClass(error) }
    throw error
  }
}, 120_000)
