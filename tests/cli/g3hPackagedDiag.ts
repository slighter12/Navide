// G3-r2-hosted, rehearsal diagnostics only (never `cost`, `recover` or the registered run): ONE extra packaged attempt that mirrors `g3Packaged.ts` `runPackagedAttempt` step for step and, unlike it, says which
// stage it reached and what the caught error was. `runPackagedAttempt` swallows the reason (`catch { passed = false }`) and shows only `passed=false`; the rehearsal-4 Linux probe (Python client) reached READY while
// the Node attempt did not, so the difference is on the Node side. Copied from `runPackagedAttempt`, unchanged: the isolated environment, the spawn, the port and token discovery, the connect, the waiter registered
// before `terminal.create`, the create payload (same `buildAgentPaneCommand('/bin/bash', [READY_COMMAND])`), the deadlines. Added: the stage log, a recording subclass of the global WebSocket (event names and the
// JS type of every message `data`), the runtime facts, the facts of the deferral preload (`preloadFacts`: loaded or not, and its refusal count: a count, never a timing), and `redactDiag`. No port, no token and no timing value is ever printed.
import { execFileSync, spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, realpathSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { buildAgentPaneCommand } from '../../src/main/plugins/agentPaneCommand'
import { ArrivalWaiter } from './g1eWait'
import { LaunchReady } from './g3Ready'
import { READY_COMMAND } from './g3Packaged'

export const DIAG_PREFIX = 'g3h-diag '
const deadlineMs = 60_000
const pollMs = 20
const OUTPUT_LINES = 8
const MESSAGE_CAP = 150

/** Replaces every secret value and every token-shaped or socket-URL-shaped string with `[redacted]`. */
export function redactDiag(text: string, secrets: readonly string[] = []): string {
  let out = text
  for (const secret of secrets) if (secret) out = out.split(secret).join('[redacted]')
  return out
    .replace(/(ghs|ghp|gho|ghu|ghr)_[A-Za-z0-9]{20,}/g, '[redacted]')
    .replace(/github_pat_[A-Za-z0-9_]{20,}/g, '[redacted]')
    .replace(/ws:\/\/[^\s"']+/g, 'ws://[redacted]')
}

/** `Object.prototype.toString` type name of a WebSocket message payload, plus the one fact the client's own check depends on. */
export function describeData(data: unknown): string {
  const kind = Object.prototype.toString.call(data).slice(8, -1)
  return `${kind} instanceof-ArrayBuffer=${data instanceof ArrayBuffer} instanceof-Uint8Array=${data instanceof Uint8Array}`
}

export function runtimeFacts(): string[] {
  const socket = (globalThis as { WebSocket?: unknown }).WebSocket
  return [
    `node version=${process.version}`,
    `execPath basename=${basename(process.execPath)}`,
    `typeof globalThis.WebSocket=${typeof socket}`,
    `bun version=${process.versions.bun ?? 'not running under bun'}`,
    `platform=${process.platform}`,
  ]
}

/** What the Linux deferral preload (`hosted-net-preload.cjs`) says about this process: loaded or not and, when loaded, how many TCP connects it saw refused before the first accept (counts only, never a timing). */
export function preloadFacts(): string[] {
  const stats = (globalThis as unknown as Record<symbol, { loaded?: boolean; deferred?: number; refused?: number; bounded?: number } | undefined>)[Symbol.for('acr.g3h.wsDefer')]
  if (!stats?.loaded) return ['ws deferral preload loaded in this process=false']
  return ['ws deferral preload loaded in this process=true', `ws deferral sockets deferred=${stats.deferred}`, `refused TCP connects before the first accept=${stats.refused}`, `bounded fallbacks (real socket created without an accept)=${stats.bounded}`]
}

type SocketLike ={ addEventListener: (type: string, listener: (event: any) => void) => void }
type SocketCtor = new (url: string) => SocketLike

/** A subclass of the global WebSocket that logs its event names and the type of each distinct message payload; undefined when the runtime has no global WebSocket (the client then fails by itself, and says so). */
export function recordingSocket(log: (line: string) => void): SocketCtor | undefined {
  const Base = (globalThis as { WebSocket?: SocketCtor }).WebSocket
  if (!Base) return undefined
  const kinds = new Set<string>()
  return class extends Base {
    constructor(url: string) {
      super(url)
      this.addEventListener('open', () => log('socket event: open'))
      this.addEventListener('error', (event) => log(`socket event: error ${String(event?.message ?? event?.error?.message ?? '').slice(0, MESSAGE_CAP)}`))
      this.addEventListener('close', (event) => log(`socket event: close code=${event?.code}`))
      this.addEventListener('message', (event) => {
        const kind = describeData(event?.data)
        if (!kinds.has(kind)) { kinds.add(kind); log(`socket message data type: ${kind}`) }
      })
    }
  }
}

export type DiagSpec = { backendBin: string; python: string }

/** Runs one attempt and reports it through `emit`, one `DIAG_PREFIX` line each. Never throws: a failure is the finding. */
export async function runPackagedDiagnostic(spec: DiagSpec, emit: (line: string) => void): Promise<void> {
  const secrets: string[] = []
  const say = (line: string): void => emit(`${DIAG_PREFIX}${redactDiag(line, secrets)}`)
  const stage = (name: string): void => say(`stage reached: ${name}`)
  for (const fact of runtimeFacts()) say(fact)
  say(preloadFacts()[0])
  let child: ReturnType<typeof spawn> | null = null
  let client: ReturnType<typeof createWsClient> | null = null
  let exited = false
  const statuses: string[] = []
  try {
    const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-g3p-diag-')))
    const env = JSON.parse(execFileSync(spec.python, ['-B', '-m', 'tests.cli_regression.support.g3.helper', 'isolated-env', root], { cwd: resolve('backend'), encoding: 'utf8', timeout: 60_000 })) as Record<string, string>
    stage('isolated-env')
    const waiter = new ArrivalWaiter()
    let connections = 0
    client = createWsClient({ WebSocketImpl: recordingSocket(say) as never, onStatus: (status) => { statuses.push(status); say(`ws status: ${status}`); if (status === 'connected') { connections++; waiter.notify() } } })
    const ready = new LaunchReady()
    const frameTypes = new Set<string>()
    client.on('terminal.output', (payload) => {
      const frame = payload as TerminalOutputFrame
      const kind = Object.prototype.toString.call(frame.data).slice(8, -1)
      if (!frameTypes.has(kind)) { frameTypes.add(kind); say(`terminal.output frame data type: ${kind}`) }
      ready.onFrame(frame.terminal_session_id, performance.now(), frame.data)
    })
    child = spawn(spec.backendBin, ['--port', '0', '--log-level', 'warning'], { cwd: root, env: { ...env, NAVIDE_REGRESSION_ROOT: root }, stdio: ['pipe', 'ignore', 'ignore'] })
    child.on('exit', () => { exited = true })
    stage('backend spawned')
    child.stdin!.write('\n')
    const portFile = join(root, 'data', 'backend-port')
    const tokenFile = join(root, 'data', 'backend-ws-token')
    for (let waited = 0; !(existsSync(portFile) && existsSync(tokenFile)); waited += pollMs) { if (exited || waited > deadlineMs) throw new Error(exited ? 'the backend exited before it served' : 'packaged backend readiness deadline'); await delay(pollMs) }
    stage('port and token files present')
    let url = ''
    for (let waited = 0; !url; waited += pollMs) {
      const port = readFileSync(portFile, 'utf8').trim()
      const token = readFileSync(tokenFile, 'utf8').trim()
      if (/^\d+$/.test(port) && token) { url = `ws://127.0.0.1:${port}/ws?t=${token}`; secrets.push(port, token) }
      else { if (waited > deadlineMs) throw new Error('packaged backend discovery deadline'); await delay(pollMs) }
    }
    stage('url discovered')
    client.connect(url)
    await waiter.until(() => connections === 1, 'authenticated WS connection deadline', deadlineMs)
    stage('ws connected')
    const readyWait = ready.waitReady(deadlineMs)
    readyWait.catch(() => {})
    const response = await client.send<{ terminal_session_id: string }>('terminal.create', {
      pane_id: 'packaged-pane', agent_key: 'terminal', cwd: root, command: buildAgentPaneCommand('/bin/bash', [READY_COMMAND]), cols: 100, rows: 30, create_generation: 'packaged-generation',
      metadata: { workspace_path: root, session_marker: 'at-pane:packaged-pane' },
    }, deadlineMs)
    say(`terminal.create reply: ok=${response.ok}`)
    if (!response.ok) throw new Error('terminal.create refused')
    try { await readyWait } catch (error) {
      const seen = ready.stream.toBuffer().toString('utf8')
      say(`terminal.output frame arrived=${ready.stream.length > 0}`)
      for (const line of seen.split(/\r?\n/).slice(0, OUTPUT_LINES)) say(`pane output line: ${line.slice(0, 200)}`)
      throw error
    }
    stage('READY seen')
    say(`foreign pane output=${ready.foreign(response.payload!.terminal_session_id)}`)
    await client.send('terminal.kill', { terminal_session_id: response.payload!.terminal_session_id }, deadlineMs)
    stage('terminal.kill')
    say('attempt passed')
  } catch (error) {
    const e = error as { name?: string; constructor?: { name?: string }; message?: string }
    say(`attempt failed: ${e?.constructor?.name ?? e?.name ?? 'unknown'}: ${String(e?.message ?? error).slice(0, MESSAGE_CAP)}`)
  }
  say(`ws status sequence: ${statuses.length ? statuses.join(' > ') : 'none'}`)
  for (const fact of preloadFacts().slice(1)) say(fact)      // this attempt's own backend start: its sockets are the only ones the process opened
  try { client?.dispose('g3 packaged diagnostic finished') } catch { /* already torn down */ }
  try { child?.stdin?.write('shutdown\n') } catch { /* the backend is already gone */ }
  if (child && !exited) {
    await new Promise<void>((done) => {
      const timer = setTimeout(() => { child!.kill('SIGKILL'); done() }, 15_000)
      child!.on('exit', () => { clearTimeout(timer); done() })
    })
  }
}
