import { describe, expect, it } from 'vitest'
import {
  MSG_INTERRUPTED_PREFIX,
  isInjectedMessageText,
  renderInterruptedChildrenNotice,
} from '../agentMessaging'

describe('renderInterruptedChildrenNotice', () => {
  it('is labelled as something Navide injected', () => {
    const out = renderInterruptedChildrenNotice([{ name: 'worker-1', resumed: true }])
    expect(out.startsWith(MSG_INTERRUPTED_PREFIX)).toBe(true)
    // A reader echoing it back must not read as the parent's own words.
    expect(isInjectedMessageText(out)).toBe(true)
  })

  it('names each child and whether it came back', () => {
    const out = renderInterruptedChildrenNotice([
      { name: 'worker-1', resumed: true },
      { name: 'worker-2', resumed: false },
    ])
    expect(out).toContain('worker-1')
    expect(out).toContain('worker-2')
    expect(out).toMatch(/worker-1[^\n]*已接回/)
    expect(out).toMatch(/worker-2[^\n]*未接回/)
  })

  it('says the resumed children were not told to continue', () => {
    // Resuming only restores the conversation; continuing is the parent's call.
    const out = renderInterruptedChildrenNotice([{ name: 'worker-1', resumed: true }])
    expect(out).toContain('沒有自動續跑')
  })

  it('returns nothing for no children', () => {
    expect(renderInterruptedChildrenNotice([])).toBe('')
  })
})

describe('renderInterruptedChildrenNotice after a backend restart (H3)', () => {
  it('names the backend restart as the cause and keeps the relaunch text by default', () => {
    const children = [{ name: 'worker-1', resumed: true }, { name: 'worker-2', resumed: false }]
    const backend = renderInterruptedChildrenNotice(children, 'backend-restart')
    expect(backend).toContain('Navide 的後端重新啟動時')
    expect(backend.startsWith(MSG_INTERRUPTED_PREFIX)).toBe(true)
    expect(backend).toMatch(/worker-2[^\n]*未接回[^\n]*已結束/)
    expect(backend).toContain('沒有自動續跑')
    // The relaunch notice is unchanged.
    expect(renderInterruptedChildrenNotice(children)).toBe(renderInterruptedChildrenNotice(children, 'relaunch'))
    expect(renderInterruptedChildrenNotice(children)).toContain('— Navide 重新啟動時')
    expect(renderInterruptedChildrenNotice(children)).toMatch(/worker-2[^\n]*仍是還原佔位/)
  })
})

describe('renderInterruptedChildrenNotice after an Agent CLI Runtime loss', () => {
  it('names the runtime loss as the cause, never a backend restart or a crash loop', () => {
    const children = [{ name: 'worker-1', resumed: true }, { name: 'worker-2', resumed: false }]
    const out = renderInterruptedChildrenNotice(children, 'runtime-lost')
    expect(out.startsWith(MSG_INTERRUPTED_PREFIX)).toBe(true)
    expect(out).toContain('Navide 的 CLI runtime 中止時')
    expect(out).not.toContain('後端')
    expect(out).toMatch(/worker-1[^\n]*已接回/)
    expect(out).toMatch(/worker-2[^\n]*未接回[^\n]*已結束/)
    expect(out).not.toMatch(/worker-2[^\n]*反覆崩潰/)
    expect(out).toContain('沒有自動續跑')
  })
})
