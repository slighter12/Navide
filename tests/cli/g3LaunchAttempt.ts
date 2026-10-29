// G3 agent-launch attempt runner (H4): ONE agent pane through the production WebSocket client against the isolated backend (`g3/launch_driver.py`), fixture vendor only.
// Stamp sources are frozen in F0 (backend g3/launch.py STAMP_SOURCES): `agentCreateAtMs` = performance.now() immediately before `terminal.create`; `firstUsableAtMs` = performance.now() at the
// arrival of the chunk that completes the fixture `BASELINE_READY {json}\n` line. Records, never decides: the Python side recomputes validity from the receipt.
// SMOKE MODE (`spec.smoke`, the pre-F0 plumbing smoke): no stamp is taken at all (no performance.now() for a stamp, a counter orders the frames), no resource sampler exists on this path, and the
// receipt carries null stamps; only pass/fail and the arm-identity facts are read.
import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, realpathSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { buildAgentPaneCommand } from '../../src/main/plugins/agentPaneCommand'
import { ArrivalWaiter } from './g1eWait'
import { agentLaunchMs, type AgentLaunchVendor } from './g3AgentLaunch'
import { LaunchReady } from './g3Ready'
import type { RuntimeMode } from './runtimeMatched'

/** `shell` is the smoke-only plain shell pane; the registered rows use claude and aider. */
export type LaunchVendor = AgentLaunchVendor | 'shell'
export type LaunchSpec = {
  kind: 'calibration' | 'measured'; mode: RuntimeMode; vendor: LaunchVendor; cell: number; repeat: number; slot: number | null; firstArm: RuntimeMode | null; warmup: boolean
  freezeSha256: string; python: string; sidecarBin: string | null; toolchain: { match: boolean }
  /** The pre-F0 plumbing smoke: takes no stamp and no timing value (see the header). */
  smoke?: boolean
}

const deadlineMs = 60_000
const pollMs = 20

/** The smoke entry of one receipt: pass/fail and the arm-identity facts only (the shape `g3/smoke.py` checks); no stamp, latency, CPU or memory value. */
export function launchSmokeEntry(spec: Pick<LaunchSpec, 'mode' | 'vendor'>, receipt: Record<string, any>): Record<string, unknown> {
  return {
    kind: spec.vendor === 'shell' ? 'shell' : 'launch', mode: spec.mode, vendor: spec.vendor, passed: receipt.passed === true, evidenceValid: receipt.evidenceValid === true, owned: receipt.owned === true,
    argvSource: receipt.argvSource ?? null, readySidecarSha256: receipt.readySidecarSha256 ?? null, runtimeLost: receipt.runtimeLost === true, sidecarRestarts: receipt.sidecarRestarts ?? 0, survivors: receipt.survivors ?? [],
  }
}

/** The receipt body from what the run observed. Pure, so the shape is unit-tested without launching anything. */
export function launchReceipt(spec: LaunchSpec, observed: {
  agentCreateAtMs: number | null; firstUsableAtMs: number | null; argvSource: string | null; owned: boolean; readySidecarSha256: string | null
  runtimeLost: boolean; sidecarRestarts: number; survivors: number[]; isolationBreach: boolean; pythonPtySpawns: number; evidenceValid: boolean; passed: boolean
}): Record<string, unknown> {
  return {
    schema: 'acr-g3-launch-trial/1', kind: spec.kind, selectedRuntime: spec.mode, vendor: spec.vendor, cell: spec.cell, repeat: spec.repeat, slot: spec.slot, firstArm: spec.firstArm,
    warmup: spec.warmup, measuring: spec.kind === 'measured' && !spec.warmup, freezeSha256: spec.freezeSha256, toolchain: { match: spec.toolchain.match }, ...observed,
    launchMs: agentLaunchMs({ agentCreateAtMs: observed.agentCreateAtMs, firstUsableAtMs: observed.firstUsableAtMs }),
  }
}

export async function runLaunchAttempt(spec: LaunchSpec): Promise<Record<string, unknown>> {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-g3l-')))
  const driver = spawn(spec.python, ['-m', 'tests.cli_regression.support.g3.launch_driver', root, spec.vendor, String(spec.cell), spec.mode], {
    cwd: resolve('backend'), stdio: ['pipe', 'pipe', 'pipe'], env: { ...process.env, ...(spec.sidecarBin ? { ACR_SIDECAR_BIN: spec.sidecarBin } : {}) },
  })
  let exited = false
  driver.on('exit', () => { exited = true })
  driver.stdout.on('data', () => {})
  driver.stderr.on('data', () => {})
  const waiter = new ArrivalWaiter()
  let connections = 0
  const client = createWsClient({ onStatus: (status) => { if (status === 'connected') { connections++; waiter.notify() } } })
  const ready = new LaunchReady()
  let ticks = 0
  client.on('terminal.output', (payload) => {
    const frame = payload as TerminalOutputFrame
    ready.onFrame(frame.terminal_session_id, spec.smoke ? ++ticks : performance.now(), frame.data)
  })
  let createAt: number | null = null
  let usableAt: number | null = null
  let passed = false
  try {
    const endpointPath = join(root, 'endpoint.json')
    for (let waited = 0; !existsSync(endpointPath); waited += pollMs) { if (exited || waited > deadlineMs) throw new Error('backend readiness deadline'); await delay(pollMs) }
    const endpoint = JSON.parse(readFileSync(endpointPath, 'utf8'))
    client.connect(endpoint.url)
    await waiter.until(() => connections === 1, 'authenticated WS connection deadline', deadlineMs)
    const pane = endpoint.pane
    const readyWait = ready.waitReady(deadlineMs)              // registered BEFORE the create request: a READY line that beats the create response is not lost
    readyWait.catch(() => {})                                   // a failure before the await below must not leave an unhandled rejection
    if (!spec.smoke) createAt = performance.now()              // agentCreateAtMs: immediately before the request (smoke takes no stamp)
    const response = await client.send<{ terminal_session_id: string }>('terminal.create', {
      pane_id: pane.pane, agent_key: pane.vendor === 'shell' ? 'terminal' : pane.vendor, cwd: pane.workspace, command: buildAgentPaneCommand(endpoint.shell, [pane.commandLine]), cols: 100, rows: 30,
      create_generation: `${pane.pane}-generation`, metadata: { workspace_path: pane.workspace, session_marker: `at-pane:${pane.pane}`, ...(pane.vendor === 'claude' ? { explicit_session_id: pane.pane } : {}) },
    }, deadlineMs)
    if (!response.ok) throw new Error('terminal.create refused')
    const agentTid = response.payload!.terminal_session_id
    const stamp = await readyWait                              // firstUsableAtMs: arrival of the chunk completing the line
    if (stamp === null) throw new Error('READY line has no arrival stamp')
    if (ready.foreign(agentTid)) throw new Error('output of another terminal reached the client: the line is not attributable to the sole pane')
    usableAt = spec.smoke ? null : stamp
    passed = true
    await client.send('terminal.kill', { terminal_session_id: agentTid }, deadlineMs)
  } catch { passed = false }
  driver.stdin.write('done\n')
  await new Promise<void>((done) => { if (exited) done(); else driver.on('exit', () => done()) })
  const finalPath = join(root, 'launch-final.json')
  const final = existsSync(finalPath) ? JSON.parse(readFileSync(finalPath, 'utf8')) : null
  const receipt = launchReceipt(spec, {
    agentCreateAtMs: spec.smoke ? null : createAt, firstUsableAtMs: spec.smoke ? null : usableAt, argvSource: final?.argvSource ?? null, owned: final?.owned ?? false, readySidecarSha256: final?.readySidecarSha256 ?? null,
    runtimeLost: final?.runtimeLost ?? true, sidecarRestarts: final?.sidecarRestarts ?? 0, survivors: final?.survivors ?? [-1], isolationBreach: false, pythonPtySpawns: 0,
    evidenceValid: final !== null && passed, passed,
  })
  writeFileSync(join(root, 'receipt.json'), JSON.stringify(receipt))
  return receipt
}
