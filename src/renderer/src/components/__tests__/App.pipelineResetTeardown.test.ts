// @vitest-environment happy-dom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { transformWithEsbuild } from 'vite'
import { closePaneHidden, pendingPaneClose } from '../../lib/paneRetirement'

// App mounts the terminal and backend lifecycles. Run its reset and kill-all
// bodies with a controllable settlement boundary, as App.pipelineActions does.
const source = readFileSync(resolve(process.cwd(), 'src/renderer/src/App.vue'), 'utf8')

function block(start: string, end: string): string {
  const from = source.indexOf(start)
  expect(from, start).toBeGreaterThan(-1)
  const to = source.indexOf(end, from)
  expect(to, end).toBeGreaterThan(from)
  return source.slice(from, to)
}

const resetSource = [
  block('async function onKillAll(', '\n// ── Batch actions'),
  block('async function onPipelineReset(', '\n/** The backend leg'),
].join('\n')

async function runtime(initialIds: string[]) {
  const panes = { value: initialIds.map((id) => ({ id })) }
  const closingPaneIds = { value: new Set<string>() }
  const panesInView = { get value() { return panes.value } }
  const listedPanesInView = { get value() { return panes.value.filter((p) => !closingPaneIds.value.has(p.id)) } }
  const requests: string[] = []
  const replies = new Map<string, (settled: boolean) => void>()
  const onKill = async (id: string): Promise<boolean> => {
    requests.push(id)
    const settled = await new Promise<boolean>((answer) => replies.set(id, answer))
    if (settled) panes.value = panes.value.filter((p) => p.id !== id)
    return settled
  }
  const onUserClosePane = (id: string) => closePaneHidden(
    closingPaneIds.value, id, () => onKill(id), () => panes.value.some((p) => p.id === id),
  )
  const pipeline = { state: 'running', task: 'task', stageIndex: 1, log: [] as string[] }
  const { code } = await transformWithEsbuild(resetSource, 'AppPipelineReset.ts', { loader: 'ts' })
  const factory = new Function(
    'panes', 'panesInView', 'listedPanesInView', 'closingPaneIds', 'pendingPaneClose',
    'onKill', 'onUserClosePane', 'stopGlobalManagerRouter', 'cancelAllWatchers',
    'stageCompletions', 'stagePrewarms', 'stageRouters', 'disposeStageRouter',
    'questionQueue', 'activeQuestion', 'clearStageStallAutoTimer', 'stageStallPrompt',
    'pipeline', 'pipelineRunWorkspace',
    `${code}\nreturn { onKillAll, onPipelineReset }`,
  )
  const actions = factory(
    panes, panesInView, listedPanesInView, closingPaneIds, pendingPaneClose,
    onKill, onUserClosePane, () => {}, () => {}, new Map(), new Map(), new Map(),
    () => {}, [], { value: null }, () => {}, { value: null }, pipeline, '',
  ) as { onKillAll: () => Promise<void>; onPipelineReset: () => Promise<void> }
  return {
    ...actions, panes, pipeline, requests, closing: closingPaneIds.value, onUserClosePane,
    finish: (id: string, settled: boolean) => {
      const answer = replies.get(id)
      expect(answer).toBeDefined()
      replies.delete(id)
      answer!(settled)
    },
  }
}

describe('pipeline reset pane teardown', () => {
  it('closes every viewed pane without hiding a reset target while settlement is pending', async () => {
    const app = await runtime(['a', 'b'])
    const reset = app.onPipelineReset()
    await vi.waitFor(() => expect(app.requests).toEqual(['a']))
    expect(app.closing.size).toBe(0)
    app.finish('a', true)
    await vi.waitFor(() => expect(app.requests).toEqual(['a', 'b']))
    expect(app.closing.size).toBe(0)
    app.finish('b', true)
    await reset
    expect(app.panes.value).toEqual([])
    expect(app.pipeline.state).toBe('idle')
  })

  it('joins an existing close, then retries a refusal before reset finishes', async () => {
    const app = await runtime(['a'])
    const userClose = app.onUserClosePane('a')
    await vi.waitFor(() => expect(app.requests).toEqual(['a']))
    const reset = app.onPipelineReset()
    expect(app.requests).toEqual(['a'])
    app.finish('a', false)
    await userClose
    await vi.waitFor(() => expect(app.requests).toEqual(['a', 'a']))
    app.finish('a', true)
    await reset
    expect(app.panes.value).toEqual([])
    expect(app.pipeline.state).toBe('idle')
  })

  it('keeps sidebar and History kill-all on the closing-free guarded path', async () => {
    const app = await runtime(['a', 'b'])
    const first = app.onUserClosePane('a')
    await vi.waitFor(() => expect(app.requests).toEqual(['a']))
    const killAll = app.onKillAll()
    await vi.waitFor(() => expect(app.requests).toEqual(['a', 'b']))
    expect(app.closing.has('b')).toBe(true)
    app.finish('b', true)
    await killAll
    app.finish('a', false)
    await first
    expect(app.panes.value.map((p) => p.id)).toEqual(['a'])
  })
})
