import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { WebSocket as NodeWebSocket } from 'ws'
import { createWsClient } from '../../src/shared/wsClient'
import { settlePaneTerminals } from '../../src/renderer/src/lib/paneRetirement'
import { until, withDriver } from './recoveryDriver'

// Review-9 F1, principal seam: production WebSocket client -> authenticated Python handlers -> the
// actual sidecar -> native macOS PTY. A pane holds a known terminal id.
// FAULT (labelled, injected by an owned test-only gate in the disposable backend): `terminal.kill`
// and `manual_pane.release_pty` for that pane are REFUSED without being acted on, so the old root
// demonstrably stays alive. The Rebuild decision is App.vue's own: `settlePaneTerminals` (what onKill
// calls) with the pane's kill and `manual_pane.release_pty` as its steps, and Rebuild starts the
// replacement only when it answers true. After SIGCONT, a retry settles with verified cleanup and
// only then is a replacement admitted. T05_PRE_R9=1 replays the round-9 renderer, which ignored the
// release answer after a failed kill and went on to the replacement (the differential).

it.runIf(process.platform === 'darwin')('a known-id Rebuild whose kill and release are both unproven starts no replacement; a retry after verified release does', async () => {
  await withDriver('runtime-refusal', async (driver) => {
    const wire = driver.wire
    let connections = 0
    const client = createWsClient({
      WebSocketImpl: NodeWebSocket as never,
      onStatus: (status) => { if (status === 'connected') connections++ },
    })
    const send = <T>(type: string, payload: Record<string, unknown>, timeout?: number) => client.send<T>(type, payload, timeout)
    // sendQuiet's contract: the payload on success, null on refusal / timeout / transport failure.
    const quiet = async <T>(type: string, payload: Record<string, unknown>, timeout?: number): Promise<T | null> => {
      try {
        const response = await send<T>(type, payload, timeout)
        return response.ok ? (response.payload as T) : null
      } catch {
        return null
      }
    }
    let stopped = false
    try {
      client.connect(wire.url)
      await until(() => connections === 1, 'authenticated WS')
      const created = await send<{ terminal_session_id: string; pid: number }>('terminal.create', {
        pane_id: 'known-p1', agent_key: 'terminal', command: wire.command, cwd: wire.workspace,
        cols: 100, rows: 30, create_generation: 'known-g1',
      }, 30_000)
      expect(created.ok, JSON.stringify(created)).toBe(true)
      const oldTid = created.payload!.terminal_session_id
      const old = driver.receipts().find((r) => r.event === 'owned' && r.session_id === oldTid)!
      const pid = old.root.pid
      const birth = (await driver.ctl<Record<string, number>>('identify', String(pid)))[String(pid)]
      const oldAlive = async () => (await driver.ctl<Record<string, boolean>>('alive', `${pid}:${birth}`))[String(pid)]

      await driver.ctl('refuse-retirement', `known-p1:${oldTid}`)
      stopped = true
      const killFailures: unknown[] = []
      const rebuild = async (): Promise<boolean> => {
        const settled = await settlePaneTerminals({
          killOwn: async () => {
            const killed = await send('terminal.kill', { terminal_session_id: oldTid, force: true }, 30_000)
            if (!killed.ok) throw new Error(JSON.stringify(killed.error))
          },
          onKillFailed: (error) => killFailures.push(error),
          settle: () => quiet<{ ok: boolean }>('manual_pane.release_pty', { pane_id: 'known-p1' }, 90_000),
        })
        if (!settled && process.env.T05_PRE_R9 !== '1') return false
        const replacement = await send<{ terminal_session_id: string }>('terminal.create', {
          pane_id: `rebuilt-${Date.now()}`, agent_key: 'terminal', command: wire.command, cwd: wire.workspace,
          cols: 100, rows: 30, create_generation: `rebuilt-${Date.now()}`,
        }, 60_000)
        return replacement.ok
      }

      const first = await rebuild()
      expect(killFailures, 'the pane\'s own kill was refused').toHaveLength(1)
      expect(await oldAlive(), 'the old root is still alive (the release could not prove it gone)').toBe(true)
      expect(driver.receipts().filter((r) => r.event === 'owned'), 'a replacement was admitted while the old root lives').toHaveLength(1)
      expect(first, 'a Rebuild over an unproven old terminal must not start a replacement').toBe(false)

      await driver.ctl('refuse-retirement', '')  // the backend recovers
      stopped = false
      await delay(200)
      // Retry (the pane is still there and actionable): the release now proves settlement.
      const retried = await rebuild()
      expect(retried).toBe(true)
      expect(await oldAlive()).toBe(false)
      const receipts = driver.receipts()
      const cleaned = receipts.findIndex((r) => r.event === 'cleaned' && r.session_id === oldTid)
      const owned = receipts.map((r, i) => (r.event === 'owned' ? i : -1)).filter((i) => i >= 0)
      expect(owned).toHaveLength(2)
      expect(cleaned).toBeGreaterThan(-1)
      expect(cleaned).toBeLessThan(owned[1])
      expect(receipts[cleaned]).toMatchObject({ cleanup_complete: true, root_reaped: true })
    } finally {
      if (stopped) await driver.ctl('refuse-retirement', '').catch(() => undefined)
      client.dispose('F1 case complete')
    }
  })
}, 240_000)
