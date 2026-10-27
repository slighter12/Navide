import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, readdirSync, copyFileSync, rmSync, writeFileSync } from 'node:fs'
import { basename, join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { driverEnv, isWindows, makeTempRoot, pythonExecutable } from './recoveryDriver'

async function until(predicate: () => boolean, message: string, timeout = 15_000): Promise<void> {
  const deadline = Date.now() + timeout
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(message)
    await delay(5)
  }
}

async function withBackend(fault: string, run: (root: string, endpoint: any) => Promise<void>): Promise<void> {
  const root = makeTempRoot('navide-rust-shell-')
  const driver = spawn(pythonExecutable, ['-m', 'tests.cli_regression.support.shell_runtime_driver', root, 'rust-shell', fault], {
    cwd: resolve('backend'), stdio: ['pipe', 'pipe', 'pipe'], env: driverEnv,
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
    for (const name of ['backend.log', 'scenario.json', 'runtime-receipts.jsonl', 'process-identities.jsonl', 'control-order.json', 'trial-proof.json']) {
      if (existsSync(join(root, name))) copyFileSync(join(root, name), join(reports, name))
    }
    expect(code, log).toBe(0)
    rmSync(root, { recursive: true, force: true })
  }
}

it.runIf(process.platform === 'darwin' || process.platform === 'linux' || process.platform === 'win32')('uses one actual native runtime for concurrent shells, ordered Unicode, resize, reconnect and exits', async () => {
  await withBackend('', async (root, endpoint) => {
    let connections = 0
    const client = createWsClient({ onStatus: (status) => { if (status === 'connected') connections++ } })
    const output = new Map<string, string>()
    const decoders = new Map<string, TextDecoder>()
    const exits = new Map<string, any>()
    const tailBeforeExit = new Map<string, boolean>()
    // Failure diagnostics only: when each session's chunks arrived (ms since the test began, cumulative bytes).
    const startedAt = Date.now()
    // Event-loop lag of this test process: a frozen client looks like a silent server from the outside.
    let loopLagMs = 0
    let lastTick = Date.now()
    const lagTimer = setInterval(() => {
      const now = Date.now()
      loopLagMs = Math.max(loopLagMs, now - lastTick - 50)
      lastTick = now
    }, 50)
    const chunkLog = new Map<string, Array<[number, number]>>()
    // Linux only: what each thread of the peer is doing (state letter and kernel wait channel), so a peer
    // blocked writing a full pty is told apart from one that is runnable but starved of CPU.
    const peerThreads = (pid: number): string => {
      if (process.platform !== 'linux') return 'n/a'
      try {
        return readdirSync(`/proc/${pid}/task`).map((task) => {
          const stat = readFileSync(`/proc/${pid}/task/${task}/stat`, 'utf8')
          const wchan = readFileSync(`/proc/${pid}/task/${task}/wchan`, 'utf8')
          let syscall = ''
          try { syscall = readFileSync(`/proc/${pid}/task/${task}/syscall`, 'utf8').trim().split(' ').slice(0, 4).join(' ') } catch { /* not readable */ }
          return `${task}:${stat.slice(stat.lastIndexOf(')') + 2, stat.lastIndexOf(')') + 3)}:${wchan}:sys[${syscall}]`
        }).join(' ')
      } catch (error) {
        return `unreadable ${(error as Error).message}`
      }
    }
    const describeFlow = (tid: string): string => {
      const log = chunkLog.get(tid) ?? []
      const text = output.get(tid) ?? ''
      const gaps = log.slice(1).map(([at], index) => at - log[index][0])
      return JSON.stringify({
        nowMs: Date.now() - startedAt, maxLoopLagMs: loopLagMs, chunks: log.length, bytes: log.at(-1)?.[1] ?? 0,
        firstChunkMs: log[0]?.[0] ?? null, lastChunkMs: log.at(-1)?.[0] ?? null, maxGapMs: gaps.length ? Math.max(...gaps) : null,
        chunkTimeline: log,
        burstRows: (text.match(/BURST \d{4} /g) ?? []).length, burstDone: text.includes('BURST_DONE'),
        echoSeen: text.includes('"text": "during-burst-世界"'), tail: text.slice(-120),
      })
    }
    client.on('terminal.output', (payload) => {
      const frame = payload as TerminalOutputFrame
      const flow = chunkLog.get(frame.terminal_session_id) ?? []
      flow.push([Date.now() - startedAt, (flow.at(-1)?.[1] ?? 0) + frame.data.byteLength])
      chunkLog.set(frame.terminal_session_id, flow)
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
        // Four ConPTYs each re-render a 256 KB burst row by row: a bound that suits a POSIX pty is too short there.
        // A hosted x64 run passed "input while bursting" and then missed BURST_DONE at 15 s (cause not pinned), so the
        // POSIX bound is wider too; only the deadline changed, not the content assertions.
        const burstWait = isWindows ? 120_000 : 60_000
        await until(() => (output.get(tid) ?? '').includes('BURST 0000 '), 'first burst', burstWait)
        await input(tid, 'during-burst-世界\r')
        try {
          await until(() => (output.get(tid) ?? '').includes('"text": "during-burst-世界"'), 'input while bursting', burstWait)
        } catch (error) {
          const peer = peers.find((candidate) => candidate.tid === tid)
          // Diagnosis only, after the bound has already failed: does the missing output ever arrive by itself
          // (slow delivery), or only after the session is poked with more input (stranded in the backend)?
          const before = describeFlow(tid)
          const echoed = () => (output.get(tid) ?? '').includes('"text": "during-burst-世界"')
          const deadlineAt = Date.now() + 4_000
          while (!echoed() && Date.now() < deadlineAt) await delay(50)
          const arrivedByItself = echoed() ? Date.now() - startedAt : null
          let arrivedAfterPoke: number | null = null
          if (!echoed()) {
            await client.send('terminal.input', { terminal_session_id: tid, data: 'poke\r' }).catch(() => undefined)
            const pokeDeadline = Date.now() + 3_000
            while (!echoed() && Date.now() < pokeDeadline) await delay(50)
            arrivedAfterPoke = echoed() ? Date.now() - startedAt : null
          }
          throw new Error(`${(error as Error).message}; peer ${peers.findIndex((candidate) => candidate.tid === tid)} exits=${JSON.stringify([...exits.keys()])} peerThreads=${peerThreads(peer?.pid ?? 0)} atTimeout=${before} late=${JSON.stringify({ arrivedByItself, arrivedAfterPoke })} afterWait=${describeFlow(tid)}`)
        }
        try {
          await until(() => (output.get(tid) ?? '').includes('BURST_DONE'), 'complete burst', burstWait)
        } catch (error) {
          const peer = peers.find((candidate) => candidate.tid === tid)
          // Diagnosis only, after the bound has already failed: does BURST_DONE ever arrive by itself, and where is the flow?
          const before = describeFlow(tid)
          const done = () => (output.get(tid) ?? '').includes('BURST_DONE')
          const deadlineAt = Date.now() + 10_000
          while (!done() && Date.now() < deadlineAt) await delay(50)
          throw new Error(`${(error as Error).message}; peer ${peers.findIndex((candidate) => candidate.tid === tid)} exits=${JSON.stringify([...exits.keys()])} peerThreads=${peerThreads(peer?.pid ?? 0)} atTimeout=${before} arrivedWithin10sAfter=${done()} afterWait=${describeFlow(tid)} bytes=${(output.get(tid) ?? '').length}`)
        }
        let previous = -1
        // A ConPTY re-renders long lines as screen rows: it cuts a wrapped line with CR LF and cursor-position
        // sequences. Those are layout, not content, so they are removed before the exact-bytes comparison
        // (a CR LF that is followed by the line's own filler cannot be a line end). POSIX bytes are compared as sent.
        const burstView = isWindows
          ? output.get(tid)!.replace(/\u001b\[[0-9;?]*[ -/]*[@-~]/g, '').replace(/\r\n(?=x)/g, '')
          : output.get(tid)!
        for (let line = 0; line < 128; line++) {
          const expected = `BURST ${String(line).padStart(4, '0')} ${'x'.repeat(2048)}`
          const position = burstView.indexOf(expected)
          if (position <= previous) {
            // Show the stream around the line's first row, so a terminal that re-flows long lines is diagnosable.
            const at = burstView.indexOf(`BURST ${String(line).padStart(4, '0')} `)
            expect.fail(`burst line ${line} is not contiguous (position ${position}, previous ${previous}); stream at its start: ${JSON.stringify(burstView.slice(Math.max(0, at - 30), at + 450))}`)
          }
          expect(position).toBeGreaterThan(previous)
          expect(burstView.indexOf(expected, position + 1)).toBe(-1)
          previous = position
        }
        expect((await client.send('terminal.resize', { terminal_session_id: tid, cols: 120, rows: 40 })).ok).toBe(true)
        await input(tid, 'size\r')
        await until(() => (output.get(tid) ?? '').includes('SIZE [120, 40]'), 'actual dimensions')
        expect((await client.send('terminal.interrupt', { terminal_session_id: tid })).ok).toBe(true)
        try {
          await until(() => (output.get(tid) ?? '').includes('INTERRUPTED'), `controlling tty interrupt (peer ${peers.findIndex((peer) => peer.tid === tid)})`)
        } catch (error) {
          // The wait's own message names the peer; the session state tells a peer that ended from one that ignored it.
          throw new Error(`${(error as Error).message}; exit=${JSON.stringify(exits.get(tid) ?? null)} tail=${JSON.stringify((output.get(tid) ?? '').slice(-200))}`)
        }
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
      // The identity monitor samples the process tree at its own (coarse) pace, so a short run can end before it
      // saw a peer (measured: a failing run had 4 snapshots in total). Wait for the observation instead of
      // assuming it: this is the receipt the assertion below reads.
      await until(() => {
        const text = readFileSync(join(root, 'process-identities.jsonl'), 'utf8')
        return peers.every((peer) => text.includes(`"pid": ${peer.pid},`))
      }, 'every peer observed by the identity monitor')
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
      clearInterval(lagTimer)
      client.dispose('native shell complete')
    }
  })
  // ConPTY re-renders every burst row, so four concurrent 256 KB bursts take far longer than on a POSIX pty.
}, isWindows ? 300_000 : 90_000)

for (const [first, second] of [['terminal.resize', 'terminal.redraw'], ['terminal.redraw', 'terminal.reattach'], ['terminal.reattach', 'terminal.resize']]) {
  it.runIf(process.platform === 'darwin')(`holds overlapping ${first}/${second} output until each public ACK`, async () => {
    await withBackend('fences', async (root, endpoint) => {
      const client = createWsClient()
      const frames: Array<{ kind: string; text?: string }> = []
      let ready = false
      client.on('terminal.output', (payload) => {
        const frame = payload as TerminalOutputFrame
        const text = new TextDecoder().decode(frame.data)
        // Recorded in a later macrotask: an ACK and the output that follows it can arrive in one
        // socket read, and the ACK's promise reaction (a microtask) would otherwise run after
        // this synchronous handler, inverting what the backend sent in order.
        setImmediate(() => { frames.push({ kind: 'output', text }) })
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

for (const [fault, expected] of [['missing', 'missing'], ['exec', 'START_FAILED at exec'], ['startup', 'START_FAILED: STARTUP_FAILED'], ['protocol', 'PROTOCOL_MISMATCH at handshake']]) {
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

// E1 P8, through the principal seam: a non-deferred kill hangs the CLI up (it ignores SIGTERM and records the SIGHUP it
// receives when every master closes) and the last words it wrote before the hangup reach the renderer before terminal.exit.
it.runIf(process.platform === 'darwin')('delivers the last words written before a hangup ahead of terminal.exit (P8)', async () => {
  await withBackend('', async (root, endpoint) => {
    let connections = 0
    const client = createWsClient({ onStatus: (status) => { if (status === 'connected') connections++ } })
    let text = ''
    const decoder = new TextDecoder()
    let exit: { exit_code: number | null; reason?: string } | undefined
    let atExit = ''
    client.on('terminal.output', (payload) => { text += decoder.decode((payload as TerminalOutputFrame).data, { stream: true }) })
    client.on('terminal.exit', (payload) => { exit = payload as typeof exit; atExit = text })
    try {
      client.connect(endpoint.url)
      await until(() => connections === 1, 'authenticated WS')
      const workspace = join(root, 'workspace hup')
      mkdirSync(workspace)
      const created = await client.send<{ terminal_session_id: string }>('terminal.create', {
        pane_id: 'native-shell-hup', agent_key: 'terminal', command: endpoint.command, cwd: workspace,
        cols: 100, rows: 30, create_generation: 'native-create-hup',
      }, 15_000)
      expect(created.ok, JSON.stringify(created)).toBe(true)
      const tid = created.payload!.terminal_session_id
      await until(() => text.includes('NATIVE_READY '), 'native child READY')
      expect((await client.send('terminal.input', { terminal_session_id: tid, data: 'hup-last-words\r' })).ok).toBe(true)
      // Gate the kill on the CLI having FINISHED writing (its own completion file, written after every write returned), not on
      // what the renderer has seen: the property under test is that words already written reach the renderer before the exit,
      // so the hangup must come while some of them may still be in flight, and never before the CLI wrote them.
      await until(() => existsSync(join(workspace, 'last-words-written.json')), 'the CLI finished writing its last words', 30_000)
      expect((await client.send('terminal.kill', { terminal_session_id: tid, force: false })).ok).toBe(true)
      await until(() => exit !== undefined, 'terminal.exit', 30_000)
      for (let index = 0; index < 64; index++) expect(atExit, `line ${index} was not delivered before terminal.exit`).toContain(`LAST ${String(index).padStart(3, '0')} ${'w'.repeat(2048)}`)
      expect(atExit).toContain('LAST_WORDS_END')
      // the CLI observed the hangup itself (SIGTERM was ignored, so only the closed masters could have ended it)
      await until(() => existsSync(join(workspace, 'hup-observed.json')), 'the CLI recorded the SIGHUP')
      expect(JSON.parse(readFileSync(join(workspace, 'hup-observed.json'), 'utf8')).signal).toBe('SIGHUP')
      const receipts = readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
      expect(receipts.filter((entry) => entry.event === 'hangup')).toHaveLength(1)
      expect(receipts.filter((entry) => entry.event === 'cleaned' && entry.cleanup_complete && entry.root_reaped)).toHaveLength(1)
    } finally {
      client.dispose('P8 complete')
    }
  })
}, 90_000)

function pidAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true } catch { return false }
}

/** One authenticated client with per-session output and exit records, for the Windows principal-seam cases below. */
async function windowsClient(endpoint: any) {
  let connections = 0
  const client = createWsClient({ onStatus: (status) => { if (status === 'connected') connections++ } })
  const output = new Map<string, string>()
  const decoders = new Map<string, TextDecoder>()
  const exits = new Map<string, any>()
  client.on('terminal.output', (payload) => {
    const frame = payload as TerminalOutputFrame
    const decoder = decoders.get(frame.terminal_session_id) ?? new TextDecoder()
    decoders.set(frame.terminal_session_id, decoder)
    output.set(frame.terminal_session_id, (output.get(frame.terminal_session_id) ?? '') + decoder.decode(frame.data, { stream: true }))
  })
  client.on('terminal.exit', (payload) => { exits.set((payload as { terminal_session_id: string }).terminal_session_id, payload) })
  client.connect(endpoint.url)
  await until(() => connections === 1, 'authenticated WS')
  const open = async (pane: string, command: string | string[], cwd: string) => {
    const created = await client.send<{ terminal_session_id: string }>('terminal.create', {
      pane_id: pane, agent_key: 'terminal', command, cwd, cols: 100, rows: 30, create_generation: pane,
    }, 30_000)
    expect(created.ok, JSON.stringify(created)).toBe(true)
    const tid = created.payload!.terminal_session_id
    await until(() => (output.get(tid) ?? '').includes('NATIVE_READY '), `${pane}: native child READY`, 60_000)
    const ready = JSON.parse(output.get(tid)!.split('NATIVE_READY ')[1].split('\r\n')[0])
    return { tid, ready }
  }
  const input = async (tid: string, data: string) => {
    expect((await client.send('terminal.input', { terminal_session_id: tid, data })).ok).toBe(true)
  }
  const kill = async (tid: string) => {
    expect((await client.send('terminal.kill', { terminal_session_id: tid, force: true })).ok).toBe(true)
  }
  return { client, output, exits, open, input, kill }
}

// E5 final review 5: the product's own Windows wrapper forms (`shellCommandArgv`, src/shared/osplat.ts), not the fixture launcher.
// The shell stays up after the program under -NoExit and /k, so an exit is the program's, then the shell's own.
for (const kind of ['pwsh', 'cmd'] as const) {
  it.runIf(process.platform === 'win32')(`uses one actual native runtime through the product's ${kind} wrapper: descendant cleanup, interrupt and exit`, async () => {
    await withBackend('', async (root, endpoint) => {
      const argv: string[] = endpoint.productWrappers[kind]
      expect(argv.length).toBeGreaterThan(1)
      const { client, output, exits, open, input, kill } = await windowsClient(endpoint)
      try {
        // descendant cleanup: a kill takes the program and both children it spawned
        const tree = await open(`wrap-${kind}-tree`, argv, root)
        await input(tree.tid, 'spawn-tree-hup\r')
        await until(() => (output.get(tree.tid) ?? '').includes('TREE '), `${kind}: the program spawned its children`)
        const children: number[] = JSON.parse(output.get(tree.tid)!.split('TREE ')[1].split('\r\n')[0]).pids
        expect(children).toHaveLength(2)
        await kill(tree.tid)
        await until(() => [tree.ready.pid, ...children].every((pid) => !pidAlive(pid)), `${kind}: the program and its children outlived the kill`, 30_000)

        // interrupt: Ctrl+C reaches the program through the wrapper
        const interrupt = await open(`wrap-${kind}-int`, argv, root)
        expect((await client.send('terminal.interrupt', { terminal_session_id: interrupt.tid })).ok).toBe(true)
        await until(() => (output.get(interrupt.tid) ?? '').includes('INTERRUPTED'), `${kind}: Ctrl+C did not reach the program`)
        await kill(interrupt.tid)
        await until(() => !pidAlive(interrupt.ready.pid), `${kind}: the program outlived the kill`, 30_000)

        // exit: the program ends with its own tail, then the shell it ran in
        const done = await open(`wrap-${kind}-exit`, argv, root)
        await input(done.tid, 'exit\r')
        await until(() => (output.get(done.tid) ?? '').includes('FINAL_TAIL café 世界'), `${kind}: the program's final output`)
        await until(() => !pidAlive(done.ready.pid), `${kind}: the program did not exit`, 30_000)
        await input(done.tid, 'exit\r')
        await until(() => exits.has(done.tid), `${kind}: the shell did not exit`, 30_000)
      } finally {
        client.dispose('wrapper case complete')
      }
    })
  }, 240_000)
}

// W10: kill, then recreate, repeatedly, with the creates of each round concurrent, so pipe and console handles are reused.
it.runIf(process.platform === 'win32')('uses one actual native runtime for kill and recreate cycles with concurrent creates that keep every session isolated', async () => {
  await withBackend('', async (root, endpoint) => {
    const { client, output, open, input, kill } = await windowsClient(endpoint)
    try {
      // Each requested session carries its own immutable marker as the peer's last argument: the READY line
      // (argv) and every echo (`marker`) must name it, so a session handed another session's complete handle
      // pair shows the other marker even though its own input would still come back.
      const markerOf = (round: number, slot: number) => `slot-${round}-${slot}-marker`
      // Handle reuse is the kernel's choice, so the cycles continue until a value from an earlier round is seen again,
      // bounded at MAX_ROUNDS; the test fails only when no reuse appears within the bound.
      const MAX_ROUNDS = 8
      const everyMarker = Array.from({ length: MAX_ROUNDS }, (_, round) => [0, 1, 2, 3].map((slot) => markerOf(round, slot))).flat()
      const receipts = () => readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
        .filter((entry) => entry.event === 'handle_attached')
      let roundsRun = 0
      let reuse: { round: number, value: number } | undefined
      for (let round = 0; round < MAX_ROUNDS && !reuse; round++) {
        roundsRun = round + 1
        const sessions = await Promise.all([0, 1, 2, 3].map((slot) =>
          open(`cycle-${round}-${slot}`, [...(endpoint.command as string[]), `marker=${markerOf(round, slot)}`], root)))
        expect(new Set(sessions.map((s) => s.ready.pid)).size).toBe(4)
        sessions.forEach((s, slot) => expect(s.ready.argv.at(-1), `round ${round} slot ${slot} READY marker`).toBe(`marker=${markerOf(round, slot)}`))
        await Promise.all(sessions.map((s, slot) => input(s.tid, `mark-${round}-${slot}\r`)))
        await Promise.all(sessions.map((s, slot) => until(
          () => (output.get(s.tid) ?? '').includes(`"text": "mark-${round}-${slot}"`), `round ${round} slot ${slot}: its own bytes`)))
        sessions.forEach((s, slot) => {
          const text = output.get(s.tid)!
          expect(text.split('NATIVE_READY ').length - 1, `round ${round} slot ${slot} READY count`).toBe(1)
          expect(text, `round ${round} slot ${slot} echo marker`).toContain(`"text": "mark-${round}-${slot}", "marker": "${markerOf(round, slot)}"`)
          for (const marker of everyMarker) if (marker !== markerOf(round, slot)) expect(text, `round ${round} slot ${slot} saw ${marker}`).not.toContain(marker)
          for (let other = 0; other < 4; other++) if (other !== slot) expect(text).not.toContain(`mark-${round}-${other}`)
          expect(text).not.toContain(`mark-${round - 1}-`)
        })
        await Promise.all(sessions.map((s) => kill(s.tid)))
        await until(() => sessions.every((s) => !pidAlive(s.ready.pid)), `round ${round}: a killed session's program is still alive`, 60_000)
        // A value this round's sessions hold that an earlier round's sessions already held is a reused handle
        // (within one round every session is live at once, so its values are distinct).
        const soFar: number[][] = receipts().map((entry) => entry.handles)
        const earlier = new Set(soFar.slice(0, 4 * round).flat())
        const repeated = soFar.slice(4 * round).flat().find((value) => earlier.has(value))
        if (repeated !== undefined) reuse = { round, value: repeated }
      }
      // The pipe handle values the client duplicated for every session: the cycles must really have reused them.
      const attached = receipts()
      expect(attached, `one handle receipt per created session over ${roundsRun} rounds`).toHaveLength(4 * roundsRun)
      const values: number[] = attached.flatMap((entry) => entry.handles)
      expect(values.every((value) => Number.isInteger(value) && value > 0), JSON.stringify(values)).toBe(true)
      expect(reuse, `no handle value was reused within ${MAX_ROUNDS} rounds: ${JSON.stringify(values)}`).toBeDefined()
      // Evidence: the first round that held a value an earlier round had held, and that value.
      console.log(`W10 handle reuse first seen in round ${reuse!.round} (of ${roundsRun} run): value ${reuse!.value}`)
    } finally {
      client.dispose('W10 cycle complete')
    }
  })
}, 600_000)
