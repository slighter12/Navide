// @vitest-environment happy-dom
//
// Agent CLI Runtime loss with the WebSocket still up arrives as a plain
// `terminal.exit` whose reason is `runtime_lost`. The pane must hand that loss
// to the existing conversation-recovery owner exactly like a PTY that did not
// survive a reconnect, and must do nothing else on its own.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { createMockBackend, withScope, flush } from './mockBackend'

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

import { useTerminal } from '../useTerminal'

async function spawnedPane(onPtyLostWhileDisconnected: () => void, agentKey = 'claude') {
  const mock = createMockBackend()
  mock.setResponse('terminal.create', { terminal_session_id: 'sess-1', pid: 42 })
  const { result, scope } = withScope(() => useTerminal('pane-1', mock.backend, { onPtyLostWhileDisconnected }))
  result.mount(document.createElement('div'))
  await result.spawn({ command: 'bash', cwd: '/tmp', skipReattach: true, agentKey })
  await flush()
  return { mock, result, scope }
}

const sentRequests = (mock: ReturnType<typeof createMockBackend>) =>
  mock.sent.map((s) => s.type).filter((t) => t !== 'terminal.create')

describe('useTerminal — Agent CLI Runtime loss presented as terminal.exit(runtime_lost)', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    store.clear()
    writes.length = 0
  })

  it('reports the lost PTY to the recovery owner once and marks the pane exited', async () => {
    const lost = vi.fn()
    const { mock, result, scope } = await spawnedPane(lost)
    mock.emit('terminal.exit', { terminal_session_id: 'sess-1', reason: 'runtime_lost', exit_code: null, signal: null })
    await flush()
    expect(lost).toHaveBeenCalledTimes(1)
    expect(result.status.value).toBe('exited')
    scope.stop()
  })

  it('does not replay input or create anything by itself', async () => {
    const lost = vi.fn()
    const { mock, scope } = await spawnedPane(lost)
    const before = sentRequests(mock)
    mock.emit('terminal.exit', { terminal_session_id: 'sess-1', reason: 'runtime_lost', exit_code: null, signal: null })
    await flush()
    expect(sentRequests(mock)).toEqual(before)
    expect(mock.sent.filter((s) => s.type === 'terminal.create')).toHaveLength(1)
    scope.stop()
  })

  it.each(['exit', 'killed', 'shutdown', 'error'])('does not treat an ordinary %s exit as a lost runtime', async (reason) => {
    const lost = vi.fn()
    const { mock, scope } = await spawnedPane(lost)
    mock.emit('terminal.exit', { terminal_session_id: 'sess-1', reason, exit_code: 0, signal: null, uptime_ms: 60_000 })
    await flush()
    expect(lost).not.toHaveBeenCalled()
    scope.stop()
  })

  it('ignores a loss reported for another terminal session', async () => {
    const lost = vi.fn()
    const { mock, result, scope } = await spawnedPane(lost)
    mock.emit('terminal.exit', { terminal_session_id: 'someone-else', reason: 'runtime_lost', exit_code: null, signal: null })
    await flush()
    expect(lost).not.toHaveBeenCalled()
    expect(result.status.value).toBe('running')
    scope.stop()
  })
})
