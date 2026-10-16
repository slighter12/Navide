// @vitest-environment happy-dom
//
// Review-7 R7-1, principal seam: the production WebSocket client -> the production
// TerminalDockPort -> useTerminal, against the authenticated Python backend -> the actual
// sidecar -> native macOS PTY. The backend's committed create success is held at its awaited
// send (an owned test-only gate in the disposable backend; nothing faked); the pane cancels on
// the same connection, observes {ok:true, payload:{cancelled:false}}, the success is then
// delivered, and the pane must close that terminal (verified) before another create is admitted.
// A create whose response is lost must fence the pane instead. No UI automation.
import { describe, it, expect, vi } from 'vitest'

const store = vi.hoisted(() => {
  const values = new Map<string, string>()
  values.set('terminal-last-size', JSON.stringify({ cols: 80, rows: 24 }))
  vi.stubGlobal('localStorage', {
    get length(): number { return values.size },
    key: (i: number) => Array.from(values.keys())[i] ?? null,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, String(value)) },
    removeItem: (key: string) => { values.delete(key) },
    clear: () => {
      values.clear()
      values.set('terminal-last-size', JSON.stringify({ cols: 80, rows: 24 }))
    },
  })
  return values
})
const writes = vi.hoisted(() => [] as string[])
const ctrl = vi.hoisted(() => ({
  applyFit: vi.fn(), sendResizeNow: vi.fn(), requestResizeRedraw: vi.fn(), setColsCap: vi.fn(),
  capCols: vi.fn((cols: number) => cols), attachObserver: vi.fn(), dispose: vi.fn(), ackedCols: 0, ackedRows: 0,
}))
vi.mock('../useTerminalResize', () => ({ createResizeController: () => ctrl }))
vi.mock('@xterm/xterm', () => {
  class Terminal {
    cols = 80
    rows = 24
    options: Record<string, unknown> = {}
    unicode = { activeVersion: '6' }
    textarea = document.createElement('textarea')
    buffer = { active: { type: 'normal', viewportY: 0, baseY: 0, cursorX: 0, cursorY: 0, getLine: () => undefined } }
    loadAddon(): void {}
    open(): void {}
    attachCustomWheelEventHandler(): void {}
    attachCustomKeyEventHandler(): void {}
    registerLinkProvider(): { dispose(): void } { return { dispose(): void {} } }
    onResize(): { dispose(): void } { return { dispose(): void {} } }
    onData(): { dispose(): void } { return { dispose(): void {} } }
    write(data?: string): void { if (typeof data === 'string') writes.push(data) }
    writeln(data?: string): void { if (typeof data === 'string') writes.push(data) }
    resize(): void {}
    focus(): void {}
    select(): void {}
    clearSelection(): void {}
    hasSelection(): boolean { return false }
    onSelectionChange(): { dispose: () => void } { return { dispose: (): void => {} } }
    scrollLines(): void {}
    scrollToBottom(): void {}
    dispose(): void {}
  }
  return { Terminal }
})
vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class {
    fit(): void {}
    proposeDimensions(): { cols: number; rows: number } { return { cols: 80, rows: 24 } }
  },
}))
vi.mock('@xterm/addon-serialize', () => ({
  SerializeAddon: class { activate(): void {}; dispose(): void {}; serialize(): string { return '' } },
}))

import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { ref } from 'vue'
import { WebSocket as NodeWebSocket } from 'ws'
import { createWsClient } from '../../src/shared/wsClient'
import { createHostTerminalDockPort } from '../../src/renderer/src/composables/hostSurfacePorts'
import { useTerminal } from '../../src/renderer/src/platform/terminal/composables/useTerminal'
import { withScope } from '../../src/renderer/src/platform/terminal/composables/__tests__/mockBackend'
import { until, withDriver, type Driver } from './recoveryDriver'

/** The production client and port, wired like useBackend() minus its Electron glue. */
function productionPort(url: string) {
  const status = ref<string>('starting')
  const client = createWsClient({
    WebSocketImpl: NodeWebSocket as never,
    onStatus: (s) => { status.value = s },
  })
  const backend = {
    status, wsUrl: ref(url), httpUrl: ref(''), shell: ref(''), port: ref(0), pid: ref(0),
    lastError: ref(''), autoRestart: ref(null),
    send: client.send, on: client.on, restart: async () => {}, stop: async () => {},
  }
  const port = createHostTerminalDockPort(backend as never)
  const cancelResponses: any[] = []
  const realCancel = port.cancelCreate
  port.cancelCreate = async (paneId, generation) => {  // observation only
    const response = await realCancel(paneId, generation)
    cancelResponses.push(response)
    return response
  }
  client.connect(url)
  return { client, port, status, cancelResponses }
}

function tracked<T>(promise: Promise<T>) {
  const state = { settled: false, error: null as Error | null, at: 0 }
  promise.then(() => { state.settled = true; state.at = Date.now() },
    (error: Error) => { state.settled = true; state.error = error; state.at = Date.now() })
  return state
}

const heldFile = (driver: Driver, n: number) => join(driver.root, `create-send-held-${n}.json`)

describe.runIf(process.platform === 'darwin')('R7-1: the pane consumes the cancellation verdict (production port, real sidecar)', () => {
  it('cancelled:false + late success: the old root is reaped (verified) before another create is admitted', async () => {
    await withDriver('runtime-gated', async (driver) => {
      const wire = driver.wire
      const { client, port, status, cancelResponses } = productionPort(wire.url)
      const { result, scope } = withScope(() => useTerminal('gated-pane-1', port))
      try {
        await until(() => status.value === 'connected', 'authenticated WS')
        result.mount(document.createElement('div'))
        const spawning = result.spawn({ command: wire.command, cwd: wire.workspace, skipReattach: true, agentKey: 'terminal' })
        spawning.catch(() => {})
        await until(() => existsSync(heldFile(driver, 1)), 'the committed success send is held', 30_000)
        const held = JSON.parse(readFileSync(heldFile(driver, 1), 'utf8'))
        const owned = driver.receipts().find((r) => r.event === 'owned' && r.session_id === held.terminal_session_id)!
        const identity = { [owned.root.pid]: (await driver.ctl<Record<string, number>>('identify', String(owned.root.pid)))[String(owned.root.pid)] }

        const cancelling = result.cancelPendingCreate()
        const state = tracked(cancelling)
        await until(() => cancelResponses.length === 1, 'the cancel verdict on the same connection')
        expect(cancelResponses[0].ok).toBe(true)
        expect(cancelResponses[0].payload.cancelled).toBe(false)  // the backend committed this create
        await delay(500)
        expect(state.settled, 'cancelled:false must not complete the cancellation').toBe(false)
        expect(Object.values(await driver.ctl<Record<string, boolean>>('alive', `${owned.root.pid}:${identity[owned.root.pid]}`))).toEqual([true])

        await driver.ctl('create-send-release', '1')  // deliver the late success
        await until(() => state.settled, 'the cancellation resolves after the adopted term is closed', 30_000)
        expect(state.error).toBeNull()
        // At resolution the root is gone and the sidecar verified it.
        expect(Object.values(await driver.ctl<Record<string, boolean>>('alive', `${owned.root.pid}:${identity[owned.root.pid]}`))).toEqual([false])
        const cleaned = driver.receipts().find((r) => r.event === 'cleaned' && r.session_id === held.terminal_session_id)
        expect(cleaned).toMatchObject({ cleanup_complete: true, root_reaped: true })
        expect(result.sessionId.value).toBe('')  // never presented

        // Only now may the replacement go out.
        result.status.value = 'idle'
        const ownedBefore = driver.receipts().filter((r) => r.event === 'owned').length
        const respawn = result.spawn({ command: wire.command, cwd: wire.workspace, skipReattach: true, agentKey: 'terminal' })
        respawn.catch(() => {})
        await until(() => existsSync(heldFile(driver, 2)), 'the replacement create', 30_000)
        await driver.ctl('create-send-release', '2')
        await respawn
        const receipts = driver.receipts()
        const firstCleaned = receipts.findIndex((r) => r.event === 'cleaned' && r.session_id === held.terminal_session_id)
        const secondOwned = receipts.findIndex((r, i) => r.event === 'owned' && i >= 0 && r.session_id === result.sessionId.value)
        expect(receipts.filter((r) => r.event === 'owned')).toHaveLength(ownedBefore + 1)
        expect(firstCleaned).toBeGreaterThan(-1)
        expect(firstCleaned).toBeLessThan(secondOwned)
        await result.kill({ force: true })
      } finally {
        scope.stop()
        client.dispose('R7-1 case complete')
      }
    })
  }, 150_000)

  it('a create whose response is lost to a dropped connection fences the pane instead of assuming cleanup', async () => {
    await withDriver('runtime-gated', async (driver) => {
      const wire = driver.wire
      const { client, port, status } = productionPort(wire.url)
      const { result, scope } = withScope(() => useTerminal('gated-pane-2', port))
      try {
        await until(() => status.value === 'connected', 'authenticated WS')
        result.mount(document.createElement('div'))
        const spawning = result.spawn({ command: wire.command, cwd: wire.workspace, skipReattach: true, agentKey: 'terminal' })
        spawning.catch(() => {})
        await until(() => existsSync(heldFile(driver, 1)), 'the committed success send is held', 30_000)
        const held = JSON.parse(readFileSync(heldFile(driver, 1), 'utf8'))
        const owned = driver.receipts().find((r) => r.event === 'owned' && r.session_id === held.terminal_session_id)!
        const pid = owned.root.pid
        const created = (await driver.ctl<Record<string, number>>('identify', String(pid)))[String(pid)]

        client.reconnectNow('the success frame is lost with this connection')
        // The create's own failure is shown first; the settlement then fences the pane.
        await until(() => result.status.value === 'error' && /unresolved/.test(result.error.value), 'the pane is fenced', 30_000)
        // Fenced: the existing respawn path refuses and sends nothing.
        result.status.value = 'idle'
        await expect(result.spawn({ command: wire.command, cwd: wire.workspace, skipReattach: true, agentKey: 'terminal' }))
          .rejects.toThrow(/unresolved/)
        expect(driver.receipts().filter((r) => r.event === 'owned')).toHaveLength(1)

        // The backend side: the dead connection's held success send finds its session dead and
        // the create is rolled back (the existing disconnect path) once the send resumes.
        await driver.ctl('create-send-release', '1')
        await until(async () => Object.values(await driver.ctl<Record<string, boolean>>('alive', `${pid}:${created}`))[0] === false,
          'the existing disconnect rollback reaps the unnamed term', 30_000)
        const cleaned = driver.receipts().find((r) => r.event === 'cleaned' && r.session_id === held.terminal_session_id)
        expect(cleaned).toMatchObject({ cleanup_complete: true, root_reaped: true })
      } finally {
        scope.stop()
        client.dispose('R7-1 lost-response case complete')
      }
    })
  }, 150_000)
})
