import { describe, expect, it } from 'vitest'
import { VENDOR_FACTS } from './vendorFacts'
import {
  argWord,
  factOptions,
  freshLaunchWords,
  literalWords,
  optionText,
  optionWord,
  programWord,
  verifiedWords,
  withSessionIdWords,
  wordsText,
  type VendorFacts,
} from './launchWords'

const ALL = Object.values(VENDOR_FACTS) as VendorFacts[]

describe('the text of a launch is the text of its words', () => {
  it('joins a program, literals, args and spelled options', () => {
    const words = [
      programWord('claude'),
      { lit: ' --flag' },
      argWord('x'),
      { opt: 'model' as const, value: 'm', text: ' --model m' },
    ]
    expect(wordsText(words)).toBe('claude --flag x --model m')
  })

  it('keeps anything that needs quoting a literal, as written', () => {
    expect(programWord('/my dir/claude')).toEqual({ lit: '/my dir/claude' })
    expect(argWord('a b')).toEqual({ lit: ' a b' })
    expect(optionWord(VENDOR_FACTS.claude, 'model', 'a b')).toEqual({ lit: ' --model a b' })
    expect(literalWords('x --y')).toEqual([{ lit: 'x --y' }])
  })
})

describe('options are spelled from the generated facts', () => {
  it.each(ALL.map((facts) => [facts.key, facts] as const))('%s: each declared flag is spelled once, from its fact', (_key, facts) => {
    expect(optionText(facts, 'model', 'm')).toBe(facts.model_flag ? ` ${facts.model_flag} m` : null)
    expect(optionText(facts, 'effort', 'e')).toBe(facts.effort_flag ? ` ${facts.effort_flag} e` : null)
    expect(optionText(facts, 'skip_permissions')).toBe(facts.permission_flag ? ` ${facts.permission_flag}` : null)
    expect(optionText(facts, 'resume', 'abc')).toBe(facts.resume_args ? ` ${facts.resume_args.replace('{id}', 'abc')}` : null)
    expect(optionText(facts, 'session_id', 'abc')).toBe(facts.session_id_flag ? ` ${facts.session_id_flag} abc` : null)
    expect(optionText(facts, 'login')).toBe(facts.login_args ? ` ${facts.login_args}` : null)
  })

  it.each(ALL.map((facts) => [facts.key, facts] as const))('%s: a spec declares exactly the options its facts do', (_key, facts) => {
    const options = factOptions(facts)
    expect('modelArgs' in options).toBe(Boolean(facts.model_flag))
    expect('effortArgs' in options).toBe(Boolean(facts.effort_flag))
    expect('knownEfforts' in options).toBe(Boolean(facts.known_efforts))
    expect('skipPermissionFlag' in options).toBe(Boolean(facts.permission_flag))
    expect('resumeArgs' in options).toBe(Boolean(facts.resume_args))
    if (facts.model_flag) expect(options.modelArgs!('m')).toBe(`${facts.model_flag} m`)
    if (facts.resume_args) expect(options.resumeArgs!('abc')).toBe(facts.resume_args.replace('{id}', 'abc'))
  })
})

describe('a fresh launch', () => {
  it('spells program, pane argument, permission flag and model in the order the command line has them', () => {
    const words = freshLaunchWords({
      agentKey: 'claude', program: 'claude', paneArg: '--x y', skipPermissions: true, model: 'opus', effort: 'high',
    })
    expect(wordsText(words)).toBe('claude --x y --dangerously-skip-permissions --model opus --effort high')
    expect(words.filter((word) => 'opt' in word).map((word) => ('opt' in word ? word.opt : ''))).toEqual([
      'skip_permissions', 'model', 'effort'])
  })

  it('adds the pinned session id as one more option word', () => {
    const base = freshLaunchWords({ agentKey: 'claude', program: 'claude', skipPermissions: false })
    const pinned = withSessionIdWords('claude', base, '4d4a11fe-b08a-46df-9f86-685589531e65')
    expect(wordsText(pinned)).toBe('claude --session-id 4d4a11fe-b08a-46df-9f86-685589531e65')
    expect(pinned[pinned.length - 1]).toMatchObject({ opt: 'session_id', value: '4d4a11fe-b08a-46df-9f86-685589531e65' })
  })

  it('leaves out an option the vendor declares no spelling for', () => {
    const words = freshLaunchWords({ agentKey: 'codex', program: 'codex', skipPermissions: false, effort: 'high' })
    expect(wordsText(words)).toBe('codex')
  })
})

describe('words are sent only when they spell exactly the command', () => {
  it('returns them when they do and nothing when they do not', () => {
    const words = freshLaunchWords({ agentKey: 'claude', program: 'claude', skipPermissions: true })
    expect(verifiedWords('claude --dangerously-skip-permissions', words)).toEqual(words)
    expect(verifiedWords('claude --other', words)).toBeUndefined()
  })
})
