// The Windows ConPTY probe (rh16, windows-4c). NOT EVIDENCE. Gated like g3Matrix.test.ts: it does nothing unless ACR_G3_CONPTY_PROBE=1 (the hosted driver's `conpty-probe` step sets it).
//
// It runs ONE measured-peer session per arm (the Python arm, then the Rust arm through the F0 sidecar) the way `g3ShellAttempt.runAttempt` does it (same driver, same create parameters, same stream handling and
// the same `g3hConpty` view), but for a few seconds instead of an attempt: READY, one io ACK, one burst round, one resize, FINAL. It writes, per arm, the RAW bytes the renderer stream received (`raw.bin`), the
// bytes after the view (`view.bin`) and `probe.json` with what matched: the READY cwd, the ACK needle, the burst window digest against the peer's own receipt, the reader's byte delta against what the peer
// accepted, and how often the window's END line is on the stream after the resize (a repaint shows up there). A mismatch fails the test, with the bytes already written, so the artifact alone says why.
import { spawn } from 'node:child_process'
import { createHash, randomBytes } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { buildAgentPaneCommand } from '../../src/main/plugins/agentPaneCommand'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { doneLine } from './g1eBurst'
import { endLine, startLine, windowDigest } from './g1eLedger'
import { finalLine, peerTextAfterMarker, readStable } from './g1eReceipt'
import { StreamBuffer } from './g1eStream'
import { conptyColumns, ConptyView } from './g3hConpty'

const enabled = process.env.ACR_G3_CONPTY_PROBE === '1'
const python = resolve('backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const out = resolve(process.env.ACR_G3_OUT ?? 'test-results/ci/g3/conpty-probe')
const sidecar = process.env.ACR_G3_SIDECAR ? resolve(process.env.ACR_G3_SIDECAR) : null
const workload = JSON.parse(readFileSync(resolve('tests/fixtures/cli-regression/runtime-workload-g1e.json'), 'utf8'))
const sha256 = (data: Buffer): string => createHash('sha256').update(data).digest('hex')
const count = (haystack: Buffer, needle: Buffer): number => { let n = 0; for (let at = haystack.indexOf(needle); at >= 0; at = haystack.indexOf(needle, at + 1)) n++; return n }
const escaped = (bytes: Buffer): string => JSON.stringify(bytes.toString('latin1'))

const unix = (path: string) => (payload: Record<string, unknown>): Promise<any> => new Promise((resolveReply, reject) => {
  const socket = net.connect(path)
  let buffer = ''
  const timer = setTimeout(() => { socket.destroy(); reject(new Error('counters socket deadline')) }, workload.deadlineMs)
  socket.on('data', (chunk) => { buffer += chunk; if (buffer.endsWith('\n')) { clearTimeout(timer); socket.end(); resolveReply(JSON.parse(buffer)) } })
  socket.on('error', (error) => { clearTimeout(timer); reject(error) })
  socket.write(`${JSON.stringify(payload)}\n`)
})
const counters = (raw: Record<string, unknown>): { read_total: number; emitted_total: number; dropped_oldest_total: number } => {
  const { read_total, emitted_total, dropped_oldest_total } = raw as Record<string, number>
  return { read_total, emitted_total, dropped_oldest_total }
}

async function probeArm(mode: 'python' | 'rust-shell', directory: string): Promise<{ problems: string[] }> {
  mkdirSync(directory, { recursive: true })
  const problems: string[] = []
  const report: Record<string, unknown> = { mode, platform: process.platform, columnsAsked: conptyColumns(100), resizeColumnsAsked: conptyColumns(120) }
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-g3h-conpty-')))
  const driver = spawn(python, ['-m', 'tests.cli_regression.support.g1e.driver', root, '1', '0', mode], {
    cwd: resolve('backend'), stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, env: { ...process.env, ...(sidecar ? { ACR_SIDECAR_BIN: sidecar } : {}) },
  })
  let log = ''
  let exited: number | null | undefined
  driver.stdout.on('data', (chunk) => { log += chunk })
  driver.stderr.on('data', (chunk) => { log += chunk })
  driver.on('error', (error) => { log += error.message; exited = -1 })
  driver.on('exit', (code) => { exited = code })
  let connections = 0
  const client = createWsClient({ onStatus: (status) => { if (status === 'connected') connections++ } })
  const raw: Buffer[] = []
  const viewBytes: Buffer[] = []
  const view = new ConptyView()
  const stream = new StreamBuffer()
  let last = -Infinity
  const until = async (ok: () => boolean, message: string, timeout: number): Promise<void> => {
    const deadline = performance.now() + timeout
    while (!ok()) {
      if (exited !== undefined) throw new Error(`${message}: driver exited: ${log.slice(-300)}`)
      if (performance.now() >= deadline) throw new Error(message)
      await delay(20)
    }
  }
  client.on('terminal.output', (payload) => {
    const frame = payload as TerminalOutputFrame
    const at = Math.max(performance.now(), last + 1e-6)
    last = at
    raw.push(Buffer.from(frame.data))
    const seen = view.push(frame.data)
    if (seen.length) { viewBytes.push(seen); stream.onChunk(at, seen) }
  })
  let terminalId = ''
  try {
    await until(() => existsSync(join(root, 'endpoint.json')), 'backend readiness deadline', 60_000)
    const endpoint = JSON.parse(readFileSync(join(root, 'endpoint.json'), 'utf8'))
    const countersCall = unix(endpoint.countersSocket)
    client.connect(endpoint.url)
    await until(() => connections === 1, 'authenticated WS connection deadline', workload.deadlineMs)
    const peer = endpoint.peers[0]
    const nonce = randomBytes(16).toString('hex')
    writeFileSync(join(root, 'g1e', `${peer.pane}.nonce`), nonce)
    const request = async (type: string, body: Record<string, unknown>): Promise<any> => {
      const response = await client.send<any>(type, body, workload.deadlineMs)
      if (!response.ok) throw new Error(`${type} refused: ${JSON.stringify(response).slice(0, 200)}`)
      return response.payload
    }
    const created = await request('terminal.create', {
      pane_id: peer.pane, agent_key: peer.vendor, cwd: peer.workspace,
      command: peer.wrapped ? buildAgentPaneCommand(endpoint.shell, [peer.commandLine]) : peer.command,
      cols: conptyColumns(100), rows: 30, create_generation: `${peer.pane}-generation`, output_log_file: peer.logPath,
      metadata: { workspace_path: peer.workspace, session_marker: `at-pane:${peer.pane}`, ...(peer.vendor === 'claude' ? { explicit_session_id: peer.pane } : {}) },
    })
    terminalId = created.terminal_session_id
    await stream.waitFor(Buffer.from('BASELINE_READY '), 'child READY deadline', workload.deadlineMs)
    await stream.until((buffer) => buffer.includes('\n', buffer.indexOf('BASELINE_READY ')), 'READY line deadline', workload.deadlineMs)
    const text = stream.toBuffer().toString('utf8')
    let ready: any = null
    try { ready = JSON.parse(text.split('BASELINE_READY ')[1].split('\n')[0].split('\r')[0]) } catch (error) { problems.push(`READY line is not JSON after the view: ${(error as Error).message}`) }
    report.readyCwdMatch = ready !== null && ready.cwd === peer.workspace
    report.readyArgvMatch = ready !== null && JSON.stringify(ready.argv) === JSON.stringify(peer.argv)
    report.readyHomeMatch = ready !== null && ready.home === endpoint.home
    report.readyEchoDisabled = ready !== null && ready.echoDisabled === true
    if (!report.readyCwdMatch) problems.push('READY cwd differs from the workspace the driver created (after the view)')
    await stream.waitFor(startLine(nonce), 'ledger start marker deadline', workload.deadlineMs)
    const origin = await readStable(async () => counters(await countersCall({ op: 'snapshot', session: terminalId })), 8)
    writeFileSync(join(root, 'g1e', `${peer.pane}.release`), 'go')
    const ackNeedle = Buffer.from(`BASELINE_ACK 0:${workload.input}\n`, 'utf8')
    const acked = stream.expect(ackNeedle, performance.now(), 'exact child ACK deadline', workload.deadlineMs)
    await request('terminal.input', { terminal_session_id: terminalId, data: `0:${workload.input}\r` })
    try { await acked; report.ackNeedleMatch = true } catch { report.ackNeedleMatch = false; problems.push('the exact ACK needle never arrived after the view') }
    const doneSeen = stream.waitFor(doneLine(0), 'burst completion deadline', 120_000)
    await request('terminal.input', { terminal_session_id: terminalId, data: 'burst r00\r' })
    try { await doneSeen; report.burstDoneMatch = true } catch { report.burstDoneMatch = false; problems.push('the burst completion line never arrived after the view') }
    await request('terminal.resize', { terminal_session_id: terminalId, cols: conptyColumns(120), rows: 40 })
    await delay(1500)
    const wid = `b${peer.index ?? 0}r00`
    const afterBurst = stream.toBuffer()
    report.windowEndLinesAfterResize = count(afterBurst, endLine(wid))
    const finalSeen = stream.until((buffer) => { const at = buffer.indexOf('BASELINE_FINAL:'); return at >= 0 && finalLine(buffer.subarray(at)) !== null }, 'FINAL line deadline', workload.deadlineMs)
    await request('terminal.input', { terminal_session_id: terminalId, data: 'g1e-final\r' })
    try { await finalSeen } catch { problems.push('the FINAL line never arrived after the view') }
    const close = await readStable(async () => counters(await countersCall({ op: 'snapshot', session: terminalId })), 8)
    const receiptPath = join(root, 'g1e', `${peer.pane}.peer.json`)
    await until(() => existsSync(receiptPath), 'peer receipt deadline', workload.deadlineMs)
    const receipt = JSON.parse(readFileSync(receiptPath, 'utf8'))
    const whole = stream.toBuffer()
    const post = peerTextAfterMarker(whole, nonce)
    const markerAt = whole.indexOf(startLine(nonce))
    const afterMarker = markerAt >= 0 ? whole.subarray(markerAt + startLine(nonce).length) : Buffer.alloc(0)
    const mine = windowDigest(afterMarker, wid)
    const theirs = (receipt.windows ?? []).find((w: { wid: string }) => w.wid === wid)?.digest ?? null
    report.windowDigestMatch = mine !== null && mine === theirs
    report.readerDeltaBytes = close.counters.read_total - origin.counters.read_total
    report.peerAcceptedSinceOrigin = receipt.acceptedSinceOrigin
    report.readerDeltaEqualsPeerAccepted = report.readerDeltaBytes === receipt.acceptedSinceOrigin
    report.viewBytesAfterMarker = post === null ? null : post.length
    report.rawBytes = Buffer.concat(raw).length
    report.viewBytes = whole.length
    report.heldBack = view.held().length
    report.originStable = origin.stable
    report.closeStable = close.stable
    if (!report.windowDigestMatch) problems.push('the burst window digest on the stream differs from the peer\'s own (or the window is not on the stream exactly once)')
    if (!report.readerDeltaEqualsPeerAccepted) problems.push(`the reader counted ${report.readerDeltaBytes} bytes, the peer accepted ${receipt.acceptedSinceOrigin}`)
    if (report.windowEndLinesAfterResize !== 1) problems.push(`the window END line is on the stream ${report.windowEndLinesAfterResize} times after the resize, 1 expected`)
    await request('terminal.kill', { terminal_session_id: terminalId })
  } catch (error) {
    problems.push(error instanceof Error ? error.message : String(error))
  } finally {
    client.dispose('conpty probe finished')
    driver.stdin.end('\n')
    try { await until(() => exited !== undefined, 'driver shutdown deadline', 60_000) } catch (error) { problems.push((error as Error).message) }
    const rawAll = Buffer.concat(raw)
    const viewAll = Buffer.concat(viewBytes)
    writeFileSync(join(directory, 'raw.bin'), rawAll)
    writeFileSync(join(directory, 'view.bin'), viewAll)
    const ackAt = rawAll.indexOf('BASELINE_ACK 0:')
    writeFileSync(join(directory, 'probe.json'), `${JSON.stringify({
      ...report, problems, rawSha256: sha256(rawAll), viewSha256: sha256(viewAll), driverExit: exited ?? null,
      rawHead: escaped(rawAll.subarray(0, 700)), rawAroundAck: ackAt >= 0 ? escaped(rawAll.subarray(Math.max(0, ackAt - 120), ackAt + 260)) : null, driverLogTail: log.slice(-400),
    }, null, 1)}\n`)
    rmSync(root, { recursive: true, force: true })
  }
  return { problems }
}

it.runIf(enabled)('G3 Windows ConPTY probe (NOT evidence): one measured-peer session per arm, raw bytes recorded, the unchanged checks recomputed after the view', async () => {
  expect(sidecar, 'ACR_G3_SIDECAR is required').not.toBeNull()
  const all: Record<string, string[]> = {}
  for (const mode of ['python', 'rust-shell'] as const) all[mode] = (await probeArm(mode, join(out, mode))).problems
  mkdirSync(out, { recursive: true })
  writeFileSync(join(out, 'conpty-probe-summary.json'), `${JSON.stringify({ notEvidence: true, platform: process.platform, problems: all }, null, 1)}\n`)
  expect(all).toEqual({ python: [], 'rust-shell': [] })
}, 1_200_000)
