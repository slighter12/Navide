import { afterEach, describe, expect, it, vi } from 'vitest'
import { join, posix, win32 } from 'node:path'
import { tmpdir } from 'node:os'
import { mkdirSync, mkdtempSync, writeFileSync, readFileSync, rmSync, statSync, realpathSync, symlinkSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { Window } from 'happy-dom'

const roots: string[] = []
const electron = vi.hoisted(() => {
  const paths = new Map<string, string>()
  return { paths, setPath: vi.fn((name: string, value: string) => paths.set(name, value)), getPath: vi.fn((name: string) => paths.get(name)) }
})
vi.mock('electron', () => ({ app: electron }))

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.resetModules()
  vi.clearAllMocks()
  electron.paths.clear()
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

function evaluationRoot(): string {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'ni4-eval-')))
  roots.push(root)
  writeFileSync(join(root, 'desktop-host-evaluation.json'), JSON.stringify({ schemaVersion: 1, runId: '0123456789abcdef' }), { mode: 0o600 })
  vi.stubGlobal('__NAVIDE_DESKTOP_HOST_EVALUATION__', true)
  vi.stubEnv('NAVIDE_DESKTOP_HOST_EVALUATION_ROOT', root)
  return root
}

describe('desktop Host evaluation isolation', () => {
  it.each(['darwin', 'linux', 'win32'] as const)('computes %s native paths from the requested platform, not the runner', async (platform) => {
    const { evaluationPaths } = await import('./desktopHostEvaluation')
    const path = platform === 'win32' ? win32 : posix
    const root = platform === 'win32' ? 'C:\\ni4-eval' : '/ni4-eval'
    expect(evaluationPaths(root, platform)).toEqual(Object.fromEntries([
      ['home', 'home'], ['appData', 'app-data'], ['userData', 'user-data'], ['sessionData', 'session-data'],
      ['temp', 'tmp'], ['logs', 'logs'], ['crashDumps', 'crashes'],
    ].map(([name, leaf]) => [name, path.join(root, leaf)])))
  })

  it('synchronously isolates every native path before later app state can read it', async () => {
    const root = evaluationRoot()
    const { desktopHostEvaluation, evaluationPaths } = await import('./desktopHostEvaluation')
    expect(desktopHostEvaluation.enabled).toBe(true)
    const paths = evaluationPaths(root, process.platform)
    expect(Object.fromEntries(electron.paths)).toEqual(paths)
    for (const [name, path] of Object.entries(paths)) {
      expect(electron.getPath(name)).toBe(path)
      expect(statSync(path).isDirectory()).toBe(true)
    }
  })

  it('canonicalizes a fixture created through an OWN-contained temp alias', async () => {
    const owner = realpathSync(mkdtempSync(join(tmpdir(), 'ni4-alias-owner-')))
    roots.push(owner)
    const target = join(owner, 'target'), alias = join(owner, 'alias')
    mkdirSync(target)
    symlinkSync(target, alias, process.platform === 'win32' ? 'junction' : 'dir')
    for (const name of ['TMPDIR', 'TMP', 'TEMP']) vi.stubEnv(name, alias)
    const root = evaluationRoot()
    const { desktopHostEvaluation, evaluationPaths } = await import('./desktopHostEvaluation')
    expect(root).toBe(realpathSync(root))
    expect(desktopHostEvaluation.enabled).toBe(true)
    expect(Object.fromEntries(electron.paths)).toEqual(evaluationPaths(root, process.platform))
  })

  it('fails closed without the marked OWN root instead of using normal profiles', async () => {
    vi.stubGlobal('__NAVIDE_DESKTOP_HOST_EVALUATION__', true)
    vi.stubEnv('NAVIDE_DESKTOP_HOST_EVALUATION_ROOT', '')
    await expect(import('./desktopHostEvaluation')).rejects.toThrow('evaluation root')
    expect(electron.setPath).not.toHaveBeenCalled()
  })

  it('reads only the current MAIN pane-owned xterm getter, never clean/raw/fallback data', async () => {
    const root = evaluationRoot()
    const workspace = join(root, 'A')
    mkdirSync(workspace)
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    const nonce = '0123456789abcdef'
    const dom = new Window()
    vi.stubGlobal('window', dom); vi.stubGlobal('document', dom.document)
    vi.stubGlobal('navigator', dom.navigator); vi.stubGlobal('self', dom)
    // Import the actual exported serializer without pulling renderer files into
    // the main-process TypeScript project. Only the xterm buffer is a fixture.
    const { serializeRenderedBuffer } = await vi.importActual<{
      serializeRenderedBuffer: (term: unknown, maxLines: number) => string
    }>('../../renderer/src/platform/terminal/composables/useTerminal')
    const rendered = ['earlier output', `I4T:${nonce}:46`, '']
    const term = { buffer: { active: { baseY: 2, cursorY: 0,
      getLine: (row: number) => rendered[row] === undefined ? undefined : {
        translateToString: () => rendered[row],
      },
    } } }
    // Matches useTerminal.readRenderedText's actual delegation and mandatory argument.
    const getter = vi.fn((maxLines: number) => serializeRenderedBuffer(term, maxLines))
    const component = { type: { __name: 'TerminalPane' }, props: { paneId: 'pane-1' }, exposed: {
      readRenderedText: getter, sessionId: { value: 'live-session' },
      get cleanBuffer(): never { throw new Error('not render provenance') },
    } }
    const element = { isConnected: true, getAttribute: () => 'pane-1', __vueParentComponent: component }
    const frame = { frameTreeNodeId: 10, processId: 20, routingId: 30, detached: false,
      isDestroyed: () => false, url: 'file:///main.html',
      executeJavaScript: vi.fn(async (script: string, gesture: boolean) => {
        expect(gesture).toBe(false)
        return runInNewContext(script, { document: { querySelectorAll: () => [element] } })
      }),
    }
    const window = { id: 1, isDestroyed: () => false, once: vi.fn(), webContents: {
      id: 2, mainFrame: frame, isDestroyed: () => false, on: vi.fn(),
    } }
    desktopHostEvaluation.registerMain(window as never, () => workspace)
    expect(await desktopHostEvaluation.snapshotTerminal(99, 'pane-1', nonce)).toBeNull()
    expect(await desktopHostEvaluation.snapshotTerminal(1, 'wrong-pane', nonce)).toBeNull()
    expect(await desktopHostEvaluation.snapshotTerminal(1, 'pane-1', nonce)).toMatchObject({ markerVisible: true })
    expect(getter).toHaveBeenCalledTimes(1)
    expect(getter).toHaveBeenLastCalledWith(256)
    getter.mockReturnValue('')
    expect(await desktopHostEvaluation.snapshotTerminal(1, 'pane-1', nonce)).toMatchObject({ markerVisible: false })
    component.exposed.sessionId.value = ''
    expect(await desktopHostEvaluation.snapshotTerminal(1, 'pane-1', nonce)).toBeNull()
    component.exposed.sessionId.value = 'live-session'
    getter.mockReturnValue(`I4T:${nonce}:46\n`)
    for (let index = 2; index < 2000; index++) {
      expect(await desktopHostEvaluation.snapshotTerminal(1, 'pane-1', nonce)).not.toBeNull()
    }
    expect(await desktopHostEvaluation.snapshotTerminal(1, 'pane-1', nonce)).toBeNull()
    const bytes = readFileSync(join(root, 'logs', 'observer.jsonl'))
    const lines = bytes.toString().trim().split('\n')
    expect(lines).toHaveLength(2000)
    expect(lines.every((line) => Buffer.byteLength(line + '\n') <= 8192)).toBe(true)
    expect(bytes.length).toBeLessThanOrEqual(16 * 1024 * 1024)
  })

  it('rejects snapshots when workspace/frame identity changes during the asynchronous read', async () => {
    const root = evaluationRoot()
    const a = join(root, 'A'), b = join(root, 'B')
    mkdirSync(a); mkdirSync(b)
    let workspace = a
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    const frame = { frameTreeNodeId: 10, processId: 20, routingId: 30, detached: false,
      isDestroyed: () => false, url: 'file:///main.html', executeJavaScript: vi.fn(async () => {
        workspace = b
        return { paneId: 'pane-1', sessionId: 'live-session', markerVisible: true }
      }),
    }
    const window = { id: 1, isDestroyed: () => false, once: vi.fn(), webContents: {
      id: 2, mainFrame: frame, isDestroyed: () => false, on: vi.fn(),
    } }
    desktopHostEvaluation.registerMain(window as never, () => workspace)
    expect(await desktopHostEvaluation.snapshotTerminal(1, 'pane-1', '0123456789abcdef')).toBeNull()
    expect(frame.executeJavaScript).toHaveBeenCalledTimes(1)
  })

  it('rejects wrong or changing Plans tuples, frame identities and unbounded/escaping DOM rows', async () => {
    const root = evaluationRoot(), workspace = join(root, 'A')
    mkdirSync(workspace)
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    const binding = { id: 'frame-1', artifactId: 'artifact', contributionKey: 'navide.plans.left', instanceId: 'instance',
      packageId: 'navide.plans', packageVersion: '0.2.9', receiverGeneration: 'generation', receiverWebContentsId: 11,
      workspacePath: workspace, entryUrl: 'navide-plugin-frame://host/index.html', frameTreeNodeId: 10,
      documentGeneration: 1, state: 'active' as const }
    let current = { ...binding }
    const rows = [{ name: 'Fresh plan', relPath: '.agent-team/plans/fresh.html' }]
    const frame = { frameTreeNodeId: 10, processId: 20, routingId: 30, detached: false, parent: {},
      isDestroyed: () => false, url: binding.entryUrl,
      executeJavaScript: vi.fn(async (_script: string, gesture: boolean): Promise<unknown> => {
        expect(gesture).toBe(false)
        return rows
      }),
    }
    const snapshot = () => desktopHostEvaluation.snapshotPlans(binding, 1, frame as never, () => current, 'list-request')
    expect(await snapshot()).toMatchObject({ rows })
    current = { ...binding, instanceId: 'different' }
    expect(await snapshot()).toBeNull()
    expect(frame.executeJavaScript).toHaveBeenCalledTimes(1)
    current = { ...binding }
    frame.executeJavaScript.mockImplementationOnce(async () => {
      current = { ...binding, documentGeneration: 2 }
      return rows
    })
    expect(await snapshot()).toBeNull()
    current = { ...binding }
    frame.executeJavaScript.mockResolvedValueOnce([{ name: 'Fake', relPath: '../outside.html' }])
    expect(await snapshot()).toBeNull()
    frame.executeJavaScript.mockResolvedValueOnce(Array.from({ length: 17 }, () => rows[0]))
    expect(await snapshot()).toBeNull()
    frame.detached = true
    expect(await snapshot()).toBeNull()
  })

  it.each([
    ['compact recent duplicates alongside full rows',
      '<div class="plan-row plan-row--compact"><span class="plan-row-title plan-row-name">Fresh plan</span></div>' +
      '<div class="plan-row plan-row--compact"><span class="plan-row-title plan-row-name">Recent only</span></div>' +
      '<button class="plan-row"><span class="plan-row-title plan-row-name">Fresh plan</span><span class="plan-row-path">.agent-team/plans/fresh.html</span></button>',
      [{ name: 'Fresh plan', relPath: '.agent-team/plans/fresh.html' }]],
    ['a full row with missing path',
      '<button class="plan-row"><span class="plan-row-name">Fresh plan</span></button>', null],
    ['a full row with ambiguous names',
      '<button class="plan-row"><span class="plan-row-name">Fresh plan</span><span class="plan-row-name">Other</span><span class="plan-row-path">.agent-team/plans/fresh.html</span></button>', null],
    ['a full row with ambiguous paths',
      '<button class="plan-row"><span class="plan-row-name">Fresh plan</span><span class="plan-row-path">.agent-team/plans/fresh.html</span><span class="plan-row-path">.agent-team/plans/other.html</span></button>', null],
    ['ambiguous sidebar lists', '<div class="plans-sidebar-list"></div>', null],
    ['more than sixteen full rows',
      '<button class="plan-row"><span class="plan-row-name">Fresh plan</span><span class="plan-row-path">.agent-team/plans/fresh.html</span></button>'.repeat(17), null],
  ] as const)('executes the fixed Plans DOM script against %s', async (_case, markup, expected) => {
    const root = evaluationRoot(), workspace = join(root, 'A')
    mkdirSync(workspace)
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    const binding = { id: 'frame-1', artifactId: 'artifact', contributionKey: 'navide.plans.left', instanceId: 'instance',
      packageId: 'navide.plans', packageVersion: '0.2.9', receiverGeneration: 'generation', receiverWebContentsId: 11,
      workspacePath: workspace, entryUrl: 'navide-plugin-frame://host/index.html', frameTreeNodeId: 10,
      documentGeneration: 1, state: 'active' as const }
    const dom = new Window()
    // Inert, source-derived PlansApp markup: no plugin code, actions or network.
    dom.document.body.innerHTML = `<div class="plans-left-surface"><div class="plans-sidebar-list">${markup}</div></div>`
    const frame = { frameTreeNodeId: 10, processId: 20, routingId: 30, detached: false, parent: {},
      isDestroyed: () => false, url: binding.entryUrl,
      executeJavaScript: vi.fn(async (script: string, gesture: boolean) => {
        expect(gesture).toBe(false)
        return runInNewContext(script, { document: dom.document })
      }),
    }
    const snapshot = await desktopHostEvaluation.snapshotPlans(binding, 1, frame as never, () => binding, 'list-request')
    expect(frame.executeJavaScript).toHaveBeenCalledTimes(1)
    if (expected === null) expect(snapshot).toBeNull()
    else expect(snapshot).toMatchObject({ rows: expected })
  })

  it.each([
    ['released array result', [{ meta: { name: 'Fresh plan' }, rel_path: '.agent-team/plans/fresh.html' }],
      [{ name: 'Fresh plan', relPath: '.agent-team/plans/fresh.html' }]],
    ['empty array result', [], []],
    ['non-array legacy wrapper', { plans: [{ meta: { name: 'Fresh plan' }, rel_path: '.agent-team/plans/fresh.html' }] }, null],
    ['null result', null, null],
    ['over-cap array', Array.from({ length: 17 }, () => ({ meta: { name: 'Fresh plan' }, rel_path: '.agent-team/plans/fresh.html' })), null],
    ['missing metadata name', [{ rel_path: '.agent-team/plans/fresh.html' }], null],
    ['escaping path', [{ meta: { name: 'Fresh plan' }, rel_path: '../outside.html' }], null],
  ])('observes %s without modifying the backend response', async (_case, result, expected) => {
    const root = evaluationRoot(), workspace = join(root, 'A')
    mkdirSync(workspace)
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    const binding = { id: 'frame-1', artifactId: 'artifact', contributionKey: 'navide.plans.left', instanceId: 'instance',
      packageId: 'navide.plans', packageVersion: '0.2.9', receiverGeneration: 'generation', receiverWebContentsId: 11,
      workspacePath: workspace, entryUrl: 'navide-plugin-frame://host/index.html', frameTreeNodeId: 10,
      documentGeneration: 1, state: 'active' as const }
    const response = { reqId: 'backend-request', ok: true, result }
    const before = structuredClone(response)
    desktopHostEvaluation.observeFrame(binding, 1, 'response', 'plugin:backend:call',
      { name: 'plans.list', args: {}, reqId: 'backend-request' }, 'frame-request', response)
    const lines = readFileSync(join(root, 'logs', 'observer.jsonl'), 'utf8').trim().split('\n')
    expect(lines).toHaveLength(1)
    expect(Buffer.byteLength(lines[0] + '\n')).toBeLessThanOrEqual(8192)
    expect(JSON.parse(lines[0])).toMatchObject({ phase: 'response', operation: 'plans.list', ok: true, resultPlans: expected })
    expect(response).toEqual(before)
  })

  // Validator proofs for ticket line 8 (not live evidence): stale or wrong-instance tuples, uncorrelated responses, backend-only output, an acknowledgement
  // and a ready flag cannot produce an observer row that the driver could use as endpoint evidence.
  async function observerFixture() {
    const root = evaluationRoot(), workspace = join(root, 'A')
    mkdirSync(workspace)
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    const binding = { id: 'frame-1', artifactId: 'artifact', contributionKey: 'navide.plans.left', instanceId: 'instance',
      packageId: 'navide.plans', packageVersion: '0.2.9', receiverGeneration: 'generation', receiverWebContentsId: 11,
      workspacePath: workspace, entryUrl: 'navide-plugin-frame://host/index.html', frameTreeNodeId: 10,
      documentGeneration: 1, state: 'active' as const }
    const rows = (): Array<Record<string, unknown>> => {
      try { return readFileSync(join(root, 'logs', 'observer.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map((line) => JSON.parse(line)) } catch { return [] }
    }
    return { root, workspace, desktopHostEvaluation, binding, rows }
  }

  it.each([
    ['a revoked (stale) frame binding', { state: 'revoked' }],
    ['a stale document generation of zero', { documentGeneration: 0 }],
    ['a non-integer frame tree node', { frameTreeNodeId: 1.5 }],
    ['a workspace outside the OWN root', { workspacePath: '/tmp' }],
    ['a relative workspace path', { workspacePath: 'A' }],
    ['an illegal package id', { packageId: '../navide.plans' }],
    ['an illegal package version', { packageVersion: '0.2.9 ' }],
  ])('writes no observer row for %s (stale / wrong-instance evidence), while the genuine binding writes one', async (_name, patch) => {
    const { desktopHostEvaluation, binding, rows } = await observerFixture()
    desktopHostEvaluation.observeFrame({ ...binding, ...patch } as never, 1, 'request', 'plugin:backend:call', { name: 'plans.list', reqId: 'r' }, 'q')
    expect(rows()).toEqual([])
    desktopHostEvaluation.observeFrame({ ...binding, ...patch } as never, 1.5, 'request', 'plugin:backend:call', { name: 'plans.list', reqId: 'r' }, 'q')
    expect(rows()).toEqual([])
    desktopHostEvaluation.observeFrame(binding as never, 1, 'request', 'plugin:backend:call', { name: 'plans.list', reqId: 'r' }, 'q')
    expect(rows()).toHaveLength(1)
    expect(rows()[0]).toMatchObject({ phase: 'request', operation: 'plans.list', windowId: 1, documentGeneration: 1, packageVersion: '0.2.9' })
  })

  it('rejects a DOM snapshot when the frame was renewed (stale document generation) or the binding is no longer active', async () => {
    const { desktopHostEvaluation, binding, rows } = await observerFixture()
    const dom = new Window()
    dom.document.body.innerHTML = '<div class="plans-left-surface"><div class="plans-sidebar-list"></div></div>'
    const frame = { frameTreeNodeId: 10, processId: 20, routingId: 30, detached: false, parent: {}, isDestroyed: () => false, url: binding.entryUrl,
      executeJavaScript: vi.fn(async (script: string) => runInNewContext(script, { document: dom.document })) }
    const snapshot = (current: unknown) => desktopHostEvaluation.snapshotPlans(binding as never, 1, frame as never, () => current as never, 'list-request')
    expect(await snapshot({ ...binding, documentGeneration: 2 })).toBeNull()
    expect(await snapshot({ ...binding, state: 'revoked' })).toBeNull()
    expect(await snapshot({ ...binding, instanceId: 'other-instance' })).toBeNull()
    expect(await snapshot(null)).toBeNull()
    expect(rows().filter((row) => row.phase === 'plans-dom')).toEqual([])
    expect(await snapshot(binding)).toMatchObject({ rows: [] })   // positive control: the genuine current binding
  })

  it('records request, backend and response ids as separate hashes so a wrong response correlation stays visible, and never writes the raw ids', async () => {
    const { root, desktopHostEvaluation, binding, rows } = await observerFixture()
    const call = { name: 'plans.list', args: {}, reqId: 'backend-request-A' }
    desktopHostEvaluation.observeFrame(binding as never, 1, 'response', 'plugin:backend:call', call, 'frame-request-A', { reqId: 'backend-request-A', ok: true, result: [] })
    desktopHostEvaluation.observeFrame(binding as never, 1, 'response', 'plugin:backend:call', call, 'frame-request-A', { reqId: 'backend-request-OTHER', ok: true, result: [] })
    desktopHostEvaluation.observeFrame(binding as never, 1, 'response', 'plugin:backend:call', call, 'frame-request-A', { ok: true, result: [] })
    const [matched, wrong, missing] = rows()
    expect(matched!.backendRequestIdHash).toMatch(/^[0-9a-f]{64}$/)
    expect(matched!.backendRequestIdHash).toBe(matched!.responseRequestIdHash)
    expect(wrong!.backendRequestIdHash).toMatch(/^[0-9a-f]{64}$/)
    expect(wrong!.responseRequestIdHash).toMatch(/^[0-9a-f]{64}$/)
    expect(wrong!.backendRequestIdHash).not.toBe(wrong!.responseRequestIdHash)   // the driver's equality check (humanMount) rejects this row
    expect(missing!.responseRequestIdHash).toBeNull()
    expect(matched!.requestIdHash).toBe(wrong!.requestIdHash)
    const text = readFileSync(join(root, 'logs', 'observer.jsonl'), 'utf8')
    for (const raw of ['backend-request-A', 'backend-request-OTHER', 'frame-request-A']) expect(text).not.toContain(raw)
  })

  it('produces no DOM or terminal evidence from backend output, an acknowledgement or a ready flag', async () => {
    const { desktopHostEvaluation, binding, rows } = await observerFixture()
    const call = { name: 'plans.list', args: {}, reqId: 'r', ready: true, status: 'ready' }
    // backend-only: a genuine list result, but nothing reads the consumer DOM
    desktopHostEvaluation.observeFrame(binding as never, 1, 'response', 'plugin:backend:call', call, 'q',
      { reqId: 'r', ok: true, result: [{ meta: { name: 'Fresh plan' }, rel_path: '.agent-team/plans/fresh.html' }], ready: true })
    // acknowledgement: ok with no result list, with a ready flag; an error response with a code outside the public set
    desktopHostEvaluation.observeFrame(binding as never, 1, 'response', 'plugin:backend:call', call, 'q', { reqId: 'r', ok: true, ack: true, ready: true })
    desktopHostEvaluation.observeFrame(binding as never, 1, 'response', 'plugin:backend:subscribe', { subscriptionId: 's', ready: true }, 'q', { ok: true, ready: true })
    desktopHostEvaluation.observeFrame(binding as never, 1, 'response', 'plugin:backend:call', call, 'q', { reqId: 'r', ok: false, error: { code: 'ALL_GOOD_READY' } })
    const written = rows()
    expect(written).toHaveLength(4)
    expect(written.map((row) => row.phase)).toEqual(['response', 'response', 'response', 'response'])
    expect(written.some((row) => row.phase === 'plans-dom' || row.phase === 'terminal')).toBe(false)   // only snapshotPlans / snapshotTerminal write consumer evidence
    expect(written[1]!.resultPlans).toBeNull()        // an acknowledgement yields no list
    expect(written[2]!.event).toBeNull()              // a bare subscribe acknowledgement carries no plans.changed event
    expect(written[3]!.errorCode).toBeNull()          // an unknown error code is not recorded
    for (const row of written) {
      for (const key of ['ready', 'status', 'ack', 'backendReady']) expect(Object.keys(row)).not.toContain(key)   // no flag from payload or response is copied into the receipt
      expect(Object.values(row)).not.toContain('ready')
    }
    // the terminal getter is only read through snapshotTerminal; without a registered MAIN pane it records nothing
    expect(await desktopHostEvaluation.snapshotTerminal(1, 'pane-1', '0123456789abcdef')).toBeNull()
    expect(rows().filter((row) => row.phase === 'terminal-xterm' || row.phase === 'terminal')).toEqual([])
  })

  it('records the host close decision as an enum-only receipt and drops anything else', async () => {
    const root = evaluationRoot()
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    desktopHostEvaluation.observeCloseDecision('begin-busy-lock', 'native-window-close')
    desktopHostEvaluation.observeCloseDecision('receiver-busy', 'quit')
    desktopHostEvaluation.observeCloseDecision('provider-refused', 'reload')
    desktopHostEvaluation.observeCloseDecision('timeout', 'receiver-item-batch')
    desktopHostEvaluation.observeCloseDecision('accepted', 'native-window-close')
    desktopHostEvaluation.observeCloseDecision('busy', 'native-window-close')
    desktopHostEvaluation.observeCloseDecision('accepted', '/Users/someone/secret')
    desktopHostEvaluation.observeCloseDecision({ secret: 'x' } as never, 'quit')
    const rows = readFileSync(join(root, 'logs', 'observer.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
    expect(rows).toEqual([
      { sequence: 1, phase: 'close-decision', outcome: 'begin-busy-lock', reason: 'native-window-close' },
      { sequence: 2, phase: 'close-decision', outcome: 'receiver-busy', reason: 'quit' },
      { sequence: 3, phase: 'close-decision', outcome: 'provider-refused', reason: 'reload' },
      { sequence: 4, phase: 'close-decision', outcome: 'timeout', reason: 'receiver-item-batch' },
      { sequence: 5, phase: 'close-decision', outcome: 'accepted', reason: 'native-window-close' },
    ])
    expect(JSON.stringify(rows)).not.toContain('secret')
  })

  it('records the quit preparation outcome as an enum-only receipt and drops anything else', async () => {
    const root = evaluationRoot()
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    desktopHostEvaluation.observeQuitPrepare('reentrant-pending')
    desktopHostEvaluation.observeQuitPrepare(false)
    desktopHostEvaluation.observeQuitPrepare(true)
    desktopHostEvaluation.observeQuitPrepare('not-required')
    desktopHostEvaluation.observeQuitPrepare('busy' as never)
    desktopHostEvaluation.observeQuitPrepare({ secret: 'x' } as never)
    const rows = readFileSync(join(root, 'logs', 'observer.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
    expect(rows).toEqual([
      { sequence: 1, phase: 'quit-prepare', outcome: 'reentrant-pending' },
      { sequence: 2, phase: 'quit-prepare', outcome: 'false' },
      { sequence: 3, phase: 'quit-prepare', outcome: 'true' },
      { sequence: 4, phase: 'quit-prepare', outcome: 'not-required' },
    ])
  })

  it('does not record a quit preparation receipt when the evaluation is disabled', async () => {
    vi.stubGlobal('__NAVIDE_DESKTOP_HOST_EVALUATION__', false)
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    expect(() => desktopHostEvaluation.observeQuitPrepare(true)).not.toThrow()
    expect(() => desktopHostEvaluation.observeCloseDecision('accepted', 'quit')).not.toThrow()
    expect(electron.setPath).not.toHaveBeenCalled()
  })

  it('does not activate or touch Electron state from runtime environment alone', async () => {
    vi.stubGlobal('__NAVIDE_DESKTOP_HOST_EVALUATION__', false)
    vi.stubEnv('NAVIDE_DESKTOP_HOST_EVALUATION', '1')
    vi.stubEnv('NAVIDE_DESKTOP_HOST_EVALUATION_ROOT', join(tmpdir(), 'runtime-env-alone'))
    const { desktopHostEvaluation } = await import('./desktopHostEvaluation')
    expect(desktopHostEvaluation.enabled).toBe(false)
    desktopHostEvaluation.registerMain({} as never, () => null)
    expect(await desktopHostEvaluation.snapshotTerminal(1, 'pane', '0123456789abcdef')).toBeNull()
    expect(electron.setPath).not.toHaveBeenCalled()
    expect(electron.getPath).not.toHaveBeenCalled()
  })
})
