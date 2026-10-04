import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { buildAgentPaneCommand } from '../../src/main/plugins/agentPaneCommand'
import { redactDiagnostic, saveTrialArtifacts } from './baselineArtifacts'
import { hasExactLiveTotals, matchesSessionDetection, runtimeStudyEnabled } from './runtimeBaselineAssertions'

const workload = JSON.parse(readFileSync(resolve('tests/fixtures/cli-regression/runtime-workload.json'), 'utf8'))
const python = resolve('backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const measuring = process.env.ACR_MEASURE === '1'
const closeWalDiagnostic = process.env.ACR_DIAGNOSE_CLOSE_WAL === '1'

async function until(predicate: () => boolean, message: string, timeout = workload.deadlineMs): Promise<void> {
  const deadline = performance.now() + timeout
  while (!predicate()) {
    if (performance.now() >= deadline) throw new Error(message)
    await delay(5)
  }
}

it.runIf(runtimeStudyEnabled(process.env))('runs the frozen isolated workload through the production WS client and native PTY', async () => {
  expect(existsSync(python), 'run uv --project backend sync --locked').toBe(true)
  expect(measuring && (closeWalDiagnostic || process.env.ACR_DIAGNOSE === '1'), 'diagnostic trace/WAL-close is never a measurement').toBe(false)
  const reports = resolve('test-results/ci/runtime-baseline', process.env.ACR_EVIDENCE_REVISION ?? 'correction-r2-check')
  mkdirSync(reports, { recursive: true })
  const failures: string[] = []
  for (const count of workload.sessionCounts) {
    for (let repeat = 0; repeat < (measuring ? workload.measurementRepeats : 1); repeat++) {
      const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-runtime-baseline-')))
      const started = performance.now()
      const driver = spawn(python, ['-m', 'tests.cli_regression.support.runtime_baseline', root, String(count), String(repeat)], {
        cwd: resolve('backend'), stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
      })
      let log = ''
      let exitCode: number | null | undefined
      driver.stdout.on('data', (chunk) => { log += chunk })
      driver.stderr.on('data', (chunk) => { log += chunk })
      driver.on('error', (error) => { log += error.message; exitCode = -1 })
      driver.on('exit', (code) => { exitCode = code })
      let connections = 0
      const client = createWsClient({ onStatus: (status) => { if (status === 'connected') connections++ } })
      const output = new Map<string, string>()
      const decoders = new Map<string, TextDecoder>()
      const events: Array<{ type: string; payload: Record<string, unknown>; wallMs: number }> = []
      const timeline: Array<Record<string, unknown>> = []
      for (const type of ['agent.activity', 'session.detected', 'terminal.exit']) {
        client.on(type, (payload) => {
          const event = { type, payload: payload as Record<string, unknown>, wallMs: Date.now() }
          events.push(event)
          timeline.push(event)
        })
      }
      client.on('terminal.output', (payload) => {
        const frame = payload as TerminalOutputFrame
        const decoder = decoders.get(frame.terminal_session_id) ?? new TextDecoder()
        decoders.set(frame.terminal_session_id, decoder)
        const previous = output.get(frame.terminal_session_id) ?? ''
        const text = decoder.decode(frame.data, { stream: true })
        output.set(frame.terminal_session_id, previous + text)
        timeline.push({ type: 'terminal.output', tid: frame.terminal_session_id, wallMs: Date.now(), offset: previous.length, length: text.length })
      })
      const baselineWarnings: string[] = []
      const latencies: Record<string, number[]> = {}
      const phases: Array<{ name: string; wallMs: number }> = [{ name: 'cold_startup', wallMs: Date.now() }]
      const phaseOutcomes: Record<string, string> = { cold_startup: 'running' }
      const observationFailures: Array<{ pane: string; vendor: string; kind: string; message: string }> = []
      const observationOutcomes: Array<{ pane: string; vendor: string; kind: string; status: string; latencyMs?: number }> = []
      const tokenSnapshots: Record<string, unknown> = {}
      const mark = (name: string): void => {
        const previous = phases.at(-1)!.name
        if (phaseOutcomes[previous] === 'running') phaseOutcomes[previous] = 'passed'
        phases.push({ name, wallMs: Date.now() })
        phaseOutcomes[name] = name === 'finished' ? 'passed' : 'running'
      }
      const observationFailure = (peer: Peer, kind: string, error: unknown): void => {
        const message = redactDiagnostic(error instanceof Error ? error.message : String(error))
        observationFailures.push({ pane: peer.pane, vendor: peer.vendor, kind, message })
        observationOutcomes.push({ pane: peer.pane, vendor: peer.vendor, kind, status: message.endsWith('deadline') ? 'censored' : 'failed' })
        phaseOutcomes.observation = 'failed'
        failures.push(`${count}/${repeat}: ${peer.vendor} ${kind}: ${message}`)
      }
      const record = (name: string, start: number): void => { (latencies[name] ??= []).push(performance.now() - start) }
      async function request<T = Record<string, unknown>>(type: string, payload: Record<string, unknown>): Promise<T> {
        timeline.push({ type, tid: payload.terminal_session_id, pane: payload.pane_id, wallMs: Date.now(), ...(type === 'terminal.input' ? { input: String(payload.data).slice(0, 80) } : {}) })
        const response = await client.send<T>(type, payload, workload.deadlineMs)
        expect(response.ok, JSON.stringify(response)).toBe(true)
        return response.payload!
      }
      type Peer = { pane: string; vendor: string; workspace: string; command: string[]; argv: string[]; wrapped: boolean; commandLine: string }
      type Running = Peer & { tid: string; pid: number; sessionId: string }
      type Sample = { generation: string; backend: { pid: number; created: number }; wallMs: number; processes: Array<{ pid: number; created: number; category: string }> }
      const identifiedSessions = new Map<string, string>()
      let peers: Running[] = []
      let passed = false
      let evidenceValid = false
      let activeSamples = 0
      let failure: string | null = null
      try {
        await until(() => {
          if (exitCode !== undefined) throw new Error(`driver exited: ${log}`)
          return existsSync(join(root, 'endpoint.json'))
        }, 'backend readiness deadline', 35_000)
        const endpoint = JSON.parse(readFileSync(join(root, 'endpoint.json'), 'utf8'))
        expect(endpoint.actualRuntime).toBe('python-native-pty')
        client.connect(endpoint.url)
        await until(() => connections === 1, 'authenticated WS connection deadline')
        record('cold_backend_ready_ms', started)
        mark('empty_idle')
        await delay(workload.idleMs)
        async function create(peer: Peer, label: string): Promise<Running> {
          const start = performance.now()
          const created = await request<{ terminal_session_id: string; pid: number }>('terminal.create', {
            pane_id: peer.pane, agent_key: peer.vendor, cwd: peer.workspace,
            command: peer.wrapped ? buildAgentPaneCommand(endpoint.shell, [peer.commandLine]) : peer.command,
            cols: 100, rows: 30, create_generation: `${peer.pane}-generation`,
            metadata: { workspace_path: peer.workspace, session_marker: `at-pane:${peer.pane}`,
              ...(peer.vendor === 'claude' ? { explicit_session_id: peer.pane } : {}) },
          })
          record(`${label}_ack_ms`, start)
          const tid = created.terminal_session_id
          await until(() => (output.get(tid) ?? '').includes('BASELINE_READY '), `child READY deadline (${peer.vendor})`)
          record(`${label}_ready_ms`, start)
          const observed = JSON.parse(output.get(tid)!.split('BASELINE_READY ')[1].split('\r')[0])
          expect(observed.argv).toEqual(peer.argv)
          expect(observed.cwd).toBe(peer.workspace)
          expect(observed.home).toBe(endpoint.home)
          expect(observed.providerKeys).toEqual([])
          expect(observed.echoDisabled).toBe(true)
          if (peer.vendor === 'claude') {
            expect(observed.injected.mcp).toBe(true)
            expect(typeof observed.injected.skills).toBe('boolean')
            if (!observed.injected.skills) baselineWarnings.push(`claude skills injection missing: ${peer.pane}`)
          } else expect(observed.injected).toEqual(peer.vendor === 'opencode' ? { mcp: true, push: true } : {})
          expect(observed.pid).toBeGreaterThan(0)
          // Independent literals from baseline_peer.observe(), not inferred from WS data.
          const sessionId = peer.vendor === 'opencode' ? `ses_${peer.pane}` : peer.pane
          if (peer.vendor === 'claude') identifiedSessions.set(peer.pane, sessionId) // Existing explicit-session pin.
          return { ...peer, tid, pid: observed.pid, sessionId }
        }
        mark('shell_startup')
        for (const [index, shell] of (endpoint.shells as Peer[]).entries()) {
          const peer = await create(shell, index === 0 ? 'first_shell' : 'warm_shell')
          await request('terminal.kill', { terminal_session_id: peer.tid })
        }
        mark('agent_startup')
        peers = await Promise.all((endpoint.peers as Peer[]).map((peer) => create(peer, 'agent')))
        mark('live_idle')
        await delay(workload.idleMs)
        async function ack(peer: Running, text: string, label: string): Promise<void> {
          const start = performance.now()
          await request('terminal.input', { terminal_session_id: peer.tid, data: `${text}\r` })
          const expected = `BASELINE_ACK ${JSON.stringify({ pid: peer.pid, text })}`
          await until(() => (output.get(peer.tid) ?? '').includes(expected), 'exact child ACK deadline')
          record(label, start)
        }
        mark('input_output')
        await Promise.all(peers.map(async (peer) => {
          for (let round = 0; round < workload.ioRounds; round++) await ack(peer, `${round}:${workload.input}`, 'io_ms')
        }))
        mark('burst')
        await Promise.all(peers.map(async (peer) => {
          const start = performance.now()
          await request('terminal.input', { terminal_session_id: peer.tid, data: 'burst\r' })
          await until(() => (output.get(peer.tid) ?? '').includes('BASELINE_BURST 0000 '), 'first burst frame deadline')
          await ack(peer, 'during-burst', 'burst_input_ms')
          await until(() => (output.get(peer.tid) ?? '').includes('BASELINE_BURST_DONE'), 'burst completion deadline')
          record('burst_ms', start)
          const text = output.get(peer.tid)!
          let previous = -1
          for (let line = 0; line < workload.burstLines; line++) {
            const expected = `BASELINE_BURST ${String(line).padStart(4, '0')} ${'x'.repeat(workload.burstWidth)}`
            const position = text.indexOf(expected)
            expect(position).toBeGreaterThan(previous)
            expect(text.indexOf(expected, position + 1)).toBe(-1)
            previous = position
          }
        }))
        mark('observation')
        await Promise.all(peers.map(async (peer) => {
          await request('terminal.resize', { terminal_session_id: peer.tid, cols: 120, rows: 40 })
          await request('terminal.input', { terminal_session_id: peer.tid, data: 'observe-initial\r' })
          await until(() => (output.get(peer.tid) ?? '').includes('BASELINE_INITIAL_WRITE'), 'initial fixture write deadline')
          if (closeWalDiagnostic && peer.vendor === 'opencode') await request('terminal.input', { terminal_session_id: peer.tid, data: 'close-store\r' })
          if (peer.vendor === 'opencode') {
            const start = performance.now()
            try {
              await until(() => events.some((event) => matchesSessionDetection(event, peer)), 'exact native session detection deadline')
              identifiedSessions.set(peer.pane, peer.sessionId)
              record('session_detection_ms', start)
              observationOutcomes.push({ pane: peer.pane, vendor: peer.vendor, kind: 'session_detection', status: 'passed', latencyMs: performance.now() - start })
            } catch (error) {
              observationFailure(peer, 'session_detection', error)
            }
          }
          await delay(workload.observationSettleMs)
          const start = performance.now()
          await request('terminal.input', { terminal_session_id: peer.tid, data: 'observe\r' })
          if (closeWalDiagnostic && peer.vendor === 'opencode') await request('terminal.input', { terminal_session_id: peer.tid, data: 'close-store\r' })
          try {
            await until(() => events.some((event) => event.type === 'agent.activity' && event.payload.pane_id === peer.pane && event.payload.event_type === 'turn_complete'), 'attributed observation deadline')
            record('observation_ms', start)
            const completion = events.find((event) => event.type === 'agent.activity' && event.payload.pane_id === peer.pane && event.payload.event_type === 'turn_complete')!
            expect(completion.payload.text).toBe(workload.reply)
            expect(completion.payload.vendor).toBe(peer.vendor)
            expect(completion.payload.workspace_path).toBe(peer.workspace)
            if (peer.vendor === 'opencode') {
              expect(events.some((event) => matchesSessionDetection(event, peer))).toBe(true)
              identifiedSessions.set(peer.pane, peer.sessionId)
            }
            observationOutcomes.push({ pane: peer.pane, vendor: peer.vendor, kind: 'activity', status: 'passed', latencyMs: performance.now() - start })
          } catch (error) {
            observationFailure(peer, 'activity', error)
          }
        }))
        type Snapshot = { global: { all_time: { input: number; output: number } }; workspace: { cumulative: { totals: { input: number; output: number } }; live_by_session: Record<string, { input: number; output: number }> } }
        await Promise.all(peers.map(async (peer) => {
          try {
            const deadline = performance.now() + workload.deadlineMs
            let ready = false
            while (!ready && performance.now() < deadline) {
              const snapshot = await request<Snapshot>('tokens.snapshot', { workspace_path: peer.workspace })
              tokenSnapshots[peer.pane] = snapshot
              ready = hasExactLiveTotals(snapshot.workspace.live_by_session, identifiedSessions.get(peer.pane))
              if (!ready) await delay(20)
            }
            expect(ready, 'identified native session entry never reached the observed log totals').toBe(true)
            observationOutcomes.push({ pane: peer.pane, vendor: peer.vendor, kind: 'live_totals', status: 'passed' })
          } catch (error) {
            observationFailure(peer, 'live_totals', error)
          }
        }))
        mark('reconnect')
        const reconnectStart = performance.now()
        client.reconnectNow('baseline transport loss')
        await until(() => connections === 2, 'reconnect deadline')
        const ids = peers.map((peer) => peer.tid)
        const attached = await request<{ alive: string[]; dead: string[] }>('terminal.reattach', { terminal_session_ids: ids })
        expect([...attached.alive].sort()).toEqual([...ids].sort())
        expect(attached.dead).toEqual([])
        await Promise.all(peers.map((peer) => ack(peer, 'after-reconnect', 'reconnect_io_ms')))
        record('reconnect_recovery_ms', reconnectStart)
        mark('kill')
        const killStart = performance.now()
        await Promise.all(peers.map((peer) => request('terminal.kill', { terminal_session_id: peer.tid })))
        let dead: string[] = []
        const killDeadline = performance.now() + workload.deadlineMs
        while (dead.length !== ids.length && performance.now() < killDeadline) {
          dead = (await request<{ dead: string[] }>('terminal.reattach', { terminal_session_ids: ids })).dead
          if (dead.length !== ids.length) await delay(20)
        }
        expect([...dead].sort()).toEqual([...ids].sort())
        record('kill_reap_ms', killStart)
        mark('persistence_restart')
        const restartStart = performance.now()
        driver.stdin.write('restart\n')
        await until(() => existsSync(join(root, 'restarted.json')), 'durable backend restart deadline', 35_000)
        client.connect(JSON.parse(readFileSync(join(root, 'restarted.json'), 'utf8')).url)
        await until(() => connections === 3, 'restarted backend WS authentication deadline')
        for (const peer of peers) {
          const snapshot = await request<Snapshot>('tokens.snapshot', { workspace_path: peer.workspace })
          tokenSnapshots[`restart-${peer.pane}`] = snapshot
          expect([snapshot.global.all_time.input, snapshot.global.all_time.output]).toEqual([12 * count, 3 * count])
          expect([snapshot.workspace.cumulative.totals.input, snapshot.workspace.cumulative.totals.output]).toEqual([12, 3])
        }
        record('persistence_restart_ms', restartStart)
        mark('finished')
        passed = observationFailures.length === 0
        if (!passed) failure = observationFailures.map((item) => `${item.vendor}/${item.kind}: ${item.message}`).join('; ')
      } catch (error) {
        failure = redactDiagnostic(error instanceof Error ? error.message : String(error))
        phaseOutcomes[phases.at(-1)!.name] = 'failed'
        failures.push(`${count}/${repeat}: ${failure}`)
      } finally {
        client.dispose('baseline finished')
        driver.stdin.end('\n')
        try {
          await until(() => exitCode !== undefined, 'driver shutdown deadline', 30_000)
          expect(exitCode, log).toBe(0)
          phaseOutcomes.cleanup = 'passed'
        } catch (error) {
          passed = false
          const detail = redactDiagnostic(error instanceof Error ? error.message : String(error))
          phaseOutcomes.cleanup = 'failed'
          failure = `${failure ?? ''}; cleanup: ${detail}`
          failures.push(`${count}/${repeat}: cleanup: ${detail}`)
        } finally {
          try {
            const identity = JSON.parse(readFileSync(join(root, 'first-identity.json'), 'utf8'))
            const samples = readFileSync(join(root, 'resources.jsonl'), 'utf8').trim().split('\n').map((line): Sample => JSON.parse(line))
            expect(samples.length).toBeGreaterThan(0)
            expect(samples.some((sample) => sample.processes.some((process) => process.pid === identity.pid && process.created === identity.created))).toBe(true)
            expect(samples.every((sample) => sample.generation === 'first' && sample.backend.pid === identity.pid && sample.backend.created === identity.created)).toBe(true)
            for (const peer of peers) expect(samples.some((sample) => sample.processes.some((process) => process.pid === peer.pid && process.category === 'controlled_child'))).toBe(true)
            const ioStart = phases.find((phase) => phase.name === 'input_output')?.wallMs
            const ioStop = phases.find((phase) => phase.name === 'burst')?.wallMs
            activeSamples = samples.filter((sample) => ioStart !== undefined && ioStop !== undefined && ioStart <= sample.wallMs && sample.wallMs < ioStop).length
            if (phaseOutcomes.input_output === 'passed') expect(activeSamples, 'active I/O requires at least 10 actual CPU/RSS samples').toBeGreaterThanOrEqual(10)
            evidenceValid = phaseOutcomes.cleanup === 'passed'
          } catch (error) {
            passed = false
            const detail = redactDiagnostic(error instanceof Error ? error.message : String(error))
            failure = `${failure ?? ''}; resource proof: ${detail}`
            failures.push(`${count}/${repeat}: resource proof: ${detail}`)
          }
          const prefix = join(reports, `${measuring ? 'trial' : 'check'}-${count}-${repeat}`)
          const receipt = { count, repeat, measuring, diagnosticTrace: process.env.ACR_DIAGNOSE === '1', closeWalDiagnostic, passed, failure, evidenceValid, activeSamples, peerIdentities: peers.map(({ vendor, pane, tid, pid, sessionId }) => ({ vendor, pane, tid, pid, fixtureSessionId: sessionId, identifiedSessionId: identifiedSessions.get(pane) ?? null })),  baselineWarnings, latencies, phases, phaseOutcomes, observationFailures, observationOutcomes, tokenSnapshots, events, timeline, output: Object.fromEntries(output), exitCode }
          const rescueError = saveTrialArtifacts(root, prefix, receipt, log, exitCode === undefined && driver.pid ? () => {
            execFileSync(python, ['-m', 'tests.cli_regression.support.runtime_baseline', '--rescue', root, String(driver.pid)], { cwd: resolve('backend'), timeout: 15_000 })
          } : undefined)
          if (rescueError) failures.push(`${count}/${repeat}: ${rescueError}`)
          if (exitCode !== undefined) rmSync(root, { recursive: true, force: true })
        }
      }
    }
  }
  expect(failures, 'invalid trials are retained, never counted as passing workloads').toEqual([])
}, 600_000)
