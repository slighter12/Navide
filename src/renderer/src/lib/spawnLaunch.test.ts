// The spawn seam of App.vue's spawnPane: what a launch is composed to, and what reaches the terminal port.
//
// App.vue cannot be mounted by the suite, so the seam is the module spawnPane calls: `composeLaunch` decides the
// command and its words, `spawnLaunch` hands them to the terminal port. A fake port records the request, which is what
// the real pane forwards to the backend's terminal.create.
import { describe, expect, it } from 'vitest'
import { buildResumeLaunch } from '@navide/plugin-shell'
import * as pluginShell from '@navide/plugin-shell'
import { shellCommandArgv } from '../../../shared/osplat'
import { wordsText, type LaunchWord } from '../../../shared/launchWords'
import { composeLaunch, spawnLaunch, type LaunchInput } from './spawnLaunch'

const ID = '4d4a11fe-b08a-46df-9f86-685589531e65'

interface Request {
  command: string | string[]
  words?: LaunchWord[]
  cwd: string
  agentKey: string
}

function fakePort() {
  const requests: Request[] = []
  return {
    requests,
    async spawn(request: Request): Promise<string> {
      requests.push(request)
      return 'spawned'
    },
  }
}

function input(over: Partial<LaunchInput>): LaunchInput {
  return {
    agentKey: 'claude',
    commandOverride: '',
    storedCommand: '',
    isLogin: false,
    isResume: false,
    skipFlag: '',
    paneArg: '',
    model: { model: '', effort: '' },
    userShell: '/bin/zsh',
    newSessionId: () => ID,
    ...over,
  }
}

async function spawned(over: Partial<LaunchInput>) {
  const port = fakePort()
  const plan = composeLaunch(input(over))
  await spawnLaunch(port, plan, '/bin/zsh', { cwd: '/work', agentKey: plan.agentKey })
  return { plan, request: port.requests[0] }
}

describe('a caller resume override reaches the terminal port as the words it was composed from', () => {
  it('sends the words of a rebuilt resume launch, not one literal', async () => {
    const launch = buildResumeLaunch('codex', ID, '--dangerously-bypass-approvals-and-sandbox')
    const { plan, request } = await spawned({
      agentKey: 'codex', commandOverride: launch.command, commandWords: launch.words, isResume: true,
    })
    expect(plan.source).toBe('caller')
    expect(request.words).toEqual(launch.words)
    expect(request.words?.some((word) => 'opt' in word && word.opt === 'resume')).toBe(true)
    expect(wordsText(request.words!)).toBe(launch.command)
    expect(request.command).toEqual(shellCommandArgv('/bin/zsh', launch.command, { agentPane: true }))
    expect(request).toMatchObject({ cwd: '/work', agentKey: 'codex' })
  })

  it('sends one literal for a caller command that has no words', async () => {
    const { request } = await spawned({ agentKey: 'codex', commandOverride: 'codex resume abc --x', isResume: true })
    expect(request.words).toEqual([{ lit: 'codex resume abc --x' }])
  })

  it('sends one literal when the words it was given do not spell the command', async () => {
    const launch = buildResumeLaunch('codex', ID)
    const { request } = await spawned({
      agentKey: 'codex', commandOverride: `${launch.command} --edited`, commandWords: launch.words, isResume: true,
    })
    expect(request.words).toEqual([{ lit: `${launch.command} --edited` }])
  })

  it('keeps the words only for the launch that carries them: nothing is remembered between launches', async () => {
    const launch = buildResumeLaunch('codex', ID)
    await spawned({ agentKey: 'codex', commandOverride: launch.command, commandWords: launch.words, isResume: true })
    const { request } = await spawned({ agentKey: 'codex', commandOverride: launch.command, isResume: true })
    expect(request.words).toEqual([{ lit: launch.command }])
  })
})

describe('a stored launch command reaches the terminal port as one literal', () => {
  it('sends the stored line as it is written, with no model or effort claimed', async () => {
    const { plan, request } = await spawned({
      agentKey: 'codex', storedCommand: 'ccr code --verbose', model: { model: 'gpt', effort: 'high' },
      skipFlag: '--dangerously-bypass-approvals-and-sandbox',
    })
    expect(plan.source).toBe('stored')
    expect(plan.launchedModel).toEqual({ model: '', effort: '' })
    expect(plan.command).toBe('ccr code --verbose')
    expect(request.words).toEqual([{ lit: 'ccr code --verbose' }])
  })

  it('never takes the words of a caller launch for the stored line', async () => {
    const launch = buildResumeLaunch('codex', ID)
    const { request } = await spawned({ agentKey: 'codex', storedCommand: 'ccr code', commandWords: launch.words })
    expect(request.words).toEqual([{ lit: 'ccr code' }])
  })

  it('a stored claude line is still pinned to a session id, as one more option word after the literal', async () => {
    const { plan, request } = await spawned({ storedCommand: 'ccr code' })
    expect(plan.source).toBe('stored')
    expect(plan.command).toBe(`ccr code --session-id ${ID}`)
    expect(request.words).toEqual([
      { lit: 'ccr code' },
      { opt: 'session_id', value: ID, text: ` --session-id ${ID}` },
    ])
  })

  it('a sign-in pane skips the stored line and launches the vendor default', async () => {
    const { plan, request } = await spawned({ storedCommand: 'ccr code', isLogin: true })
    expect(plan.source).toBe('none')
    expect(plan.command.startsWith('claude')).toBe(true)
    expect(request.words![0]).toEqual({ exe: 'claude' })
  })
})

describe('a fresh launch that pins a session id', () => {
  it('sends words that end in the session_id option', async () => {
    const { plan, request } = await spawned({
      agentKey: 'claude', skipFlag: '--dangerously-skip-permissions', model: { model: 'opus', effort: 'high' },
    })
    expect(plan.source).toBe('none')
    expect(plan.explicitSessionId).toBe(ID)
    const words = request.words!
    expect(words[words.length - 1]).toMatchObject({ opt: 'session_id', value: ID })
    expect(words.map((word) => ('opt' in word ? word.opt : 'exe' in word ? 'exe' : 'other'))).toEqual([
      'exe', 'skip_permissions', 'model', 'effort', 'session_id'])
    expect(wordsText(words)).toBe(`claude --dangerously-skip-permissions --model opus --effort high --session-id ${ID}`)
    expect(plan.command).toBe(wordsText(words))
  })

  it('pins nothing on a resume, and reuses a saved id when the restore asks for it', async () => {
    const resumed = await spawned({ agentKey: 'claude', commandOverride: `claude --resume ${ID}`, isResume: true })
    expect(resumed.plan.explicitSessionId).toBe('')
    expect(resumed.request.words).toEqual([{ lit: `claude --resume ${ID}` }])
    const saved = '11111111-2222-3333-4444-555555555555'
    const fresh = await spawned({ agentKey: 'claude', freshSessionId: saved })
    expect(fresh.plan.explicitSessionId).toBe(saved)
    expect(fresh.request.words![fresh.request.words!.length - 1]).toMatchObject({ opt: 'session_id', value: saved })
  })

  it('a terminal pane with no command runs the user shell as one literal', async () => {
    const { plan, request } = await spawned({ agentKey: 'terminal' })
    expect(plan.command).toBe('/bin/zsh')
    expect(request.words).toEqual([{ lit: '/bin/zsh' }])
  })
})

describe('no module-level side channel carries the words', () => {
  it('does not export the table of remembered resume words', () => {
    expect('resumeWordsFor' in pluginShell).toBe(false)
  })
})
