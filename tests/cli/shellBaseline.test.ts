import { execFileSync, spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { buildAgentPaneCommand } from '../../src/main/plugins/agentPaneCommand'
import { redactDiagnostic, saveTrialArtifacts } from './baselineArtifacts'

const workload = JSON.parse(readFileSync(resolve('tests/fixtures/cli-regression/runtime-workload.json'), 'utf8'))
const python = resolve('backend/.venv/bin/python')
const measuring = process.env.ACR_MEASURE === '1'
const mode = process.env.ACR_SHELL_RUNTIME ?? 'python'
type Peer = { pane: string; vendor: string; workspace: string; command: string[]; commandLine: string; wrapped: boolean; argv: string[] }
type Running = Peer & { tid: string; pid: number }

async function until(predicate: () => boolean, message: string, timeout = workload.deadlineMs): Promise<void> {
  const deadline = performance.now() + timeout
  while (!predicate()) {
    if (performance.now() >= deadline) throw new Error(message)
    await delay(5)
  }
}

it.runIf(process.platform === 'darwin' && process.env.ACR_SHELL_BASELINE_RUN === '1')('runs the separately frozen shell method 7 once for 1/4 sessions', async () => {
  expect(['python', 'rust-shell']).toContain(mode)
  expect(workload.sessionCounts).toEqual([1, 4])
  const reports = resolve('test-results/ci/shell-baseline', measuring ? 'measured' : 'check', mode)
  mkdirSync(reports, { recursive: true })
  const failures: string[] = []
  const matrixStart = performance.now()
  for (const count of workload.sessionCounts) {
    expect(performance.now() - matrixStart).toBeLessThan(300_000)
    const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-shell-method7-')))
    const started = performance.now()
    const driver = spawn(python, ['-m', 'tests.cli_regression.support.shell_baseline', root, String(count), mode], {
      cwd: resolve('backend'), stdio: ['pipe', 'pipe', 'pipe'],
    })
    let log = ''
    let code: number | null | undefined
    driver.stdout.on('data', (data) => { log += data })
    driver.stderr.on('data', (data) => { log += data })
    driver.on('error', (error) => { log += String(error); code = -1 })
    driver.on('exit', (value) => { code = value })
    let connections = 0
    const client = createWsClient({ onStatus: (status) => { if (status === 'connected') connections++ } })
    const output = new Map<string, string>()
    const decoders = new Map<string, TextDecoder>()
    const events: unknown[] = []
    const timeline: unknown[] = []
    client.on('terminal.output', (payload) => {
      const frame = payload as TerminalOutputFrame
      const decoder = decoders.get(frame.terminal_session_id) ?? new TextDecoder()
      decoders.set(frame.terminal_session_id, decoder)
      const text = decoder.decode(frame.data, { stream: true })
      output.set(frame.terminal_session_id, (output.get(frame.terminal_session_id) ?? '') + text)
      timeline.push({ type: 'terminal.output', tid: frame.terminal_session_id, wallMs: Date.now(), length: text.length })
    })
    client.on('terminal.exit', (payload) => { events.push({ type: 'terminal.exit', payload, wallMs: Date.now() }) })
    const phases: Array<{ name: string; wallMs: number }> = [{ name: 'cold_startup', wallMs: Date.now() }]
    const phaseOutcomes: Record<string, string> = { cold_startup: 'running' }
    const latencies: Record<string, number[]> = {}
    const peers: Running[] = []
    let passed = false
    let failure: string | null = null
    let activeSamples = 0
    const mark = (name: string): void => {
      phaseOutcomes[phases.at(-1)!.name] = 'passed'
      phases.push({ name, wallMs: Date.now() })
      phaseOutcomes[name] = name === 'finished' ? 'passed' : 'running'
    }
    const record = (name: string, start: number): void => { (latencies[name] ??= []).push(performance.now() - start) }
    async function request<T = Record<string, unknown>>(type: string, payload: Record<string, unknown>): Promise<T> {
      timeline.push({ type, tid: payload.terminal_session_id, wallMs: Date.now() })
      const response = await client.send<T>(type, payload, workload.deadlineMs)
      expect(response.ok, JSON.stringify(response)).toBe(true)
      return response.payload!
    }
    try {
      await until(() => {
        if (code !== undefined) throw new Error(log)
        return existsSync(join(root, 'endpoint.json'))
      }, 'backend readiness deadline', 35_000)
      const endpoint = JSON.parse(readFileSync(join(root, 'endpoint.json'), 'utf8'))
      client.connect(endpoint.url)
      await until(() => connections === 1, 'authenticated connection deadline')
      record('cold_backend_ready_ms', started)
      mark('empty_idle')
      await delay(workload.idleMs)
      async function create(peer: Peer, label: string): Promise<Running> {
        const start = performance.now()
        const created = await request<{ terminal_session_id: string; pid: number }>('terminal.create', {
          pane_id: peer.pane, agent_key: 'terminal', cwd: peer.workspace,
          command: peer.wrapped ? buildAgentPaneCommand(endpoint.shell, [peer.commandLine]) : peer.command,
          cols: 100, rows: 30, create_generation: `${peer.pane}-generation`, metadata: { workspace_path: peer.workspace },
        })
        record(`${label}_ack_ms`, start)
        const tid = created.terminal_session_id
        await until(() => (output.get(tid) ?? '').includes('BASELINE_READY '), 'child READY deadline')
        record(`${label}_ready_ms`, start)
        const observed = JSON.parse(output.get(tid)!.split('BASELINE_READY ')[1].split('\r')[0])
        expect(observed.argv).toEqual(peer.argv)
        expect(observed.cwd).toBe(peer.workspace)
        expect(observed.home).toBe(endpoint.home)
        expect(observed.providerKeys).toEqual([])
        expect(observed.injected).toEqual({})
        expect(observed.echoDisabled).toBe(true)
        return { ...peer, tid, pid: observed.pid }
      }
      mark('shell_startup')
      for (const [index, shell] of (endpoint.shells as Peer[]).entries()) {
        const peer = await create(shell, index === 0 ? 'first_shell' : 'warm_shell')
        await request('terminal.kill', { terminal_session_id: peer.tid })
      }
      mark('active_shell_startup')
      peers.push(...await Promise.all((endpoint.peers as Peer[]).map((peer) => create(peer, 'shell'))))
      mark('live_idle')
      await delay(workload.idleMs)
      async function ack(peer: Running, text: string, label: string): Promise<void> {
        const start = performance.now()
        await request('terminal.input', { terminal_session_id: peer.tid, data: `${text}\r` })
        await until(() => (output.get(peer.tid) ?? '').includes(`BASELINE_ACK ${JSON.stringify({ pid: peer.pid, text })}`), 'exact child ACK deadline')
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
        await until(() => (output.get(peer.tid) ?? '').includes('BASELINE_BURST 0000 '), 'first burst deadline')
        await ack(peer, 'during-burst', 'burst_input_ms')
        await until(() => (output.get(peer.tid) ?? '').includes('BASELINE_BURST_DONE'), 'burst completion deadline')
        record('burst_ms', start)
        let previous = -1
        for (let index = 0; index < workload.burstLines; index++) {
          const expected = `BASELINE_BURST ${String(index).padStart(4, '0')} ${'x'.repeat(workload.burstWidth)}`
          const text = output.get(peer.tid)!
          const position = text.indexOf(expected)
          expect(position).toBeGreaterThan(previous)
          expect(text.indexOf(expected, position + 1)).toBe(-1)
          previous = position
        }
      }))
      mark('reconnect')
      const reconnect = performance.now()
      client.reconnectNow('method7 transport loss')
      await until(() => connections === 2, 'reconnect deadline')
      const ids = peers.map((peer) => peer.tid)
      const attached = await request<{ alive: string[]; dead: string[] }>('terminal.reattach', { terminal_session_ids: ids })
      expect(attached.alive.sort()).toEqual([...ids].sort())
      expect(attached.dead).toEqual([])
      await Promise.all(peers.map((peer) => ack(peer, 'after-reconnect', 'reconnect_io_ms')))
      record('reconnect_recovery_ms', reconnect)
      mark('kill')
      const kill = performance.now()
      await Promise.all(peers.map((peer) => request('terminal.kill', { terminal_session_id: peer.tid })))
      expect((await request<{ dead: string[] }>('terminal.reattach', { terminal_session_ids: ids })).dead.sort()).toEqual([...ids].sort())
      record('kill_reap_ms', kill)
      mark('empty_started_idle')
      await delay(workload.idleMs)
      mark('finished')
      const samples = readFileSync(join(root, 'resources.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
      activeSamples = samples.filter((sample) => sample.wallMs >= phases.find((phase) => phase.name === 'input_output')!.wallMs &&
        sample.wallMs < phases.find((phase) => phase.name === 'burst')!.wallMs).length
      expect(activeSamples).toBeGreaterThanOrEqual(10)
      const receiptPath = join(root, 'runtime-receipts.jsonl')
      if (mode === 'rust-shell') {
        const receipts = readFileSync(receiptPath, 'utf8').trim().split('\n').map((line) => JSON.parse(line))
        const ready = receipts.filter((entry) => entry.event === 'ready')
        expect(ready).toHaveLength(1)
        expect(ready[0].executableSha256).toBe(createHash('sha256').update(readFileSync(ready[0].executable)).digest('hex'))
        expect(ready[0].owner_pid).toBe(JSON.parse(readFileSync(join(root, 'identity.json'), 'utf8')).backend.pid)
        const sidecars = samples.flatMap((sample) => sample.processes.filter((entry: any) => entry.component === 'sidecar'))
        expect(new Set(sidecars.map((entry: any) => entry.pid))).toEqual(new Set([ready[0].pid]))
        expect(receipts.filter((entry) => entry.event === 'owned')).toHaveLength(count + 2)
      } else expect(existsSync(receiptPath)).toBe(false)
      passed = true
    } catch (error) {
      failure = redactDiagnostic(String(error))
      phaseOutcomes[phases.at(-1)!.name] = failure.includes('deadline') ? 'censored' : 'failed'
      failures.push(`${mode}/${count}: ${failure}`)
    } finally {
      client.dispose('method7 finished')
      driver.stdin.end('\n')
      try { await until(() => code !== undefined, 'driver shutdown deadline', 25_000) } catch (error) { passed = false; failure = `${failure ?? ''}; ${error}` }
      if (code !== 0) { passed = false; failure = `${failure ?? ''}; driver exit=${code}; ${log}` }
      const prefix = join(reports, `sessions-${count}`)
      const receipt = { method: 7, measuring, selectedRuntime: mode, count, passed, failure, phases, phaseOutcomes,
        latencies, activeSamples, peers, events, timeline, output: Object.fromEntries(output) }
      saveTrialArtifacts(root, prefix, receipt, log, code === undefined ? () => {
        execFileSync(python, ['-m', 'tests.cli_regression.support.shell_baseline', '--rescue', root, String(driver.pid)], { cwd: resolve('backend'), timeout: 10_000 })
      } : undefined)
      if (existsSync(join(root, 'runtime-receipts.jsonl'))) writeFileSync(`${prefix}-runtime-receipts.jsonl`, redactDiagnostic(readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8')))
      if (!passed && !failures.some((entry) => entry.startsWith(`${mode}/${count}:`))) failures.push(`${mode}/${count}: ${failure}`)
      rmSync(root, { recursive: true, force: true })
    }
  }
  expect(performance.now() - matrixStart).toBeLessThan(300_000)
  expect(failures).toEqual([])
}, 300_000)
