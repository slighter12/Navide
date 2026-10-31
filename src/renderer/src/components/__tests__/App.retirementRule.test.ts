// @vitest-environment happy-dom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// Ticket 05 review-9: ONE rule for every pane-retirement path — a pane's terminal owner (ref /
// term id) and its persisted record are retired only after the backend proved settlement; any
// refusal, timeout or `null` from sendQuiet keeps the pane, its ref and its record, and starts no
// replacement. The decision itself is `settlePaneTerminals` (lib/paneRetirement.ts, executed in
// its own unit test and against the real sidecar in tests/cli/retirementRule.test.ts). App.vue
// cannot be mounted by this suite, so its call-site wiring is asserted against the source, as the
// other App.*.test.ts files do — a supplement, not the proof.
const appSource = readFileSync(resolve(process.cwd(), 'src/renderer/src/App.vue'), 'utf8')

function fn(name: string): string {
  const start = appSource.indexOf(`function ${name}(`)
  expect(start, name).toBeGreaterThan(-1)
  const end = appSource.indexOf('\n}\n', start)
  expect(end).toBeGreaterThan(start)
  return appSource.slice(start, end)
}

describe('onKill: settlement first, retirement only on proof', () => {
  const body = fn('onKill')
  it('answers whether the pane was retired', () => {
    expect(appSource).toMatch(/async function onKill\([^)]*\): Promise<boolean> \{/)
  })
  it('decides through settlePaneTerminals, for both the known-id and the no-id pane', () => {
    expect(body).toContain('await settlePaneTerminals({')
    expect(body).toContain('killOwn: ref?.sessionId ? () => ref.kill({ force }) : undefined')
  })
  it('never swallows a release answer', () => {
    expect(body).not.toMatch(/await sendQuiet<\{ ok: boolean \}>\('manual_pane\.release_pty'[^\n]*\)\n/)
  })
  it('returns false before touching the ref, the list, the history or the record', () => {
    const refusal = body.indexOf('if (!settled) {')
    expect(refusal).toBeGreaterThan(-1)
    const refusalBlock = body.slice(refusal, body.indexOf('\n  }\n', refusal))
    expect(refusalBlock).toContain('return false')
    for (const retirement of ['delete paneRefs[paneId]', 'histEntry.removedAt = ', 'panes.value = panes.value.filter((p) => p.id !== paneId)', 'unregisterPaneMessaging(paneId)']) {
      expect(body.indexOf(retirement), retirement).toBeGreaterThan(refusal)
    }
  })
})

describe('the user\'s own close hides the pane while settling', () => {
  it('goes through onUserClosePane from the confirm dialog and the list button', () => {
    expect(fn('onUserClosePane')).toContain('closePaneHidden(closingPaneIds.value, paneId, async () => await onKill(paneId, opts))')
    expect(fn('closeFocusedPane')).toContain('await onUserClosePane(paneId)')
    expect(appSource).toContain('@kill="onUserClosePane"')
  })
  it.each([
    ['batchKill', 'await onUserClosePane(id)'],
    ['closeAllSessions', 'await onUserClosePane(p.id)'],
    ['closeRunGroup', 'await onUserClosePane(p.id)'],
  ])('the bulk user close %s hides each pane too', (name, call) => {
    const body = fn(name)
    expect(body).toContain(call)
    expect(body).not.toMatch(/await onKill\(/)
  })
  it('hands the stage to the next pane while the focused pane is closing (sidebar, spotlight, fullscreen, dual focus)', () => {
    // The behaviour is tested in lib/__tests__/paneFocus.test.ts; this only pins the call site.
    expect(appSource).toContain(
      'resolveFocusedPane(focusPaneId.value, panesOnStage.value, minimizedPanes.value, closingPaneIds.value)'
    )
    const start = appSource.indexOf('const runningPaneIds = computed(')
    const block = appSource.slice(start, appSource.indexOf('\n})\n', start))
    expect(block).toContain('!closingPaneIds.value.has(p.id)')
  })
  it('removes a closing pane from the layout but not from the pane list', () => {
    const start = appSource.indexOf('const tabVisiblePanes = computed(')
    const block = appSource.slice(start, appSource.indexOf('\n)\n', start))
    expect(block).toContain('!closingPaneIds.value.has(p.id)')
    expect(block).toContain('panes.value.filter(')
  })
})

describe('callers that start a replacement (or retire more) only after a proven onKill', () => {
  it.each([
    ['rebuildPaneViaResume', 'await spawnPane({'],
    ['rebuildPaneClean', 'await spawnPane({'],
    ['onPipelineRestart', 'await onPipelineStart(payload)'],
    ['activateStage', 'registerStage(stageCompletions, index, stage.slots.length)'],
  ])('%s', (name, replacement) => {
    const body = fn(name)
    const kill = body.indexOf('onKill(')
    const next = body.indexOf(replacement, kill)
    expect(kill).toBeGreaterThan(-1)
    expect(next).toBeGreaterThan(kill)
    expect(body.slice(kill - 40, next)).toMatch(/(!\(await onKill\(|retired\.includes\(false\))[\s\S]*return/)
  })

  it('reclaimIdlePane keeps the pane when its terminal was not proven settled', () => {
    expect(fn('reclaimIdlePane')).toMatch(/if \(!\(await onKill\(paneId, \{ markRemoved: false, keepInList: true, force: false \}\)\)\) return false/)
  })

  it('closeWorkspace stops on the first unproven pane and checks the by-pane retirement', () => {
    const body = fn('closeWorkspace')
    expect(body).toMatch(/if \(!\(await onKill\(pane\.id, \{ markRemoved: false \}\)\)\) return/)
    expect(body).toMatch(/const slotRetired = await sendQuiet<ProjectPayload>\('pipeline\.slot_unspawn_by_pane'[\s\S]*if \(!slotRetired\) \{[\s\S]*return/)
    expect(body).toMatch(/if \(!\(await onKill\(pane\.id, \{ markRemoved: false, force: false \}\)\)\) return/)
    expect(body).not.toContain("await sendQuiet<{ ok: boolean }>('manual_pane.release_pty', { pane_id: pane.id })")
  })
})

describe('a closing pane is listed on no surface until its close is refused', () => {
  // The body of `const <name> = computed...(...)`, cut at the paren that closes the computed call:
  // no dependence on blank lines or on how the text is wrapped.
  const computedBody = (name: string): string => {
    const start = appSource.indexOf(`const ${name} = computed`)
    expect(start, name).toBeGreaterThan(-1)
    const open = appSource.indexOf('(', start)
    let depth = 0
    for (let i = open; i < appSource.length; i++) {
      if (appSource[i] === '(') depth++
      else if (appSource[i] === ')' && --depth === 0) return appSource.slice(start, i + 1)
    }
    throw new Error(`unterminated computed: ${name}`)
  }
  // `paneViews.value` / `panes.value` as a whole identifier: `listedPaneViews.value` and
  // `listedPanes.value` do not match, so a raw read cannot hide behind a similar name.
  const raw = (id: string): RegExp => new RegExp(`(?<![\\w$.])${id}\\.value\\b`)
  const tag = (name: string): string => {
    const start = appSource.indexOf(`<${name}\n`)
    expect(start, name).toBeGreaterThan(-1)
    return appSource.slice(start, appSource.indexOf('/>', start))
  }

  it('derives the listed panes and views through withoutClosing', () => {
    expect(computedBody('listedPanes')).toContain('withoutClosing(panes.value, closingPaneIds.value)')
    expect(computedBody('listedPaneViews')).toContain('withoutClosing(paneViews.value, closingPaneIds.value)')
  })
  it.each(['paneLineage', 'paneListLineage'])('%s is built from the listed panes', (name) => {
    expect(computedBody(name)).toContain('listedPanes.value')
    expect(computedBody(name)).not.toMatch(raw('panes'))
  })
  it('the stage tabs and the pane list read the listed panes', () => {
    expect(computedBody('panesOnStage')).toContain('closingPaneIds.value')
    expect(computedBody('auxiliaryListPanes')).toContain('listedPaneViews.value')
    expect(computedBody('auxiliaryListPanes')).not.toMatch(raw('paneViews'))
  })
  it('the workspace groups and the sidebar cards read the listed panes and views', () => {
    expect(computedBody('workspaceGroups')).toContain('panes: listedPanes.value')
    expect(tag('ControlPane')).toContain(':panes="listedPaneViews"')
  })
  it('the token panel, the turn-stats modal and the auxiliary subtree chips read the listed views', () => {
    expect(tag('TokenStatsPanel')).toContain(':panes="listedPaneViews"')
    expect(tag('TurnStatsModal')).toContain(':panes="listedPaneViews"')
    expect(computedBody('paneListSubtree')).toContain('subtreeSignals(listedPaneViews.value)')
    expect(computedBody('paneListSubtree')).not.toMatch(raw('paneViews'))
  })
  it('the spotlight empty state reads the same list as the cards beside it', () => {
    expect(appSource).toMatch(/v-if="auxiliaryListPanes\.length === 0"\s+class="spotlight-strip-empty"/)
  })
  it('the status-bar pill total and "reclaim now" leave out a closing pane', () => {
    // reclaimableNowIds drives the "reclaim now" counts AND the default targets of reclaimPanesNow,
    // so a closing pane must not be offered (a second onKill would race the close in flight).
    expect(computedBody('reclaimableNowIds')).toContain('listedPanes.value')
    expect(computedBody('reclaimableNowIds')).not.toMatch(raw('panes'))
    expect(computedBody('resourcePillText')).toContain('listedPanes.value.length')
    expect(computedBody('resourcePillText')).not.toMatch(raw('panes'))
  })
  it('a hidden closing pane is no part of a batch (selection, context menu, remove selected)', () => {
    expect(computedBody('selectionBatchIds')).toContain('idsWithoutClosing(')
    expect(computedBody('ctxTargetIds')).toContain('idsWithoutClosing(')
    expect(fn('batchKill')).toContain('idsWithoutClosing(ids, closingPaneIds.value)')
  })
})

describe('ui.pane.close (MCP) reports a refused close', () => {
  it('fails the command when onKill did not retire the pane', () => {
    const start = appSource.indexOf("registerCommand('ui.pane.close'")
    expect(start).toBeGreaterThan(-1)
    const body = appSource.slice(start, appSource.indexOf('\n})\n', start))
    expect(body).toMatch(/if \(!\(await onKill\(paneId\)\)\) \{?\s*throw new Error\(/)
    expect(body).not.toMatch(/\n  await onKill\(paneId\)\n/)
  })
})
