import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { redactDiagnostic } from './baselineArtifacts'
import { until, withDriver, type Driver } from './recoveryDriver'
import { buildAgentPaneCommand, quoteCommandWord } from '../../src/main/plugins/agentPaneCommand'
import { SPEC } from '../../src/renderer/src/platform/plugin-shell/agents/claude'
import { modelArgsFor } from '../../src/renderer/src/platform/plugin-shell/lib/cliModel'

// Ticket 05 principal seam: production TS WS client -> authenticated Python
// handlers and stores -> the actual sidecar -> native POSIX PTY. A Python driver
// owns the disposable backend(s) and performs OS-level fault injection on its
// OWN processes only (each verified by pid + creation time); it never reads or
// signals anything else. Windows native execution is pending E5.

interface Pane { tid: string; ready: any }
class Client {
  readonly client = createWsClient({ onStatus: (status) => { if (status === 'connected') this.connections++ } })
  connections = 0
  output = new Map<string, string>()
  exits = new Map<string, any[]>()
  private decoders = new Map<string, TextDecoder>()
  constructor(private url: string) {
    this.client.on('terminal.output', (payload) => {
      const frame = payload as TerminalOutputFrame
      const decoder = this.decoders.get(frame.terminal_session_id) ?? new TextDecoder()
      this.decoders.set(frame.terminal_session_id, decoder)
      this.output.set(frame.terminal_session_id, (this.output.get(frame.terminal_session_id) ?? '') + decoder.decode(frame.data, { stream: true }))
    })
    this.client.on('terminal.exit', (payload) => {
      const exit = payload as { terminal_session_id: string }
      this.exits.set(exit.terminal_session_id, [...(this.exits.get(exit.terminal_session_id) ?? []), exit])
    })
  }
  async connect(): Promise<void> {
    this.client.connect(this.url)
    await until(() => this.connections >= 1, 'authenticated WS')
  }
  async shell(wire: any, pane: string, generation: string, timeout = 20_000, env?: Record<string, string>): Promise<Pane> {
    const created = await this.client.send<{ terminal_session_id: string }>('terminal.create', {
      pane_id: pane, agent_key: 'terminal', command: wire.command, cwd: wire.workspace,
      cols: 100, rows: 30, create_generation: generation, ...(env ? { env } : {}),
    }, timeout)
    expect(created.ok, JSON.stringify(created)).toBe(true)
    const tid = created.payload!.terminal_session_id
    await until(() => (this.output.get(tid) ?? '').includes('NATIVE_READY '), 'native child READY')
    return { tid, ready: JSON.parse(this.output.get(tid)!.split('NATIVE_READY ')[1].split('\r\n')[0]) }
  }
  input(tid: string, data: string, timeout?: number) {
    return this.client.send('terminal.input', { terminal_session_id: tid, data }, timeout)
  }
  async tree(tid: string): Promise<number[]> {
    expect((await this.input(tid, 'spawn-tree-hup\r')).ok).toBe(true)
    await until(() => (this.output.get(tid) ?? '').includes('TREE {'), 'tree spawned')
    return JSON.parse(this.output.get(tid)!.split('TREE ')[1].split('\r\n')[0]).pids
  }
  reattach(tids: string[]) {
    return this.client.send<{ alive: string[]; dead: string[] }>('terminal.reattach', { terminal_session_ids: tids, cols: 100, rows: 30 })
  }
  dispose(reason: string): void { this.client.dispose(reason) }
}

const aliveFlags = (driver: Driver, identities: Record<string, number>) =>
  driver.ctl<Record<string, boolean>>('alive', Object.entries(identities).map(([pid, created]) => `${pid}:${created}`).join(','))

async function waitDescendantSnapshot(driver: Driver): Promise<void> {
  // Ownership descendants reach the durable registry on the sidecar's periodic snapshot.
  await until(async () => Object.values(await driver.ctl<Record<string, number>>('descendants')).some((n) => n >= 2),
    'descendant snapshot persisted', 40_000)
}

it.runIf(process.platform === 'darwin' || process.platform === 'linux')('sidecar death with sessions and a pending request fails visibly, cleans owned trees only and recovers on a fresh runtime', async () => {
  await withDriver('runtime', async (driver) => {
    const wire = driver.wire
    const main = new Client(wire.url)
    const other = new Client(wire.other.url)
    try {
      await main.connect()
      await other.connect()
      const survivor = await other.shell(wire.other, 'other-runtime-pane', 'other-1')
      const panes: Pane[] = []
      for (let index = 0; index < 3; index++) panes.push(await main.shell(wire, `lost-${index}`, `lost-create-${index}`))
      const tids = panes.map((pane) => pane.tid)
      const tree = await main.tree(tids[0])

      // Transport loss alone keeps every live PTY (same child, ordered I/O).
      main.client.reconnectNow('actual transport loss')
      await until(() => main.connections === 2, 'second authenticated connection')
      const attached = await main.reattach(tids)
      expect(attached.payload!.alive.sort()).toEqual([...tids].sort())
      expect(attached.payload!.dead).toEqual([])
      expect((await main.input(tids[1], 'after-transport-loss\r')).ok).toBe(true)
      await until(() => (main.output.get(tids[1]) ?? '').includes(`"pid": ${panes[1].ready.pid}, "text": "after-transport-loss"`), 'same child after transport loss')

      const identities = await driver.ctl<Record<string, number>>('identify', [panes[0].ready.pid, ...tree].join(','))
      await waitDescendantSnapshot(driver)
      const survivorTree = await other.tree(survivor.tid)
      const survivorIdentities = await driver.ctl<Record<string, number>>('identify', [survivor.ready.pid, ...survivorTree].join(','))
      const ownedBefore = driver.receipts().filter((r) => r.event === 'owned').length

      // A real outstanding request, then the sidecar dies under a live backend.
      await driver.ctl('sidecar-stop')
      // Terminal input is a local write to the handed-off PTY and does not wait for the sidecar (E1 8);
      // the outstanding sidecar request is a control message, here a resize.
      const pending = main.client.send('terminal.resize', { terminal_session_id: tids[1], cols: 110, rows: 40 }, 30_000)
      await delay(300)
      await driver.ctl('sidecar-kill')
      const failed = await pending
      expect(failed.ok).toBe(false)
      expect(JSON.stringify(failed)).toContain('RUNTIME_LOST')

      await until(() => tids.every((tid) => main.exits.has(tid)), 'a terminal.exit for every lost session', 45_000)
      for (const tid of tids) {
        expect(main.exits.get(tid)).toHaveLength(1)
        expect(main.exits.get(tid)![0]).toMatchObject({ reason: 'runtime_lost', exit_code: null, signal: null })
      }
      const lost = await main.reattach(tids)
      expect(lost.payload!.alive).toEqual([])
      expect(lost.payload!.dead.sort()).toEqual([...tids].sort())
      expect((await main.input(tids[1], 'stale-input\r')).ok).toBe(false)

      // Owned tree gone; unrelated process and the other runtime untouched.
      expect(Object.values(await aliveFlags(driver, identities))).toEqual(Object.values(identities).map(() => false))
      expect(Object.values(await aliveFlags(driver, { [wire.unrelated.pid]: wire.unrelated.created })).every(Boolean)).toBe(true)
      expect(Object.values(await aliveFlags(driver, survivorIdentities)).every(Boolean)).toBe(true)
      expect((await other.input(survivor.tid, 'other-runtime-alive\r')).ok).toBe(true)
      await until(() => (other.output.get(survivor.tid) ?? '').includes('"text": "other-runtime-alive"'), 'other runtime still serves its session')
      expect(other.exits.size).toBe(0)

      // Nothing replays: no create is re-issued for the lost sessions.
      await delay(1000)
      expect(driver.receipts().filter((r) => r.event === 'owned')).toHaveLength(ownedBefore)

      // A fresh runtime starts only now, after cleanup.
      const fresh = await main.shell(wire, 'fresh-pane', 'fresh-create')
      expect(tids).not.toContain(fresh.tid)
      const receipts = driver.receipts()
      const ready = receipts.map((r, i) => (r.event === 'ready' ? i : -1)).filter((i) => i >= 0)
      const invalidated = receipts.findIndex((r) => r.event === 'runtime_invalidated')
      const freshOwned = receipts.findIndex((r) => r.event === 'owned' && r.session_id === fresh.tid)
      expect(ready).toHaveLength(2)
      expect(ready[0]).toBeLessThan(invalidated)
      expect(invalidated).toBeLessThan(ready[1])
      expect(ready[1]).toBeLessThan(freshOwned)
      expect(receipts[invalidated]).toMatchObject({ complete: true, sessions: 3, unresolved: 0, unverifiable: false })
      expect(receipts[ready[0]].runtime_generation).not.toBe(receipts[ready[1]].runtime_generation)
      expect(receipts[ready[0]].executableSha256).toBe(receipts[ready[1]].executableSha256)
      // The replacement child for the pane whose write was pending never receives that failed input.
      const replacement = await main.shell(wire, 'lost-1', 'lost-replacement')
      expect((await main.input(replacement.tid, 'probe-after-recovery\r')).ok).toBe(true)
      await until(() => (main.output.get(replacement.tid) ?? '').includes('"text": "probe-after-recovery"'), 'replacement child answers its probe')
      const received = [...(main.output.get(replacement.tid) ?? '').matchAll(/ACK \{"pid": \d+, "text": "([^"]*)"\}/g)].map((m) => m[1])
      expect(received).toEqual(['probe-after-recovery']) // neither 'pending-input' nor 'stale-input'
      expect((await main.client.send('terminal.kill', { terminal_session_id: replacement.tid, force: true })).ok).toBe(true)
      expect(receipts[ready[1]].owner_pid).toBe(wire.backend.pid)
      expect((await main.client.send('terminal.kill', { terminal_session_id: fresh.tid, force: true })).ok).toBe(true)
      expect((await other.client.send('terminal.kill', { terminal_session_id: survivor.tid, force: true })).ok).toBe(true)
    } finally {
      main.dispose('runtime loss complete')
      other.dispose('runtime loss complete')
    }
  })
}, 180_000)

for (const [kind, expected] of [['not-executable', 'START_FAILED at exec'], ['protocol-mismatch', 'PROTOCOL_MISMATCH at handshake'], ['exits-before-ready', 'START_FAILED before ready']]) {
  it.runIf(process.platform === 'darwin')(`replacement runtime failure after loss (${kind}) is explicit and never falls back`, async () => {
    await withDriver('runtime', async (driver) => {
      const client = new Client(driver.wire.url)
      try {
        await client.connect()
        const lost = await client.shell(driver.wire, 'pane-lost', 'lost-create')
        await driver.ctl('sidecar-kill')
        await until(() => client.exits.has(lost.tid), 'lost session presented', 45_000)
        await until(() => driver.receipts().some((r) => r.event === 'runtime_invalidated' && r.complete), 'cleanup verified', 30_000)
        await driver.ctl('replace-runtime', kind)
        const response = await client.client.send('terminal.create', {
          pane_id: 'pane-replacement', agent_key: 'terminal', command: driver.wire.command, cwd: driver.wire.workspace,
          create_generation: `replacement-${kind}`,
        }, 20_000)
        expect(response.ok).toBe(false)
        expect(JSON.stringify(response)).toContain(expected)
        const before = [...client.output.keys()]
        const receipts = driver.receipts()
        expect(receipts.filter((r) => r.event === 'owned')).toHaveLength(1) // no replacement child, no Python PTY
        expect([...client.output.keys()]).toEqual(before)
        const again = await client.client.send('terminal.create', {
          pane_id: 'pane-again', agent_key: 'terminal', command: driver.wire.command, cwd: driver.wire.workspace,
          create_generation: `again-${kind}`,
        }, 20_000)
        expect(again.ok).toBe(false) // the failure stays explicit, not retried into success
      } finally {
        client.dispose('replacement failure complete')
      }
    })
  }, 120_000)
}

it.runIf(process.platform === 'darwin')('backend death is distinct: the sidecar and its trees go with it and a restarted backend answers dead', async () => {
  await withDriver('backend-death', async (driver) => {
    const first = new Client(driver.wire.url)
    let tids: string[] = []
    try {
      await first.connect()
      const panes = [await first.shell(driver.wire, 'death-0', 'death-create-0'), await first.shell(driver.wire, 'death-1', 'death-create-1')]
      tids = panes.map((pane) => pane.tid)
      await first.tree(tids[0])
      await waitDescendantSnapshot(driver)
      const report = await driver.ctl<any>('backend-kill')
      expect(report).toMatchObject({ sidecarGone: true, ownedGone: true, harnessRescued: 0, unrelatedAlive: true })
      expect(report.registryReapedAfterDeath).toEqual([])
      expect(report.ownedCount).toBeGreaterThanOrEqual(3)
    } finally {
      first.dispose('backend killed')
    }
    driver.stop()
    const wire2 = await driver.waitFile('wire-2.json', 90_000)
    const second = new Client(wire2.url)
    try {
      await second.connect()
      const answer = await second.reattach(tids)
      expect(answer.payload!.alive).toEqual([])
      expect(answer.payload!.dead.sort()).toEqual([...tids].sort())
      const fresh = await second.shell(wire2, 'after-restart', 'after-restart-create')
      expect(tids).not.toContain(fresh.tid)
      expect((await second.client.send('terminal.kill', { terminal_session_id: fresh.tid, force: true })).ok).toBe(true)
    } finally {
      second.dispose('restart verified')
    }
  })
}, 240_000)

it.runIf(process.platform === 'darwin')('a create abandoned by a lost WebSocket is cleaned, never resurrected, and the pane replacement waits for it', async () => {
  await withDriver('runtime', async (driver) => {
    const wire = driver.wire
    const holder = new Client(wire.url)
    try {
      await holder.connect()
      const warm = await holder.shell(wire, 'warm-runtime', 'warm-create') // starts the one sidecar
      await driver.ctl('sidecar-stop')
      const abandoned = new Client(wire.url)
      await abandoned.connect()
      void abandoned.client.send('terminal.create', {
        pane_id: 'pane-race', agent_key: 'terminal', command: wire.command, cwd: wire.workspace,
        cols: 100, rows: 30, create_generation: 'race-1',
      }, 60_000).catch(() => undefined)
      await delay(700) // the request is on the wire to the stopped sidecar
      abandoned.dispose('connection lost mid-create') // cancels the backend's create handler task
      await delay(300)
      const replacement = new Client(wire.url)
      await replacement.connect()
      const replacing = replacement.shell(wire, 'pane-race', 'race-2', 60_000)
      await delay(700)
      expect(driver.receipts().filter((r) => r.event === 'owned' && r.pane_id === 'pane-race')).toHaveLength(0)
      await driver.ctl('sidecar-cont')
      const pane = await replacing
      const receipts = driver.receipts()
      const owned = receipts.filter((r) => r.event === 'owned' && r.pane_id === 'pane-race')
      const abandonedOwned = owned.filter((r) => r.session_id !== pane.tid)
      expect(owned.map((r) => r.session_id)).toContain(pane.tid)
      expect(new Set(owned.map((r) => r.session_id)).size).toBe(owned.length) // one create per session id
      for (const record of abandonedOwned) {
        const cleaned = receipts.findIndex((r) => r.event === 'cleaned' && r.session_id === record.session_id)
        const replacementOwned = receipts.findIndex((r) => r.event === 'owned' && r.session_id === pane.tid)
        expect(cleaned).toBeGreaterThan(-1)
        expect(cleaned).toBeLessThan(replacementOwned)
        expect(receipts[cleaned]).toMatchObject({ cleanup_complete: true, root_reaped: true })
        expect(receipts[cleaned].survivors).toEqual([])
        expect(receipts[cleaned].unverifiable).toEqual([])
      }
      const outcomes = receipts.filter((r) => r.event === 'abandoned_create')
      expect(outcomes).toHaveLength(1)
      expect(['cleaned', 'refused', 'prevented']).toContain(outcomes[0].outcome) // E1 10.1 adds `prevented`: a CANCEL that won the gate
      for (const client of [holder, replacement]) {
        for (const [tid, exits] of client.exits) expect(exits.length === 0 || tid === warm.tid).toBe(true)
      }
      expect((await replacement.client.send('terminal.kill', { terminal_session_id: pane.tid, force: true })).ok).toBe(true)
      replacement.dispose('abandoned create resolved')
      expect((await holder.client.send('terminal.kill', { terminal_session_id: warm.tid, force: true })).ok).toBe(true)
    } finally {
      holder.dispose('abandoned create complete')
    }
  })
}, 150_000)

it.runIf(process.platform === 'darwin')('an unanswered create hits the real 15 s request timeout: not a success, cleaned afterwards, no retry', async () => {
  await withDriver('runtime', async (driver) => {
    const wire = driver.wire
    const client = new Client(wire.url)
    try {
      await client.connect()
      const warm = await client.shell(wire, 'warm-runtime', 'warm-create')
      await driver.ctl('sidecar-stop')
      const started = Date.now()
      const response = await client.client.send('terminal.create', {
        pane_id: 'pane-timeout', agent_key: 'terminal', command: wire.command, cwd: wire.workspace,
        cols: 100, rows: 30, create_generation: 'timeout-1',
      }, 60_000)
      expect(response.ok).toBe(false)
      expect(JSON.stringify(response)).toContain('REQUEST_TIMEOUT')
      expect(Date.now() - started).toBeGreaterThanOrEqual(14_000)
      await driver.ctl('sidecar-cont')
      const replacement = await client.shell(wire, 'pane-timeout', 'timeout-2', 60_000)
      const receipts = driver.receipts()
      const owned = receipts.filter((r) => r.event === 'owned' && r.pane_id === 'pane-timeout')
      expect(new Set(owned.map((r) => r.session_id)).size).toBe(owned.length) // the failed create was not re-issued
      for (const record of owned.filter((r) => r.session_id !== replacement.tid)) {
        const cleaned = receipts.findIndex((r) => r.event === 'cleaned' && r.session_id === record.session_id)
        expect(cleaned).toBeGreaterThan(-1)
        expect(cleaned).toBeLessThan(receipts.findIndex((r) => r.event === 'owned' && r.session_id === replacement.tid))
      }
      expect(receipts.filter((r) => r.event === 'abandoned_create')).toHaveLength(1)
      expect(client.exits.size).toBe(0)
      expect((await client.client.send('terminal.kill', { terminal_session_id: replacement.tid, force: true })).ok).toBe(true)
      expect((await client.client.send('terminal.kill', { terminal_session_id: warm.tid, force: true })).ok).toBe(true)
    } finally {
      client.dispose('request timeout complete')
    }
  })
}, 150_000)

it.runIf(process.platform === 'darwin')('cancelling a replacement create while Python holds it at launch coordination prevents any spawn', async () => {
  await withDriver('runtime', async (driver) => {
    const wire = driver.wire
    const holder = new Client(wire.url)
    const first = new Client(wire.url)
    const second = new Client(wire.url)
    try {
      await holder.connect()
      const warm = await holder.shell(wire, 'warm-runtime', 'warm-create')
      const ownedBefore = driver.receipts().filter((r) => r.event === 'owned').length
      await driver.ctl('sidecar-stop')
      await first.connect()
      void first.client.send('terminal.create', {
        pane_id: 'pane-coordinated', agent_key: 'terminal', command: wire.command, cwd: wire.workspace,
        cols: 100, rows: 30, create_generation: 'coordinated-1',
      }, 60_000).catch(() => undefined)
      await delay(700) // dispatched to the stopped sidecar
      first.dispose('connection lost mid-create') // abandoned: Python now holds this pane at its coordination barrier
      await delay(300)
      await second.connect()
      const replacement = second.client.send('terminal.create', {
        pane_id: 'pane-coordinated', agent_key: 'terminal', command: wire.command, cwd: wire.workspace,
        cols: 100, rows: 30, create_generation: 'coordinated-2',
      }, 60_000)
      await delay(700) // the replacement waits inside Python, nothing is on the wire for it
      // The renderer's own cancellation message, while the launch is still being coordinated.
      const cancelled = await second.client.send('terminal.create.cancel', { pane_id: 'pane-coordinated', create_generation: 'coordinated-2' })
      expect(cancelled.ok).toBe(true)
      await driver.ctl('sidecar-cont')
      const response = await replacement
      expect(response.ok).toBe(false)
      expect(JSON.stringify(response)).toContain('CREATE_CANCELLED')
      await delay(1500)
      const receipts = driver.receipts()
      // Nothing was ever spawned for this pane: the abandoned original was prevented by the sidecar's
      // own cancel, and the cancelled replacement never reached the wire.
      expect(receipts.filter((r) => r.event === 'owned' && r.pane_id === 'pane-coordinated')).toEqual([])
      expect(receipts.filter((r) => r.event === 'owned')).toHaveLength(ownedBefore)
      expect(second.output.size).toBe(0)
      expect((await holder.client.send('terminal.kill', { terminal_session_id: warm.tid, force: true })).ok).toBe(true)
    } finally {
      for (const client of [holder, first, second]) client.dispose('coordination cancel complete')
    }
  })
}, 150_000)

// The same controlled vendor records and independent totals as claudeRuntime.test.ts
// (external vendor data only, never application state).
function claudeStages(root: string): number {
  const prompt = { type: 'user', promptId: 'prompt-1', message: { content: 'Implement the fixture' } }
  const question = { type: 'assistant', requestId: 'request-1', message: {
    id: 'msg-1', model: 'fixture-model', stop_reason: 'tool_use',
    content: [{ type: 'tool_use', name: 'AskUserQuestion', input: {} }],
    usage: { input_tokens: 10, cache_read_input_tokens: 2, cache_creation_input_tokens: 3, output_tokens: 4 },
  } }
  const background = { type: 'user', message: { content: [{ type: 'tool_result' }] }, toolUseResult: { backgroundTaskId: 'bg-1' } }
  const completion = { type: 'assistant', requestId: 'request-2', version: '99.0.0-regression', message: {
    id: 'msg-2', stop_reason: 'end_turn', content: [{ type: 'text', text: 'Reference completed café 世界' }],
    usage: { input_tokens: 7, output_tokens: 5 },
  } }
  const full = Buffer.from(JSON.stringify(completion))
  const split = Math.floor(full.length / 2)
  const prefix = Buffer.from([prompt, question, question, background].map((r) => JSON.stringify(r) + '\n').join('') + 'corrupt complete record\n')
  writeFileSync(join(root, 'stage-1.bin'), Buffer.concat([prefix, full.subarray(0, split)]))
  const notification = { type: 'queue-operation', operation: 'enqueue', content: '<task-notification><task-id>bg-1</task-id><task-id>bg-2</task-id><status>completed</status></task-notification>' }
  const monitor = { ...notification, content: '<task-notification><task-id>bg-1</task-id>monitor</task-notification>' }
  const stop = { type: 'assistant', message: { stop_reason: 'tool_use', content: [{ type: 'tool_use', name: 'TaskStop', input: { task_id: 'bg-3' } }] } }
  const duplicate = { ...completion, message: { ...completion.message, stop_reason: 'tool_use' } }
  writeFileSync(join(root, 'stage-2.bin'), Buffer.concat([full.subarray(split), Buffer.from('\n' + [monitor, notification, stop, duplicate].map((r) => JSON.stringify(r) + '\n').join(''))]))
  return prefix.length
}

it.runIf(process.platform === 'darwin')('runtime death with a held resize fence and an outstanding observation keeps the durable checkpoint unchanged, then observes once on a fresh runtime', async () => {
  await withDriver('claude', async (driver) => {
    const wire = driver.wire
    const firstOffset = claudeStages(driver.root)
    const client = new Client(wire.url)
    const activity: any[] = []
    client.client.on('agent.activity', (payload) => activity.push(payload))
    const request = async <T = any>(type: string, payload: Record<string, unknown>): Promise<T> => {
      const response = await client.client.send<T>(type, payload, 20_000)
      expect(response.ok, JSON.stringify(response)).toBe(true)
      return response.payload!
    }
    const totals = async () => {
      const snapshot = await request('tokens.snapshot', { workspace_path: wire.workspace })
      const live = snapshot.workspace.live_by_session[wire.sessionId]
      return [live?.input, live?.output]
    }
    try {
      await client.connect()
      const settings = { env: { REFERENCE_MARKER: 'recovery' } }
      const modelRequest = { model: 'fixture-model', effort: 'high' }
      const model = modelArgsFor({ spec: SPEC, request: modelRequest })
      if (!model.ok) throw new Error('model request refused')
      await request('manual_pane.spawn', { workspace_path: wire.workspace, pane_id: 'recovery-reference', agent: 'claude',
        command: SPEC.defaultCommand, session_id: wire.sessionId, ...modelRequest })
      const base = `${SPEC.defaultCommand} ${model.args} ${SPEC.pinsSessionIdAtLaunch.flag} ${wire.sessionId}`
      const command = buildAgentPaneCommand(wire.shell, [base, '--settings', quoteCommandWord(JSON.stringify(settings))])
      const created = await request<{ terminal_session_id: string }>('terminal.create', { pane_id: 'recovery-reference', agent_key: 'claude',
        command, cwd: wire.workspace, env: { REFERENCE_MARKER: 'recovery' }, cols: 100, rows: 30,
        create_generation: 'recovery-1', metadata: { explicit_session_id: wire.sessionId } })
      const tid = created.terminal_session_id
      await until(() => (client.output.get(tid) ?? '').includes('CLAUDE_REFERENCE_READY '), 'actual Claude child READY')
      await request('terminal.input', { terminal_session_id: tid, data: 'stage 1\r' })
      await until(() => (client.output.get(tid) ?? '').includes('CLAUDE_STAGE 1'), 'vendor bytes appended')
      await expect.poll(totals, { timeout: 20_000, interval: 100 }).toEqual([15, 4])
      await until(() => activity.some((e) => e.detail === 'assistant:question') && activity.some((e) => e.detail === 'background:start'), 'stage-1 activity')
      const activityBefore = activity.length
      // Read-only durable checkpoints at stage 1. The store is write-behind (activity ~10 s, usage on a longer
      // cadence), so wait for the stage-1 activity high-water to be committed; a usage row may or may not be there yet.
      let durableBefore: Record<string, any> = {}
      await until(async () => {
        durableBefore = await driver.ctl<Record<string, any>>('checkpoint')
        return JSON.stringify(durableBefore['@activity']?.keys ?? []).includes('act_hw::5')
      }, 'stage-1 activity checkpoint durable', 40_000).catch((error) => { throw new Error(`${error.message}: ${JSON.stringify(durableBefore)}`) })
      const stageOne = ['assistant:question', 'background:start'].map((detail) => activity.filter((e) => e.detail === detail).length)
      const observedBefore = driver.receipts().filter((r) => r.event === 'observed').length

      // Fault: freeze the sidecar, append the next vendor data, hold a resize fence and an observation.
      await driver.ctl('sidecar-stop')
      await driver.ctl('append-stage', '2')
      let fenceSettled = false
      const fence = client.client.send('terminal.resize', { terminal_session_id: tid, cols: 120, rows: 40 }, 40_000)
        .finally(() => { fenceSettled = true })
      let observationSettled = false
      let observationEarly: unknown = null
      const observation = client.client.send('tokens.turns', { pane_id: 'recovery-reference', include_calls: true }, 40_000)
        .then((value) => { observationEarly = value; return value })
        .finally(() => { observationSettled = true })
      await delay(2500)
      expect(fenceSettled, 'resize fence settled early').toBe(false) // the fence request really is outstanding at the frozen sidecar
      expect(observationSettled, JSON.stringify(observationEarly).slice(0, 600)).toBe(false) // and so is a native observation on the same runtime
      await driver.ctl('sidecar-kill')
      const fenced = await fence
      expect(fenced.ok).toBe(false)
      expect(JSON.stringify(fenced)).toMatch(/RUNTIME_LOST|runtime/i)
      const observed = await observation
      expect((observed.payload as any)?.ok, JSON.stringify(observed).slice(0, 400)).toBe(false) // an explicit failure, not totals from nothing
      await until(() => client.exits.has(tid), 'the lost Claude session is presented', 45_000)
      expect(client.exits.get(tid)![0]).toMatchObject({ reason: 'runtime_lost', exit_code: null, signal: null })
      await until(() => driver.receipts().some((r) => r.event === 'runtime_invalidated' && r.complete), 'verified cleanup', 30_000)

      // The DURABLE checkpoints, read after the loss and before anything observes again: stage-1 rows unchanged, nothing advanced.
      const durableAfter = await driver.ctl<Record<string, any>>('checkpoint')
      for (const [scope, value] of Object.entries(durableBefore)) expect(durableAfter[scope], scope).toEqual(value) // nothing zeroed or changed
      for (const value of Object.values(durableAfter)) {
        if (typeof (value as any)?.offset === 'number') expect((value as any).offset).toBeLessThanOrEqual(firstOffset) // nothing advanced
      }
      expect(JSON.stringify(durableAfter['@activity']?.keys)).not.toContain('act_hw::10')
      // The persisted/live checkpoint did not advance, was not zeroed and nothing replayed.
      expect(await totals()).toEqual([15, 4])
      expect(activity.length).toBe(activityBefore)
      writeFileSync(join(driver.root, 'observation-during-loss.json'), JSON.stringify({ observationSettledBeforeKill: false, payload: JSON.stringify(observed.payload ?? null).slice(0, 200) }))

      // A fresh runtime observes the new vendor data exactly once.
      await expect.poll(totals, { timeout: 60_000, interval: 500 }).toEqual([22, 9])
      await until(() => activity.filter((e) => e.detail === 'background:end').length === 2, 'stage-2 activity on a fresh runtime', 30_000)
      // Stage 1 was not replayed by the fresh runtime: its event counts are exactly what they were.
      expect(['assistant:question', 'background:start'].map((detail) => activity.filter((e) => e.detail === detail).length)).toEqual(stageOne)
      expect(activity.filter((e) => e.event_type === 'turn_complete').map((e) => e.text)).toEqual(['Reference completed café 世界'])
      const receipts = driver.receipts()
      const ready = receipts.map((r, i) => (r.event === 'ready' ? i : -1)).filter((i) => i >= 0)
      expect(ready).toHaveLength(2)
      expect(receipts.findIndex((r) => r.event === 'runtime_invalidated')).toBeLessThan(ready[1])
      expect(receipts.filter((r) => r.event === 'observed').length).toBeGreaterThan(observedBefore)
      expect(receipts.filter((r) => r.event === 'observed' && r.runtime_generation === receipts[ready[1]].runtime_generation).length).toBeGreaterThan(0)
    } finally {
      client.dispose('claude recovery complete')
    }
    driver.stop()
    const durable = await driver.waitFile('durable.json', 60_000)
    expect(durable.usage.kind).toBe('jsonl')
    expect(durable.usage.offset).toBe(durable.file_size)
    expect(durable.usage.offset).toBeGreaterThan(firstOffset)
    expect(durable.usage.recent_keys).toHaveLength(2)
    expect(durable.activity.keys.filter((key: string) => !key.startsWith('act_pos::'))).toEqual(['act_hw::10'])
    expect(durable.activity.keys.filter((key: string) => key.startsWith('act_pos::'))).toHaveLength(1)
    expect([durable.totals.input, durable.totals.output]).toEqual([22, 9])
  })
}, 240_000)

it.runIf(process.platform === 'darwin')('a root the dead backend never registered is cleaned from the sidecar journal when the backend restarts', async () => {
  await withDriver('backend-death', async (driver) => {
    const first = new Client(driver.wire.url)
    let pid = 0
    let identity: Record<string, number> = {}
    const readyFile = join(driver.root, 'peer-ready')
    try {
      await first.connect()
      // A SIGHUP-immune child: only an identity-verified cleanup can end it once its PTY is gone.
      const pane = await first.shell(driver.wire, 'journal-only', 'journal-create', 20_000,
        { NAVIDE_PEER_IGNORE_HUP: '1', NAVIDE_PEER_READY_FILE: readyFile })
      pid = pane.ready.pid
      await until(() => existsSync(readyFile), 'child installed its signal handlers')
      identity = await driver.ctl<Record<string, number>>('identify', String(pid))
      // Substituted state: the registry row is removed, as if the backend died before it could register this root.
      expect((await driver.ctl<{ removed: boolean }>('registry-drop', String(pid))).removed).toBe(true)
      const crashed = await driver.ctl<any>('crash-pair', `${pid}:${identity[String(pid)]}`)
      expect(crashed).toMatchObject({ childAliveAfterCrash: true, sidecarGone: true })
    } finally {
      first.dispose('backend and sidecar crashed')
    }
    driver.stop()
    const wire2 = await driver.waitFile('wire-2.json', 90_000)
    // The restarted backend has already run its startup reap before it serves.
    const flags = await aliveFlags(driver, { [pid]: identity[String(pid)] })
    expect(flags[String(pid)]).toBe(false)
    const second = new Client(wire2.url)
    try {
      await second.connect()
      const answer = await second.reattach([])
      expect(answer.ok).toBe(true)
    } finally {
      second.dispose('restart verified')
    }
  })
}, 240_000)

it.runIf(process.platform === 'darwin')('a torn journal at restart refuses admission, keeps the immune child and the journal, and a retry after repair cleans it and admits', async () => {
  await withDriver('backend-death', async (driver) => {
    const first = new Client(driver.wire.url)
    let pid = 0
    let identity: Record<string, number> = {}
    const readyFile = join(driver.root, 'peer-ready')
    let generation = ''
    try {
      await first.connect()
      const pane = await first.shell(driver.wire, 'torn-journal', 'torn-create', 20_000,
        { NAVIDE_PEER_IGNORE_HUP: '1', NAVIDE_PEER_READY_FILE: readyFile })
      pid = pane.ready.pid
      await until(() => existsSync(readyFile), 'child installed its signal handlers')
      identity = await driver.ctl<Record<string, number>>('identify', String(pid))
      generation = driver.receipts().filter((r) => r.event === 'ready').at(-1)!.runtime_generation
      expect((await driver.ctl<{ removed: boolean }>('registry-drop', String(pid))).removed).toBe(true)
      const crashed = await driver.ctl<any>('crash-pair', `${pid}:${identity[String(pid)]}`)
      expect(crashed).toMatchObject({ childAliveAfterCrash: true, sidecarGone: true })
      // Substituted state: the dead run's journal ends in a torn append.
      expect((await driver.ctl<{ journals: string[] }>('tamper-journal')).journals).toContain(`${generation}.jsonl`)
    } finally {
      first.dispose('backend and sidecar crashed')
    }
    driver.stop()
    const wire2 = await driver.waitFile('wire-2.json', 90_000)
    const second = new Client(wire2.url)
    try {
      await second.connect()
      // The restarted backend refuses to admit a runtime; the immune child and its journal are untouched.
      const refused = await second.client.send('terminal.create', {
        pane_id: 'after-torn', agent_key: 'terminal', command: wire2.command, cwd: wire2.workspace,
        cols: 100, rows: 30, create_generation: 'after-torn-1',
      }, 30_000)
      expect(refused.ok).toBe(false)
      expect(JSON.stringify(refused)).toContain('ownership unresolved')
      expect((await aliveFlags(driver, { [pid]: identity[String(pid)] }))[String(pid)]).toBe(true)
      expect((await driver.ctl<{ journals: string[] }>('journal-files')).journals).toContain(`${generation}.jsonl`)
      expect(second.output.size).toBe(0) // no PTY was admitted
      // Recovery path: repair the journal; the next admission retries, cleans by identity, then admits.
      await driver.ctl('repair-journal')
      const fresh = await second.shell(wire2, 'after-repair', 'after-repair-1', 30_000)
      expect(fresh.tid).toBeTruthy()
      expect((await aliveFlags(driver, { [pid]: identity[String(pid)] }))[String(pid)]).toBe(false)
      expect((await driver.ctl<{ journals: string[] }>('journal-files')).journals).not.toContain(`${generation}.jsonl`)
      expect((await second.client.send('terminal.kill', { terminal_session_id: fresh.tid, force: true })).ok).toBe(true)
    } finally {
      second.dispose('torn journal case complete')
    }
  })
}, 240_000)

it.runIf(process.platform === 'darwin')('a cancel acknowledged while the ownership gate is held proves the target never runs, even after the hold is released', async () => {
  await withDriver('runtime', async (driver) => {
    const wire = driver.wire
    const client = new Client(wire.url)
    const marker = join(driver.root, 'p-cancel.marker')
    const detached = join(driver.root, 'p-cancel.detached')
    try {
      await client.connect()
      const warm = await client.shell(wire, 'warm-runtime', 'warm-create') // starts the sidecar and its journal
      await driver.ctl('journal-hold')
      const command = ['/bin/sh', '-c', 'trap "" HUP TERM; echo $$ > "$0"; sleep 120 & echo $! > "$1"; wait', marker, detached]
      const create = client.client.send('terminal.create', {
        pane_id: 'pane-p-cancel', agent_key: 'terminal', command, cwd: wire.workspace,
        cols: 100, rows: 30, create_generation: 'p-cancel-1',
      }, 60_000)
      await delay(1500) // the root is forked and gated; the journal append is held
      expect(existsSync(marker)).toBe(false)
      const ack = await client.client.send<{ cancelled: boolean }>('terminal.create.cancel', { pane_id: 'pane-p-cancel', create_generation: 'p-cancel-1' })
      expect(ack.ok, JSON.stringify(ack)).toBe(true)
      expect(ack.payload!.cancelled).toBe(true)
      expect(existsSync(marker)).toBe(false) // the ACK was given with the target still unexecuted
      await driver.ctl('journal-release') // only now does the held journal write resume
      const response = await create
      expect(response.ok).toBe(false)
      expect(JSON.stringify(response)).toContain('CREATE_CANCELLED')
      await delay(1500)
      expect(existsSync(marker)).toBe(false) // never executed, not even after the release
      expect(existsSync(detached)).toBe(false)
      const owned = driver.receipts().filter((r) => r.event === 'owned' && r.pane_id === 'pane-p-cancel')
      expect(owned).toEqual([])
      expect((await client.client.send('terminal.kill', { terminal_session_id: warm.tid, force: true })).ok).toBe(true)
    } finally {
      client.dispose('cancel during gate complete')
    }
  })
}, 150_000)
