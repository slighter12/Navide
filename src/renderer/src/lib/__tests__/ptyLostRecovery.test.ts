// @vitest-environment happy-dom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  acquirePaneRebuildLock,
  paneBusyForRebuild,
  paneCanRebuild,
} from '../../platform/plugin-shell/lib/resume-command'
import { shouldQueuePtyLostResume } from '../ptyLostRecovery'

// The decision a lost PTY (backend outage or Agent CLI Runtime loss) takes
// before it may be queued for the existing conversation-resume path. Executed
// here over the whole matrix; the rebuild gates that follow it (resumable
// session, rebuild lock, busy) are the real pure helpers App.vue calls.
describe('shouldQueuePtyLostResume — setting and visibility at report time', () => {
  const visible = new Set(['pane-a'])

  it('queues only a visible pane with the existing setting on', () => {
    expect(shouldQueuePtyLostResume({ autoResume: true, onScreenPaneIds: visible, paneId: 'pane-a' })).toBe(true)
  })

  it('never queues when the existing setting is off, visible or not', () => {
    expect(shouldQueuePtyLostResume({ autoResume: false, onScreenPaneIds: visible, paneId: 'pane-a' })).toBe(false)
    expect(shouldQueuePtyLostResume({ autoResume: false, onScreenPaneIds: new Set(), paneId: 'pane-a' })).toBe(false)
  })

  it('leaves a non-visible pane to its manual Rebuild', () => {
    expect(shouldQueuePtyLostResume({ autoResume: true, onScreenPaneIds: visible, paneId: 'pane-b' })).toBe(false)
    expect(shouldQueuePtyLostResume({ autoResume: true, onScreenPaneIds: new Set(), paneId: 'pane-a' })).toBe(false)
  })

  it('judges the visibility it is handed, so the caller captures it at report time', () => {
    const live = new Set(['pane-a'])
    const reported = shouldQueuePtyLostResume({ autoResume: true, onScreenPaneIds: live, paneId: 'pane-a' })
    live.clear() // the user navigates away during the batching window
    expect(reported).toBe(true)
  })
})

describe('the gates a queued pane still passes through (existing helpers, unchanged)', () => {
  it('only a pane with a pinned session on disk of a rebuild-capable agent is resumable', () => {
    expect(paneCanRebuild({ agentKey: 'claude', pinnedSessionId: 's1', sessionOnDisk: true })).toBe(true)
    expect(paneCanRebuild({ agentKey: 'claude', pinnedSessionId: 's1', sessionOnDisk: false })).toBe(false)
    expect(paneCanRebuild({ agentKey: 'claude', sessionOnDisk: true })).toBe(false)
    expect(paneCanRebuild({ agentKey: 'terminal', pinnedSessionId: 's1', sessionOnDisk: true })).toBe(false)
  })

  it('a second rebuild of the same pane or session is locked out until the first releases', () => {
    const locks = new Set<string>()
    const release = acquirePaneRebuildLock(locks, ['pane-a', 'session-1'])
    expect(release).not.toBeNull()
    expect(acquirePaneRebuildLock(locks, ['pane-a', 'session-2'])).toBeNull()
    expect(acquirePaneRebuildLock(locks, ['pane-b', 'session-1'])).toBeNull()
    release!()
    expect(acquirePaneRebuildLock(locks, ['pane-a', 'session-1'])).not.toBeNull()
  })

  it('a pane whose terminal create is still in flight is never rebuilt over', () => {
    expect(paneBusyForRebuild('starting', 'starting', 0)).toBe('starting')
  })
})

// App.vue cannot be mounted by this suite; the wiring is asserted against its
// source like the other App.*.test.ts files. The decision itself is executed above.
const appSource = readFileSync(resolve(process.cwd(), 'src/renderer/src/App.vue'), 'utf8')

function fn(name: string): string {
  const start = appSource.indexOf(`function ${name}(`)
  expect(start).toBeGreaterThan(-1)
  const end = appSource.indexOf('\n}\n', start)
  expect(end).toBeGreaterThan(start)
  return appSource.slice(start, end)
}

describe('App.vue wiring of the recovery decision', () => {
  it('queues through the single gate, with the setting read at report time', () => {
    const body = fn('onPanePtyLost')
    expect(body).toContain('shouldQueuePtyLostResume(')
    expect(body).toContain('AUTO_RESUME_ON_RECONNECT_SETTING_KEY')
    expect(body).toContain('onScreenPaneIds.value')
    expect(body.indexOf('shouldQueuePtyLostResume(')).toBeLessThan(body.indexOf('pendingPtyLostPanes.add'))
  })

  it('resumes through the existing rebuild path and injects nothing itself', () => {
    const body = fn('flushPtyLostResumes')
    expect(body).toContain('rebuildPaneViaResume(')
    expect(body).toContain('offerContinue: true')
    for (const injector of ['injectPane', 'continueRestoredPane', 'terminal.input', 'sendInput']) {
      expect(body).not.toContain(injector)
    }
  })
})
