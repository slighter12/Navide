// This module must be the first side-effect import of the main entrypoint.
import { app, type BrowserWindow, type WebFrameMain } from 'electron'
import { closeSync, constants, fstatSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync, writeSync } from 'node:fs'
import { posix, win32, join, isAbsolute, relative, sep } from 'node:path'
import { createHash } from 'node:crypto'
import type { PluginFrameBinding } from '../plugins/pluginFrameBinding'

declare const __NAVIDE_DESKTOP_HOST_EVALUATION__: boolean

export function evaluationPaths(root: string, platform: NodeJS.Platform) {
  const paths = platform === 'win32' ? win32 : posix
  if (!paths.isAbsolute(root)) throw new Error('invalid evaluation root')
  return {
    home: paths.join(root, 'home'), appData: paths.join(root, 'app-data'),
    userData: paths.join(root, 'user-data'), sessionData: paths.join(root, 'session-data'),
    temp: paths.join(root, 'tmp'), logs: paths.join(root, 'logs'), crashDumps: paths.join(root, 'crashes'),
  }
}

function bootstrap(): string | null {
  // The bundler emits a literal false in normal builds; runtime env cannot enable it.
  if (typeof __NAVIDE_DESKTOP_HOST_EVALUATION__ === 'undefined' || !__NAVIDE_DESKTOP_HOST_EVALUATION__) return null
  const root = process.env['NAVIDE_DESKTOP_HOST_EVALUATION_ROOT']
  if (!root || !isAbsolute(root)) throw new Error('invalid evaluation root')
  const owned = (path: string): void => {
    const stat = lstatSync(path)
    if (!stat.isDirectory() || stat.isSymbolicLink() ||
      (process.getuid && (stat.uid !== process.getuid() || (stat.mode & 0o077) !== 0))) {
      throw new Error('invalid evaluation root ownership')
    }
  }
  owned(root)
  if (realpathSync(root) !== root) throw new Error('noncanonical evaluation root')
  const fd = openSync(join(root, 'desktop-host-evaluation.json'), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK)
  try {
    const before = fstatSync(fd)
    if (!before.isFile() || before.size > 1024) throw new Error('invalid evaluation root marker')
    const marker = JSON.parse(readFileSync(fd, 'utf8')) as Record<string, unknown>
    const after = fstatSync(fd)
    if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size ||
      before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs ||
      marker.schemaVersion !== 1 || typeof marker.runId !== 'string' || !/^[0-9a-f]{16}$/.test(marker.runId)) {
      throw new Error('invalid evaluation root marker')
    }
  } finally { closeSync(fd) }
  const paths = evaluationPaths(root, process.platform)
  for (const path of Object.values(paths)) {
    mkdirSync(path, { recursive: true, mode: 0o700 })
    owned(path)
  }
  for (const [name, path] of Object.entries(paths)) app.setPath(name as keyof typeof paths, path)
  return root
}

// Runs synchronously before any app-state module can resolve a production path.
const root = bootstrap()
type Phase = 'admission' | 'request' | 'response' | 'event' | 'revocation'
type Main = { window: BrowserWindow; workspace: () => string | null; generation: number }
type PlanRow = { name: string; relPath: string }
const mains = new Map<number, Main>()
const hash = (value: string): string => createHash('sha256').update(value).digest('hex')
const object = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
let receiptFd: number | null = null
let receiptCount = 0

function ownWorkspace(value: string | null): boolean {
  if (!root || !value || !isAbsolute(value)) return false
  const child = relative(root, value)
  return child !== '' && child !== '..' && !child.startsWith(`..${sep}`) && !isAbsolute(child) && realpathSync(value) === value
}

const CLOSE_DECISION_OUTCOMES: ReadonlySet<string> = new Set([
  'begin-busy-lock', 'begin-busy-pending-target', 'receiver-busy', 'receiver-refused',
  'provider-busy', 'provider-refused', 'timeout', 'unavailable', 'accepted',
])
const CLOSE_DECISION_REASONS: ReadonlySet<string> = new Set(['native-window-close', 'reload', 'quit', 'receiver-item-batch'])

function record(value: Record<string, unknown>): boolean {
  if (!root || receiptCount >= 2000) return false
  try {
    const line = JSON.stringify({ sequence: receiptCount + 1, ...value }) + '\n'
    if (Buffer.byteLength(line) > 8192) return false
    receiptFd ??= openSync(join(root, 'logs', 'observer.jsonl'), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600)
    writeSync(receiptFd, line)
    receiptCount++
    return true
  } catch { return false } // Observation must never alter admission or delivery.
}

function tuple(binding: PluginFrameBinding, windowId: number): Record<string, unknown> | null {
  if (!ownWorkspace(binding.workspacePath) || binding.state !== 'active' ||
    !Number.isInteger(windowId) || !Number.isInteger(binding.frameTreeNodeId) ||
    !Number.isInteger(binding.documentGeneration) || !binding.documentGeneration ||
    !/^[A-Za-z0-9._-]{1,128}$/.test(binding.packageId) || !/^[A-Za-z0-9._+-]{1,128}$/.test(binding.packageVersion)) return null
  return { windowId, receiverWebContentsId: binding.receiverWebContentsId,
    frameTreeNodeId: binding.frameTreeNodeId, documentGeneration: binding.documentGeneration,
    packageId: binding.packageId, packageVersion: binding.packageVersion,
    workspacePath: binding.workspacePath, contributionHash: hash(binding.contributionKey),
    artifactHash: hash(binding.artifactId), instanceHash: hash(binding.instanceId),
    receiverGenerationHash: hash(binding.receiverGeneration), bindingHash: hash(binding.id) }
}

function operation(payload: unknown): string | null {
  const body = object(payload)
  if (body?.ns === 'shell' && body.method === 'run') return 'shell.run'
  return body?.name === 'plans.list' || body?.name === 'plans.watch' ? body.name : null
}

function planRows(value: unknown): PlanRow[] | null {
  if (!Array.isArray(value) || value.length > 16) return null
  const rows: PlanRow[] = []
  for (const valueRow of value) {
    const row = object(valueRow)
    if (!row || typeof row.name !== 'string' || !/^[\x20-\x7e]{1,128}$/.test(row.name) ||
      typeof row.relPath !== 'string' || !/^[A-Za-z0-9._/-]{1,256}$/.test(row.relPath) ||
      row.relPath.startsWith('/') || row.relPath.split('/').includes('..')) return null
    rows.push({ name: row.name, relPath: row.relPath })
  }
  return rows
}

function frameIdentity(frame: WebFrameMain): string | null {
  if (frame.isDestroyed() || frame.detached || !Number.isInteger(frame.frameTreeNodeId) ||
    !Number.isInteger(frame.processId) || !Number.isInteger(frame.routingId)) return null
  return JSON.stringify([frame.frameTreeNodeId, frame.processId, frame.routingId, frame.url])
}

function sameBinding(a: PluginFrameBinding, b: PluginFrameBinding | null): boolean {
  const keys: Array<keyof PluginFrameBinding> = ['id', 'artifactId', 'instanceId', 'packageId', 'packageVersion',
    'contributionKey', 'workspacePath', 'receiverGeneration', 'receiverWebContentsId', 'entryUrl',
    'frameTreeNodeId', 'documentGeneration', 'state']
  return b !== null && keys.every((key) => a[key] === b[key])
}

export const desktopHostEvaluation = {
  enabled: root !== null,
  registerMain(window: BrowserWindow, workspace: () => string | null): void {
    if (!root) return
    const main = { window, workspace, generation: 0 }, windowId = window.id
    mains.set(windowId, main)
    window.once('closed', () => { if (mains.get(windowId) === main) mains.delete(windowId) })
    window.webContents.on('did-start-navigation', (_event, _url, inPlace, isMainFrame) => {
      if (isMainFrame && !inPlace) main.generation++
    })
  },

  observeFrame(binding: PluginFrameBinding, windowId: number, phase: Phase, channel = '', payload?: unknown, requestId?: string, response?: unknown): void {
    if (!root) return
    try {
      const identity = tuple(binding, windowId)
      if (!identity) return
      const knownChannel = ['plugin:cap:call', 'plugin:backend:call', 'plugin:backend:subscribe',
        'plugin:backend:event', 'plugin:frame:revoked'].includes(channel) ? channel : null
      const result = object(response)
      const error = object(result?.error)
      const publicCodes = ['CAPABILITY_DENIED', 'METHOD_NOT_FOUND', 'INVALID_ARGUMENT', 'WORKSPACE_SCOPE_VIOLATION',
        'USER_CANCELLED', 'TIMEOUT', 'BACKEND_UNAVAILABLE', 'PLUGIN_STOPPING', 'STORAGE_QUOTA_EXCEEDED',
        'RESOURCE_LIMIT', 'INTERNAL_ERROR', 'BAD_REQUEST']
      const errorCode = typeof error?.code === 'string' && publicCodes.includes(error.code) ? error.code : null
      const body = object(payload), resultPlans = result?.result
      // Selected fixtures require actual metadata names, not a filename fallback.
      const plans = operation(payload) === 'plans.list' && result?.ok === true && Array.isArray(resultPlans) && resultPlans.length <= 16
        ? planRows(resultPlans.map((value) => {
          const plan = object(value)
          return { name: object(plan?.meta)?.name, relPath: plan?.rel_path }
        })) : null
      record({ ...identity, phase, channel: knownChannel, operation: operation(payload), resultPlans: plans,
        requestIdHash: requestId ? hash(requestId) : null,
        backendRequestIdHash: typeof body?.reqId === 'string' ? hash(body.reqId) : null,
        responseRequestIdHash: typeof result?.reqId === 'string' ? hash(result.reqId) : null,
        ok: typeof result?.ok === 'boolean' ? result.ok : null, errorCode,
        event: body?.event === 'plans.changed' || body?.type === 'plans.changed' ? 'plans.changed' : null,
        subscriptionIdHash: typeof body?.subscriptionId === 'string' ? hash(body.subscriptionId) : null })
    } catch { /* Passive and fail closed; never propagate observer errors. */ }
  },

  /** Passive, enum-only record of the app-quit window preparation (before-quit): the actual prepareForQuit boolean, a re-entrant before-quit while a
   *  preparation is pending, or no preparation needed. Never alters the quit; unknown values are dropped. */
  observeQuitPrepare(outcome: boolean | 'reentrant-pending' | 'not-required'): void {
    if (!root) return
    const value = outcome === true ? 'true' : outcome === false ? 'false' : outcome
    if (value !== 'true' && value !== 'false' && value !== 'reentrant-pending' && value !== 'not-required') return
    record({ phase: 'quit-prepare', outcome: value })
  },

  /** Passive, enum-only record of one host close-preparation decision (native window close, reload, quit, or a receiver item batch): which path
   *  answered busy, refused, timed out or accepted. Never alters the close; unknown values are dropped; no identity, path or payload is recorded. */
  observeCloseDecision(outcome: string, reason: string): void {
    if (!root) return
    if (!CLOSE_DECISION_OUTCOMES.has(outcome) || !CLOSE_DECISION_REASONS.has(reason)) return
    record({ phase: 'close-decision', outcome, reason })
  },

  async snapshotTerminal(windowId: number, paneId: string, nonce: string) {
    if (!root || !/^[A-Za-z0-9:_-]{1,128}$/.test(paneId) || !/^[0-9a-f]{16}$/.test(nonce)) return null
    try {
      const main = mains.get(windowId)
      if (!main || main.window.isDestroyed() || main.window.webContents.isDestroyed()) return null
      const workspace = main.workspace(), generation = main.generation
      if (!ownWorkspace(workspace)) return null
      const frame = main.window.webContents.mainFrame, before = frameIdentity(frame)
      if (!before) return null
      // Fixed read-only program. No arbitrary JS, caller flags, gesture or private fallback.
      const value: unknown = await frame.executeJavaScript(`(() => {
        const paneId = ${JSON.stringify(paneId)}, marker = ${JSON.stringify(`I4T:${nonce}:46`)};
        const elements = [...document.querySelectorAll('.xterm-host[data-pane-id]')].filter(e => e.getAttribute('data-pane-id') === paneId);
        if (elements.length !== 1 || !elements[0].isConnected) return null;
        const component = elements[0].__vueParentComponent;
        if (component?.type?.__name !== 'TerminalPane' || component.props?.paneId !== paneId) return null;
        const exposed = component.exposed;
        const sessionId = exposed?.sessionId?.value;
        if (typeof sessionId !== 'string' || !sessionId || typeof exposed?.readRenderedText !== 'function') return null;
        const text = exposed.readRenderedText(256);
        if (typeof text !== 'string' || text.length > 262144) return null;
        const line = text.split(/\\r?\\n/).find(line => line.startsWith(marker) && /^ *$/.test(line.slice(23)));
        return { paneId, sessionId, renderedMarker: line ? line.slice(0, 23) : null };
      })()`, false)
      if (mains.get(windowId) !== main || main.window.isDestroyed() || main.window.webContents.isDestroyed() ||
        main.workspace() !== workspace || main.generation !== generation ||
        main.window.webContents.mainFrame !== frame || frameIdentity(frame) !== before) return null
      const data = object(value)
      if (!data || data.paneId !== paneId || typeof data.sessionId !== 'string' || !data.sessionId || data.sessionId.length > 256 ||
        (data.renderedMarker !== null && data.renderedMarker !== `I4T:${nonce}:46`)) return null
      const snapshot = { windowId, webContentsId: main.window.webContents.id, frameIdentityHash: hash(before),
        documentGeneration: generation, workspacePath: workspace, paneId, sessionIdHash: hash(data.sessionId),
        renderedMarker: data.renderedMarker, markerVisible: data.renderedMarker !== null }
      return record({ phase: 'terminal-dom', ...snapshot }) ? snapshot : null
    } catch { return null }
  },

  async snapshotPlans(binding: PluginFrameBinding, windowId: number, frame: WebFrameMain, current: () => PluginFrameBinding | null, requestId: string) {
    if (!root) return null
    try {
      const identity = tuple(binding, windowId), before = frameIdentity(frame), parent = frame.parent
      if (!identity || !before || !parent || !sameBinding(binding, current()) ||
        frame.frameTreeNodeId !== binding.frameTreeNodeId || frame.url !== binding.entryUrl) return null
      const value: unknown = await frame.executeJavaScript(`(() => {
        const lists = [...document.querySelectorAll('.plans-left-surface .plans-sidebar-list')];
        if (lists.length !== 1 || !lists[0].isConnected) return null;
        // Compact recent/pinned divs have names but no paths. Only full buttons
        // establish rendered name/path provenance; malformed full rows still reject.
        const cells = [...lists[0].querySelectorAll('button.plan-row:not(.plan-row--compact)')];
        if (cells.length > 16) return null;
        const rows = [];
        for (const cell of cells) {
          const names = cell.querySelectorAll(':scope > .plan-row-name');
          const paths = cell.querySelectorAll(':scope > .plan-row-path');
          if (!cell.isConnected || names.length !== 1 || !names[0].isConnected || paths.length !== 1 || !paths[0].isConnected) return null;
          rows.push({ name: names[0].textContent.trim(), relPath: paths[0].textContent.trim() });
        }
        return rows;
      })()`, false)
      if (!sameBinding(binding, current()) || frameIdentity(frame) !== before || frame.parent !== parent) return null
      const rows = planRows(value)
      if (!rows) return null
      const snapshot = { ...identity, frameIdentityHash: hash(before), requestIdHash: hash(requestId), rows }
      return record({ phase: 'plans-dom', ...snapshot }) ? snapshot : null
    } catch { return null }
  },
}
