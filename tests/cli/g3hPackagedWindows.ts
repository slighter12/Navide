// G3-r2-hosted, Windows only: the packaged cold-start attempt of `g3Packaged.ts` with ONE difference, the pane command. `g3Packaged.ts` sends the POSIX line `printf 'BASELINE_READY {...}\n'; exec sleep 120` as
// `buildAgentPaneCommand('/bin/bash', ...)`; a Windows agent pane is split by `CommandLineToArgvW` and spawned without a shell, so that line can never print the READY line. The row is descriptive and
// enters no status. Here the command comes from the hosted plan (`ACR_G3H_READY_COMMAND`, `hosted_packaged.windows_ready_command`: `"<python>" -c "<script that prints the READY line and sleeps>"`).
// Copied unchanged from `g3Packaged.ts` `runPackagedAttempt` (it hard-codes the command and the waiter, so it cannot be reused): the whole attempt body, `deadlineMs`, `pollMs`. Imported, not copied: `PACKAGED_ROW`,
// `packagedReceipt`, `PackagedSpec`. Added: `VtNormalizer`, because ConPTY renders a screen, not a byte stream: it may precede the text with VT sequences (title OSC, cursor and mode changes) and may write a
// blank as a cursor-forward move; `LaunchReady` (frozen) needs the literal `BASELINE_READY ` and a later `\n`, so the frames are normalised before it sees them (sequences removed, `CSI n C` -> n blanks).
import { execFileSync, spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, realpathSync, existsSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { buildAgentPaneCommand } from '../../src/main/plugins/agentPaneCommand'
import { ArrivalWaiter } from './g1eWait'
import { LaunchReady } from './g3Ready'
import { packagedReceipt, type PackagedSpec } from './g3Packaged'

const deadlineMs = 60_000
const pollMs = 20
const MAX_PENDING = 4096

/** Removes VT control sequences from a byte stream, across frame boundaries: CSI (`ESC [ ... final`; `CSI n C` becomes n blanks, n defaulting to 1), OSC (`ESC ] ... BEL` or `ESC ] ... ESC \`) and two-byte escapes.
 *  Every other byte, including `\r` and `\n`, passes through. Bytes are handled as latin1, so non-ASCII payload bytes survive unchanged. */
export class VtNormalizer {
  private pending = ''

  push(data: Uint8Array): Uint8Array {
    const text = this.pending + Buffer.from(data).toString('latin1')
    this.pending = ''
    let out = ''
    for (let i = 0; i < text.length;) {
      if (text[i] !== '\x1b') { out += text[i]; i++; continue }
      if (i + 1 >= text.length) { this.pending = text.slice(i); break }
      const kind = text[i + 1]
      if (kind === '[') {
        let j = i + 2
        while (j < text.length && /[\x20-\x3f]/.test(text[j])) j++
        if (j >= text.length) { this.pending = text.slice(i); break }
        if (text[j] === 'C') out += ' '.repeat(Math.max(1, Number.parseInt(text.slice(i + 2, j) || '1', 10) || 1))
        i = j + 1
        continue
      }
      if (kind === ']') {
        let end = -1
        for (let j = i + 2; j < text.length; j++) {
          if (text[j] === '\x07') { end = j + 1; break }
          if (text[j] === '\x1b') { if (j + 1 >= text.length) break; if (text[j + 1] === '\\') { end = j + 2; break } }
        }
        if (end < 0) { this.pending = text.slice(i); break }
        i = end
        continue
      }
      i += 2
    }
    if (this.pending.length > MAX_PENDING) this.pending = ''
    return Buffer.from(out, 'latin1')
  }
}

export type PackagedWindowsSpec = PackagedSpec & { readyCommand: string }

export async function runPackagedAttemptWindows(spec: PackagedWindowsSpec): Promise<Record<string, unknown>> {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-g3p-')))
  const env = JSON.parse(execFileSync(spec.python, ['-B', '-m', 'tests.cli_regression.support.g3.helper', 'isolated-env', root], { cwd: resolve('backend'), encoding: 'utf8', timeout: 60_000 })) as Record<string, string>
  const waiter = new ArrivalWaiter()
  let connections = 0
  const client = createWsClient({ onStatus: (status) => { if (status === 'connected') { connections++; waiter.notify() } } })
  const ready = new LaunchReady()
  const normalizer = new VtNormalizer()
  let ticks = 0
  client.on('terminal.output', (payload) => { const frame = payload as TerminalOutputFrame; const clean = normalizer.push(frame.data); if (clean.length) ready.onFrame(frame.terminal_session_id, spec.smoke ? ++ticks : performance.now(), clean) })      // a frame of VT sequences only is empty once normalised, and the stream records an empty chunk as a violation
  let spawnAt: number | null = null
  let usableAt: number | null = null
  let passed = false
  let exited = false
  if (!spec.smoke) spawnAt = performance.now()                       // immediately before the backend process is started
  const child = spawn(spec.backendBin, ['--port', '0', '--log-level', 'warning'], { cwd: root, env: { ...env, NAVIDE_REGRESSION_ROOT: root }, stdio: ['pipe', 'ignore', 'ignore'] })
  child.on('exit', () => { exited = true })
  try {
    child.stdin.write('\n')
    const portFile = join(root, 'data', 'backend-port')
    const tokenFile = join(root, 'data', 'backend-ws-token')
    for (let waited = 0; !(existsSync(portFile) && existsSync(tokenFile)); waited += pollMs) { if (exited || waited > deadlineMs) throw new Error('packaged backend readiness deadline'); await delay(pollMs) }
    let url = ''
    for (let waited = 0; !url; waited += pollMs) {
      const port = readFileSync(portFile, 'utf8').trim()
      const token = readFileSync(tokenFile, 'utf8').trim()
      if (/^\d+$/.test(port) && token) url = `ws://127.0.0.1:${port}/ws?t=${token}`
      else { if (waited > deadlineMs) throw new Error('packaged backend discovery deadline'); await delay(pollMs) }
    }
    client.connect(url)
    await waiter.until(() => connections === 1, 'authenticated WS connection deadline', deadlineMs)
    const readyWait = ready.waitReady(deadlineMs)
    readyWait.catch(() => {})
    const response = await client.send<{ terminal_session_id: string }>('terminal.create', {
      pane_id: 'packaged-pane', agent_key: 'terminal', cwd: root, command: buildAgentPaneCommand('', [spec.readyCommand]), cols: 100, rows: 30, create_generation: 'packaged-generation',
      metadata: { workspace_path: root, session_marker: 'at-pane:packaged-pane' },
    }, deadlineMs)
    if (!response.ok) throw new Error('terminal.create refused')
    const stamp = await readyWait
    if (stamp === null || ready.foreign(response.payload!.terminal_session_id)) throw new Error('READY line is not attributable to the sole pane')
    usableAt = spec.smoke ? null : stamp
    passed = true
    await client.send('terminal.kill', { terminal_session_id: response.payload!.terminal_session_id }, deadlineMs)
  } catch { passed = false }
  client.dispose('g3 packaged attempt finished')
  try { child.stdin.write('shutdown\n') } catch { /* the backend is already gone */ }
  await new Promise<void>((done) => {
    if (exited) { done(); return }
    const timer = setTimeout(() => { child.kill('SIGKILL'); done() }, 15_000)
    child.on('exit', () => { clearTimeout(timer); done() })
  })
  const receipt = packagedReceipt(spec, { passed, spawnAtMs: spawnAt, usableAtMs: usableAt })
  writeFileSync(join(root, 'receipt.json'), JSON.stringify(receipt))
  return receipt
}
