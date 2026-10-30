// G3-r2-hosted rehearsal diagnostics: the entry of the ONE extra Node attempt a failed Linux `packaged-smoke` triggers (`hosted_diag.node_diagnostic` runs `bunx vitest run --project cli` on this file with ACR_G3=packaged-diag).
// It never fails the step it belongs to (it only reports) and writes nothing. The unit tests below always run and need no environment.
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'
import { DIAG_PREFIX, describeData, preloadFacts, recordingSocket, redactDiag, runPackagedDiagnostic, runtimeFacts } from './g3hPackagedDiag'

const selected = process.env.ACR_G3 ?? ''
const python = resolve('backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')

it.runIf(selected === 'packaged-diag')('G3 packaged attempt through Node, with the stage it reaches (rehearsal diagnostics, never a result)', async () => {
  const backend = resolve(process.env.ACR_G3_PACKAGED_BACKEND ?? '')
  expect(existsSync(backend), 'ACR_G3_PACKAGED_BACKEND (the frozen backend of the --dir layout) is required').toBe(true)
  await runPackagedDiagnostic({ backendBin: backend, python }, (line) => process.stdout.write(`${line}\n`))
}, 300_000)

// ---- unit tests (no environment, no process)
it('redactDiag removes the given secrets, token shapes and any websocket URL', () => {
  const token = `ghs_${'a'.repeat(30)}`
  const text = redactDiag(`connect ws://127.0.0.1:43210/ws?t=sekret failed, port 43210 token sekret ${token} github_pat_${'b'.repeat(30)}`, ['43210', 'sekret'])
  expect(text).not.toMatch(/43210|sekret|ghs_|github_pat_|127\.0\.0\.1/)
  expect(text).toContain('[redacted]')
})

it('describeData names the JS type of a binary payload and the instanceof facts the client depends on', () => {
  expect(describeData(new ArrayBuffer(4))).toBe('ArrayBuffer instanceof-ArrayBuffer=true instanceof-Uint8Array=false')
  expect(describeData(new Uint8Array(2))).toBe('Uint8Array instanceof-ArrayBuffer=false instanceof-Uint8Array=true')
  expect(describeData('text')).toBe('String instanceof-ArrayBuffer=false instanceof-Uint8Array=false')
})

it('runtimeFacts name the Node version, the executable, the global WebSocket and Bun, with no port or token', () => {
  const facts = runtimeFacts()
  expect(facts.map((line) => line.split('=')[0])).toEqual(['node version', 'execPath basename', 'typeof globalThis.WebSocket', 'bun version', 'platform'])
  expect(facts[0]).toBe(`node version=${process.version}`)
  expect(facts[1]).not.toContain('/')
})

it('preloadFacts says whether the deferral preload is loaded in this process and, if so, its refusal and fallback counts (counts only)', () => {
  const holder = globalThis as unknown as Record<symbol, unknown>
  const key = Symbol.for('acr.g3h.wsDefer')
  const saved = holder[key]
  try {
    delete holder[key]
    expect(preloadFacts()).toEqual(['ws deferral preload loaded in this process=false'])
    holder[key] = { loaded: true, deferred: 1, refused: 7, bounded: 0 }
    expect(preloadFacts()).toEqual(['ws deferral preload loaded in this process=true', 'ws deferral sockets deferred=1', 'refused TCP connects before the first accept=7', 'bounded fallbacks (real socket created without an accept)=0'])
  } finally {
    if (saved === undefined) delete holder[key]; else holder[key] = saved
  }
})

it('recordingSocket logs the events and each distinct payload type once, and is undefined without a global WebSocket', () => {
  const holder = globalThis as { WebSocket?: unknown }
  const saved = holder.WebSocket
  try {
    holder.WebSocket = class { private handlers = new Map<string, (event: unknown) => void>(); constructor(readonly url: string) {} addEventListener(type: string, handler: (event: unknown) => void): void { this.handlers.set(type, handler) } fire(type: string, event: unknown): void { this.handlers.get(type)?.(event) } }
    const lines: string[] = []
    const Recording = recordingSocket((line) => lines.push(line))!
    const socket = new Recording('ws://127.0.0.1:1/ws') as unknown as { fire: (type: string, event: unknown) => void }
    socket.fire('open', {})
    socket.fire('message', { data: new ArrayBuffer(1) })
    socket.fire('message', { data: new ArrayBuffer(2) })
    socket.fire('close', { code: 1000 })
    expect(lines).toEqual(['socket event: open', 'socket message data type: ArrayBuffer instanceof-ArrayBuffer=true instanceof-Uint8Array=false', 'socket event: close code=1000'])
    holder.WebSocket = undefined
    expect(recordingSocket(() => {})).toBeUndefined()
  } finally { holder.WebSocket = saved }
})

it('the diagnostic attempt reports a missing backend as a finding instead of throwing, with every line prefixed', async () => {
  const lines: string[] = []
  await runPackagedDiagnostic({ backendBin: resolve('does-not-exist/agent_team_backend'), python: resolve('does-not-exist/python') }, (line) => lines.push(line))
  expect(lines.length).toBeGreaterThan(5)
  expect(lines.every((line) => line.startsWith(DIAG_PREFIX))).toBe(true)
  expect(lines.some((line) => line.includes('attempt failed: '))).toBe(true)
  expect(lines.some((line) => line.includes('node version='))).toBe(true)
})
