import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, copyFileSync, rmSync, realpathSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'

const python = resolve('backend/.venv/bin/python')
async function until(predicate: () => boolean, message: string, timeout = 15_000): Promise<void> {
  const deadline = Date.now() + timeout
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(message)
    await delay(5)
  }
}

async function withBackend(fault: string, run: (root: string, endpoint: any) => Promise<void>): Promise<void> {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-rust-shell-')))
  const driver = spawn(python, ['-m', 'tests.cli_regression.support.shell_runtime_driver', root, 'rust-shell', fault], {
    cwd: resolve('backend'), stdio: ['pipe', 'pipe', 'pipe'],
  })
  let log = ''
  let code: number | null | undefined
  driver.stdout.on('data', (data) => { log += data })
  driver.stderr.on('data', (data) => { log += data })
  driver.on('error', (error) => { log += String(error); code = -1 })
  driver.on('exit', (value) => { code = value })
  try {
    await until(() => {
      if (code !== undefined) throw new Error(log)
      return existsSync(join(root, 'wire.json'))
    }, 'backend readiness', 35_000)
    await run(root, JSON.parse(readFileSync(join(root, 'wire.json'), 'utf8')))
  } finally {
    driver.stdin.end('\n')
    await until(() => code !== undefined, 'driver cleanup', 25_000)
    const reports = resolve('test-results/ci/shell-runtime', `${fault || 'native'}-${basename(root)}`)
    mkdirSync(reports, { recursive: true })
    for (const name of ['backend.log', 'scenario.json', 'runtime-receipts.jsonl', 'process-identities.jsonl', 'control-order.json']) {
      if (existsSync(join(root, name))) copyFileSync(join(root, name), join(reports, name))
    }
    expect(code, log).toBe(0)
    rmSync(root, { recursive: true, force: true })
  }
}

it.runIf(process.platform === 'darwin')('uses one actual native runtime for concurrent shells, ordered Unicode, resize, reconnect and exits', async () => {
  await withBackend('', async (root, endpoint) => {
    let connections = 0
    const client = createWsClient({ onStatus: (status) => { if (status === 'connected') connections++ } })
    const output = new Map<string, string>()
    const decoders = new Map<string, TextDecoder>()
    const exits = new Map<string, any>()
    const tailBeforeExit = new Map<string, boolean>()
    client.on('terminal.output', (payload) => {
      const frame = payload as TerminalOutputFrame
      const decoder = decoders.get(frame.terminal_session_id) ?? new TextDecoder()
      decoders.set(frame.terminal_session_id, decoder)
      output.set(frame.terminal_session_id, (output.get(frame.terminal_session_id) ?? '') + decoder.decode(frame.data, { stream: true }))
    })
    client.on('terminal.exit', (payload) => {
      const exit = payload as { terminal_session_id: string; exit_code: number }
      exits.set(exit.terminal_session_id, exit)
      tailBeforeExit.set(exit.terminal_session_id, (output.get(exit.terminal_session_id) ?? '').includes('FINAL_TAIL café 世界'))
    })
    const peers: Array<{ tid: string; pid: number; workspace: string }> = []
    try {
      client.connect(endpoint.url)
      await until(() => connections === 1, 'authenticated WS')
      for (let index = 0; index < 4; index++) {
        const workspace = join(root, `workspace ${index} café`)
        mkdirSync(workspace)
        const created = await client.send<{ terminal_session_id: string; pid: number }>('terminal.create', {
          pane_id: `native-shell-${index}`, agent_key: 'terminal',
          command: index % 2 ? endpoint.wrapped : endpoint.command, cwd: workspace,
          cols: 100, rows: 30, create_generation: `native-create-${index}`,
        }, 15_000)
        expect(created.ok, JSON.stringify(created)).toBe(true)
        const tid = created.payload!.terminal_session_id
        await until(() => (output.get(tid) ?? '').includes('NATIVE_READY '), 'native child READY')
        const ready = JSON.parse(output.get(tid)!.split('NATIVE_READY ')[1].split('\r\n')[0])
        expect(ready.argv).toEqual(["café 世界 quote' \" \\ ; | &", 'second argument'])
        expect(ready.cwd).toBe(workspace)
        expect(ready.home).toBe(join(root, 'home'))
        expect(ready.providerKeys).toEqual([])
        expect([ready.isatty, ready.ctty, ready.foreground]).toEqual([true, true, true])
        expect(ready.dimensions).toEqual([100, 30])
        peers.push({ tid, pid: ready.pid, workspace })
        expect(ready.pid).toBeGreaterThan(0)
      }
      async function input(tid: string, data: string): Promise<void> {
        expect((await client.send('terminal.input', { terminal_session_id: tid, data })).ok).toBe(true)
      }
      await Promise.all(peers.map(async ({ tid }) => {
        await input(tid, 'split\r')
        await until(() => (output.get(tid) ?? '').includes('SPLIT café 世界'), 'split UTF-8')
        expect(output.get(tid)).not.toContain('\uFFFD')
        await input(tid, 'burst\r')
        await until(() => (output.get(tid) ?? '').includes('BURST 0000 '), 'first burst')
        await input(tid, 'during-burst-世界\r')
        await until(() => (output.get(tid) ?? '').includes('"text": "during-burst-世界"'), 'input while bursting')
        await until(() => (output.get(tid) ?? '').includes('BURST_DONE'), 'complete burst')
        let previous = -1
        for (let line = 0; line < 128; line++) {
          const expected = `BURST ${String(line).padStart(4, '0')} ${'x'.repeat(2048)}`
          const position = output.get(tid)!.indexOf(expected)
          expect(position).toBeGreaterThan(previous)
          expect(output.get(tid)!.indexOf(expected, position + 1)).toBe(-1)
          previous = position
        }
        expect((await client.send('terminal.resize', { terminal_session_id: tid, cols: 120, rows: 40 })).ok).toBe(true)
        await input(tid, 'size\r')
        await until(() => (output.get(tid) ?? '').includes('SIZE [120, 40]'), 'actual dimensions')
        expect((await client.send('terminal.interrupt', { terminal_session_id: tid })).ok).toBe(true)
        await until(() => (output.get(tid) ?? '').includes('INTERRUPTED'), 'controlling tty interrupt')
      }))
      client.reconnectNow('actual transport loss')
      await until(() => connections === 2, 'new authenticated connection')
      const attached = await client.send<{ alive: string[]; dead: string[] }>('terminal.reattach', {
        terminal_session_ids: peers.map((peer) => peer.tid), cols: 110, rows: 35,
      })
      expect(attached.payload!.alive.sort()).toEqual(peers.map((peer) => peer.tid).sort())
      expect(attached.payload!.dead).toEqual([])
      for (const peer of peers) {
        await input(peer.tid, 'same-process-世界\r')
        await until(() => (output.get(peer.tid) ?? '').includes(`"pid": ${peer.pid}, "text": "same-process-世界"`), 'same child identity')
      }
      await input(peers[0].tid, 'exit\r')
      await until(() => exits.has(peers[0].tid), 'natural exit')
      expect(exits.get(peers[0].tid).exit_code).toBe(7)
      expect(tailBeforeExit.get(peers[0].tid)).toBe(true)
      await Promise.all(peers.slice(1).map(async (peer, index) => {
        expect((await client.send('terminal.kill', { terminal_session_id: peer.tid, force: index === 0 })).ok).toBe(true)
      }))
      const gone = await client.send<{ dead: string[] }>('terminal.reattach', { terminal_session_ids: peers.map((peer) => peer.tid) })
      expect(gone.payload!.dead.sort()).toEqual(peers.map((peer) => peer.tid).sort())
      const receipts = readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
      const ready = receipts.filter((entry) => entry.event === 'ready')
      expect(ready).toHaveLength(1)
      expect(ready[0].runtime).toBe('navide-cli-runtime')
      expect(ready[0].version).toBe('0.1.0')
      expect(ready[0].executableSha256).toMatch(/^[a-f0-9]{64}$/)
      expect(receipts.filter((entry) => entry.event === 'owned')).toHaveLength(4)
      expect(receipts.filter((entry) => entry.event === 'cleaned' && entry.cleanup_complete && entry.root_reaped)).toHaveLength(4)
      const identities = readFileSync(join(root, 'process-identities.jsonl'), 'utf8').trim().split('\n').flatMap((line) => JSON.parse(line))
      expect(identities.some((entry) => entry.pid === ready[0].pid && entry.created === ready[0].created && entry.parent === endpoint.backend.pid)).toBe(true)
      for (const peer of peers) expect(identities.some((entry) => entry.pid === peer.pid && entry.created > 0)).toBe(true)
    } finally {
      client.dispose('native shell complete')
    }
  })
}, 90_000)

for (const [first, second] of [['terminal.resize', 'terminal.redraw'], ['terminal.redraw', 'terminal.reattach'], ['terminal.reattach', 'terminal.resize']]) {
  it.runIf(process.platform === 'darwin')(`holds overlapping ${first}/${second} output until each public ACK`, async () => {
    await withBackend('fences', async (root, endpoint) => {
      const client = createWsClient()
      const frames: Array<{ kind: string; text?: string }> = []
      let ready = false
      client.on('terminal.output', (payload) => {
        const frame = payload as TerminalOutputFrame
        const text = new TextDecoder().decode(frame.data)
        frames.push({ kind: 'output', text })
        if (text.includes('NATIVE_READY ')) ready = true
      })
      const control = (type: string, tid: string, cols: number) => client.send(type, {
        terminal_session_id: tid, terminal_session_ids: [tid], cols, rows: 40,
      }, 15_000)
      try {
        client.connect(endpoint.url)
        const created = await client.send<{ terminal_session_id: string }>('terminal.create', {
          pane_id: 'control-fences', agent_key: 'terminal', command: endpoint.command, cwd: root,
          cols: 100, rows: 30, create_generation: 'control-fences',
        })
        expect(created.ok).toBe(true)
        const tid = created.payload!.terminal_session_id
        await until(() => ready, 'real native child ready')
        const a = control(first, tid, 110).then((response) => { frames.push({ kind: 'ack-A' }); return response })
        await until(() => existsSync(join(root, 'control-ack-1.json')), 'delayed ACK A')
        expect((await client.send('terminal.input', { terminal_session_id: tid, data: 'fence-A\r' })).ok).toBe(true)
        await until(() => existsSync(join(root, 'fence-A.json')), 'child produced A bytes')
        const b = control(second, tid, 120).then((response) => { frames.push({ kind: 'ack-B' }); return response })
        await until(() => existsSync(join(root, 'control-entered-2.json')), 'overlapping B dispatch entered')
        // Functional ACK hold, not a timing sample: allow already-admitted B
        // to reach the inspected overwrite path while A is deliberately held.
        await delay(250)
        writeFileSync(join(root, 'control-release-1'), '')
        expect((await a).ok).toBe(true)
        await until(() => existsSync(join(root, 'control-ack-2.json')), 'delayed ACK B')
        expect((await client.send('terminal.input', { terminal_session_id: tid, data: 'fence-B\r' })).ok).toBe(true)
        await until(() => existsSync(join(root, 'fence-B.json')), 'child produced B bytes')
        expect(JSON.parse(readFileSync(join(root, 'fence-B.json'), 'utf8')).dimensions).toEqual([120, 40])
        await delay(250)
        writeFileSync(join(root, 'control-release-2'), '')
        expect((await b).ok).toBe(true)
        await until(() => frames.some((frame) => frame.text?.includes('fence-B')), 'published B bytes')
        const outputA = frames.findIndex((frame) => frame.text?.includes('fence-A'))
        const outputB = frames.findIndex((frame) => frame.text?.includes('fence-B'))
        writeFileSync(join(root, 'control-order.json'), JSON.stringify(frames, null, 2))
        expect(outputA).toBeGreaterThan(frames.findIndex((frame) => frame.kind === 'ack-A'))
        expect(outputB).toBeGreaterThan(frames.findIndex((frame) => frame.kind === 'ack-B'))
        expect((await client.send('terminal.kill', { terminal_session_id: tid, force: true })).ok).toBe(true)
        const receipts = readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
        expect(receipts.filter((entry) => entry.event === 'ready' && entry.executableSha256)).toHaveLength(1)
        expect(receipts.filter((entry) => entry.event === 'cleaned' && entry.cleanup_complete && entry.root_reaped)).toHaveLength(1)
      } finally {
        writeFileSync(join(root, 'control-release-1'), '')
        writeFileSync(join(root, 'control-release-2'), '')
        client.dispose('control fence test complete')
      }
    })
  }, 60_000)
}

for (const [fault, expected] of [['missing', 'missing'], ['exec', 'START_FAILED at exec'], ['startup', 'START_FAILED before ready'], ['protocol', 'PROTOCOL_MISMATCH at handshake']]) {
  it.runIf(process.platform === 'darwin')(`refuses ${fault} without a Python PTY fallback`, async () => {
    await withBackend(fault, async (root, endpoint) => {
      const client = createWsClient()
      const output: unknown[] = []
      client.on('terminal.output', (data) => output.push(data))
      try {
        client.connect(endpoint.url)
        const response = await client.send('terminal.create', {
          pane_id: `fault-${fault}`, agent_key: 'terminal', command: endpoint.command, cwd: root,
          create_generation: `fault-generation-${fault}`,
        }, 15_000)
        expect(response.ok).toBe(false)
        expect(JSON.stringify(response)).toContain(expected)
        expect(output).toEqual([])
        if (fault === 'startup' || fault === 'protocol') {
          const receipts = readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
          expect(receipts.some((entry) => entry.event === 'startup_failed' && entry.pid > 0 && entry.executableSha256)).toBe(true)
          expect(receipts.some((entry) => entry.event === 'owned')).toBe(false)
        }
      } finally {
        client.dispose('fault complete')
      }
    })
  }, 45_000)
}
