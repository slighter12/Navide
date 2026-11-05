import { describe, expect, it } from 'vitest'
import { AI_CLI_PROFILES, TERMINAL_AI_CLI_PROFILES } from './aiCliProfiles'

// The Git sidebar and Plans launch an unattended CLI as `<command> <yoloFlag>`. The flags come from the
// vendor declarations (`shared/agent-cli/<key>.json`); this pins what each launch path now produces.
describe('AI CLI launch profiles', () => {
  it('launches OpenCode without --auto', () => {
    // Earlier releases launched OpenCode as `opencode --auto`. The declaration has no unattended-mode
    // flag for OpenCode (`permission_flag: null`), so no profile passes one any more.
    expect(AI_CLI_PROFILES.opencode).toEqual({ label: 'OpenCode', command: 'opencode' })
    expect(AI_CLI_PROFILES.opencode).not.toHaveProperty('yoloFlag')
    expect(TERMINAL_AI_CLI_PROFILES.opencode).toEqual({ label: 'OpenCode', command: 'opencode' })
  })

  it.each([
    ['claude', 'claude', '--dangerously-skip-permissions'],
    ['codex', 'codex', '--dangerously-bypass-approvals-and-sandbox'],
    ['antigravity', 'agy', '--dangerously-skip-permissions'],
    ['kimi', 'kimi', '--yolo'],
    ['qwen', 'qwen', '--yolo'],
    ['kilo', 'kilo', '--auto'],
    ['copilot', 'copilot', '--yolo'],
    ['cursor', 'agent', '--force'],
    ['aider', 'aider', '--yes-always'],
    ['muse', 'muse', '--disable-approval'],
  ] as const)('keeps %s unattended as `%s %s`', (id, command, flag) => {
    expect(AI_CLI_PROFILES[id]).toMatchObject({ command, yoloFlag: flag })
  })

  it.each(['grok', 'pi'] as const)('%s has no unattended-mode flag', (id) => {
    expect(AI_CLI_PROFILES[id]).not.toHaveProperty('yoloFlag')
  })

  it('keeps the richer terminal-only Droid profile', () => {
    expect(TERMINAL_AI_CLI_PROFILES.droid).toMatchObject({ command: 'droid', yoloFlag: '--auto high' })
  })
})
