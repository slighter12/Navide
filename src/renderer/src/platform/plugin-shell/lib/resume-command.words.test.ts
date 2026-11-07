import { describe, expect, it } from 'vitest'
import { VENDOR_FACTS } from '@shared/vendorFacts'
import { verifiedWords, wordsText } from '@shared/launchWords'
import { AGENT_SPECS } from '../agents'
import { buildResumeCommand, buildResumeLaunch, resumeWordsFor } from './resume-command'

const ID = '019f6155-a2ae-72a2-a455-bf454b8f9f90'
const resumable = AGENT_SPECS.filter((s) => s.resumeArgs && !s.resumeWithoutId).map((s) => s.agentKey)

describe('the words of a resume command', () => {
  it('spell exactly the command, for every vendor that resumes by id', () => {
    expect(resumable.length).toBeGreaterThan(5)
    for (const key of resumable) {
      const { command, words } = buildResumeLaunch(key, ID, '', '', { model: '', effort: '' })
      expect(command, key).not.toBe('')
      expect(wordsText(words), key).toBe(command)
      expect(verifiedWords(command, words), key).toEqual(words)
    }
  })

  it('name the id as one resume option where the vendor declares a spelling', () => {
    for (const key of resumable) {
      const { words } = buildResumeLaunch(key, ID)
      if (!(VENDOR_FACTS as Record<string, { resume_args: string | null }>)[key]?.resume_args) continue
      expect(words.some((w) => 'opt' in w && w.opt === 'resume' && w.value === ID), key).toBe(true)
    }
  })

  it('carry the permission flag as its option and the model as its option with a value', () => {
    const flag = VENDOR_FACTS.claude.permission_flag as string
    const { command, words } = buildResumeLaunch('claude', ID, flag, '', { model: 'opus', effort: '' })
    expect(command).toBe(`claude --resume ${ID} ${flag} --model opus`)
    expect(words).toContainEqual({ opt: 'skip_permissions', text: ` ${flag}` })
    expect(words).toContainEqual({ opt: 'model', value: 'opus', text: ' --model opus' })
  })

  it('keep a flag the vendor does not spell as a literal of the same text', () => {
    const { command, words } = buildResumeLaunch('claude', ID, '--some-future-flag')
    expect(command).toBe(`claude --resume ${ID} --some-future-flag`)
    expect(words).toContainEqual({ lit: ' --some-future-flag' })
    expect(wordsText(words)).toBe(command)
  })

  it('are found again by the exact command text that buildResumeCommand returned', () => {
    const command = buildResumeCommand('codex', ID)
    expect(command).toBe(`codex resume ${ID}`)
    expect(resumeWordsFor(command)).toEqual(buildResumeLaunch('codex', ID).words)
    expect(resumeWordsFor(`${command} --extra`)).toBeUndefined()
  })

  it('are not made for a resume that has no id', () => {
    expect(buildResumeCommand('claude', '')).toBe('')
    expect(resumeWordsFor('')).toBeUndefined()
  })
})

describe('verifiedWords', () => {
  it('drops words that disagree with the command, so the backend takes the command as one literal', () => {
    const { command, words } = buildResumeLaunch('claude', ID)
    expect(verifiedWords(`${command} --edited-by-a-caller`, words)).toBeUndefined()
    expect(verifiedWords(command.replace(ID, 'another-id'), words)).toBeUndefined()
  })
})
