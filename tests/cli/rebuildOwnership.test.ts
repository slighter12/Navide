import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { WebSocket as NodeWebSocket } from 'ws'
import { createWsClient } from '../../src/shared/wsClient'
import { until, withDriver } from './recoveryDriver'

// Review-8 R8-1(a), principal seam: production WebSocket client -> authenticated Python
// handlers -> the actual sidecar -> native macOS PTY.
//
// FAULT (labelled): the backend's committed `terminal.create` success is SENT and arrives at the
// client, where it is dropped at the client transport's message dispatch (the WebSocket's
// 'message' listener), then the connection is lost. The renderer never learns the terminal id. The test
// then plays the manual Rebuild message sequence of App.vue (onKill without a terminal id ->
// [A1] awaited `manual_pane.release_pty` for the old pane -> replacement `terminal.create` under a
// new pane id) and asserts the old root is verified-cleaned before the replacement's `owned`.
// T05_PRE_A1=1 replays the sequence WITHOUT the release (the pre-A1 renderer) as the differential.

const dropped: string[] = []
let dropPanePrefix = ''

// Wrapping the 'message' listener the production client registers (it uses addEventListener).
class TransportDroppingWebSocket extends NodeWebSocket {}
const baseAddEventListener = NodeWebSocket.prototype.addEventListener
Object.defineProperty(TransportDroppingWebSocket.prototype, 'addEventListener', {
  value(this: NodeWebSocket, type: string, listener: (event: { data: unknown }) => void, options?: unknown) {
    if (type !== 'message') return baseAddEventListener.call(this, type as never, listener as never, options as never)
    return baseAddEventListener.call(this, 'message', ((event: { data: unknown }) => {
      if (typeof event.data === 'string' && dropPanePrefix) {
        try {
          const frame = JSON.parse(event.data)
          if (frame.type === 'terminal.create.result' && frame.ok
            && String(frame.payload?.pane_id ?? '').startsWith(dropPanePrefix)) {
            dropped.push(frame.payload.terminal_session_id)
            return  // the frame arrived; the transport drops it before the client sees it
          }
        } catch { /* not JSON: pass through */ }
      }
      listener(event)
    }) as never, options as never)
  },
})

it.runIf(process.platform === 'darwin')('a committed create whose success is lost at the transport is cleaned (verified) by the Rebuild release before the replacement is admitted', async () => {
  await withDriver('runtime', async (driver) => {
    const wire = driver.wire
    let connections = 0
    const client = createWsClient({
      WebSocketImpl: TransportDroppingWebSocket as never,
      onStatus: (status) => { if (status === 'connected') connections++ },
    })
    try {
      client.connect(wire.url)
      await until(() => connections === 1, 'authenticated WS')
      dropPanePrefix = 'drop-'
      const lost = client.send('terminal.create', {
        pane_id: 'drop-p1', agent_key: 'terminal', command: wire.command, cwd: wire.workspace,
        cols: 100, rows: 30, create_generation: 'drop-g1',
      }, 8_000)
      lost.catch(() => undefined)
      await until(() => dropped.length === 1, 'the success frame reached the client and was dropped', 30_000)
      const oldTid = dropped[0]
      const old = driver.receipts().find((r) => r.event === 'owned' && r.session_id === oldTid)!
      const pid = old.root.pid
      const created = (await driver.ctl<Record<string, number>>('identify', String(pid)))[String(pid)]
      dropPanePrefix = ''
      client.reconnectNow('the connection is lost after the dropped success')
      await until(() => connections === 2, 'reconnected')

      // Rebuild: onKill(P1) has no terminal id; [A1] it awaits the existing release for the old pane.
      if (process.env.T05_PRE_A1 !== '1') {
        const released = await client.send('manual_pane.release_pty', { pane_id: 'drop-p1' }, 90_000)
        expect(released.ok, JSON.stringify(released)).toBe(true)
        expect((await driver.ctl<Record<string, boolean>>('alive', `${pid}:${created}`))[String(pid)]).toBe(false)
      }
      const replacement = await client.send<{ terminal_session_id: string }>('terminal.create', {
        pane_id: 'rebuilt-p2', agent_key: 'terminal', command: wire.command, cwd: wire.workspace,
        cols: 100, rows: 30, create_generation: 'rebuilt-g1',
      }, 30_000)
      expect(replacement.ok, JSON.stringify(replacement)).toBe(true)
      const newTid = replacement.payload!.terminal_session_id
      await delay(300)
      const receipts = driver.receipts()
      const cleaned = receipts.findIndex((r) => r.event === 'cleaned' && r.session_id === oldTid)
      const second = receipts.findIndex((r) => r.event === 'owned' && r.session_id === newTid)
      expect(cleaned, 'the old root was never cleaned').toBeGreaterThan(-1)
      expect(cleaned, "the replacement's CLI was admitted before the old root was cleaned").toBeLessThan(second)
      expect(receipts[cleaned]).toMatchObject({ cleanup_complete: true, root_reaped: true })
      expect((await driver.ctl<Record<string, boolean>>('alive', `${pid}:${created}`))[String(pid)]).toBe(false)
      expect((await client.send('terminal.kill', { terminal_session_id: newTid, force: true })).ok).toBe(true)
    } finally {
      dropPanePrefix = ''
      client.dispose('R8-1(a) case complete')  // the disposable backend's shutdown reaps any leftover
    }
  })
}, 150_000)
