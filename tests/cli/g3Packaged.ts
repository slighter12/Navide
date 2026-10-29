// The packaged cold-start attempt (U7; review-1 finding 10): DESCRIPTIVE ONLY, one macOS row `descriptive|lat:packaged_cold_to_first_usable_ms|cell1`, outside the decider and outside every status.
// ONE attempt starts the FROZEN backend of the `electron-builder --dir` layout (`Navide.app/Contents/Resources/bin/agent_team_backend`, the staged F0 sidecar beside it) as a fresh isolated process,
// waits for it to serve, creates one shell pane through the production WebSocket client and stops at the arrival of its `BASELINE_READY {json}\n` line.
//   coldToFirstUsableMs = performance.now() at that arrival - performance.now() immediately before `spawn` of the backend (same clock; no Electron, no UI).
// Smoke mode (`smoke: true`, pre-F0): no stamp is taken; the receipt holds pass/fail only. An invalid scored attempt is recorded and does NOT stop the run (the row is descriptive).
import { execFileSync, spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, realpathSync, existsSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { buildAgentPaneCommand } from '../../src/main/plugins/agentPaneCommand'
import { ArrivalWaiter } from './g1eWait'
import { LaunchReady } from './g3Ready'

export const PACKAGED_ROW = 'descriptive|lat:packaged_cold_to_first_usable_ms|cell1'
export const PACKAGED_SCORED = 10
export const PACKAGED_WARMUPS = 1
export const READY_COMMAND = "printf 'BASELINE_READY {\"packaged\":true}\\n'; exec sleep 120"
const deadlineMs = 60_000
const pollMs = 20

export type PackagedSpec = { backendBin: string; python: string; repeat: number; warmup: boolean; smoke?: boolean }

/** Pure: the receipt of one packaged attempt. In smoke mode the stamps are null by construction. */
export function packagedReceipt(spec: Pick<PackagedSpec, 'repeat' | 'warmup' | 'smoke'>, observed: { passed: boolean; spawnAtMs: number | null; usableAtMs: number | null }): Record<string, unknown> {
  const stamps = !spec.smoke && observed.spawnAtMs !== null && observed.usableAtMs !== null && observed.usableAtMs >= observed.spawnAtMs
  return { schema: 'acr-g3-packaged-trial/1', row: PACKAGED_ROW, decisionBearing: false, repeat: spec.repeat, warmup: spec.warmup, smoke: spec.smoke === true, passed: observed.passed,
    coldToFirstUsableMs: stamps ? observed.usableAtMs! - observed.spawnAtMs! : null }
}

export async function runPackagedAttempt(spec: PackagedSpec): Promise<Record<string, unknown>> {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-g3p-')))
  const env = JSON.parse(execFileSync(spec.python, ['-B', '-m', 'tests.cli_regression.support.g3.helper', 'isolated-env', root], { cwd: resolve('backend'), encoding: 'utf8', timeout: 60_000 })) as Record<string, string>
  const waiter = new ArrivalWaiter()
  let connections = 0
  const client = createWsClient({ onStatus: (status) => { if (status === 'connected') { connections++; waiter.notify() } } })
  const ready = new LaunchReady()
  let ticks = 0
  client.on('terminal.output', (payload) => { const frame = payload as TerminalOutputFrame; ready.onFrame(frame.terminal_session_id, spec.smoke ? ++ticks : performance.now(), frame.data) })
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
      pane_id: 'packaged-pane', agent_key: 'terminal', cwd: root, command: buildAgentPaneCommand('/bin/bash', [READY_COMMAND]), cols: 100, rows: 30, create_generation: 'packaged-generation',
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
