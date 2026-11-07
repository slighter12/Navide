import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { VENDOR_FACTS } from './vendorFacts'
import { lineTokens, optionWord, resumeIdForAgent, wordsText, type LaunchWord, type VendorFacts } from './launchWords'

// The vectors the three languages share (backend/tests/test_launch_word_vectors.py, native/navide-cli-runtime/src/compose.rs):
// the spelling of an option from the generated option table, and the resume id a line names.
const DATA = join(process.cwd(), 'backend/tests/data')
const load = (name: string): any => JSON.parse(readFileSync(join(DATA, name), 'utf8'))
const VECTORS = load('launch_word_vectors.json')

type SpelledVector = { name: string; vendor: string; renderer: 'safe' | 'skip'; words: any[]; posix: string; windows: string }
type RefusedVector = { name: string; vendor: string; words: any[]; renderer: 'omitted' | 'literal' | 'none'; literal?: string }
type ResumeVector = { vendor: string; line: string; id: string; grammar?: string; readers?: string[] }

const factsOf = (vendor: string) => (VENDOR_FACTS as Record<string, VendorFacts>)[vendor]

/** The words the renderer composes for a vector: an option goes through `optionWord`, the rest stays as it is. */
function rendered(vendor: string, words: any[]): LaunchWord[] {
  return words.map((word) => {
    if (!('opt' in word)) return word as LaunchWord
    const made = optionWord(factsOf(vendor), word.opt, word.value)
    if (made === null) throw new Error(`${vendor}: no spelling for ${word.opt}`)
    return made
  })
}

describe('the renderer spells an option as the shared vectors do', () => {
  const safe = (VECTORS.words_to_text.vectors as SpelledVector[]).filter((vector) => vector.renderer === 'safe')

  it.each(safe.map((vector) => [vector.name, vector] as const))('%s', (_name, vector) => {
    // A value that needs no quoting is spelled the same in both grammars, and the renderer has no quoting rules of its own.
    expect(vector.posix).toBe(vector.windows)
    expect(wordsText(rendered(vector.vendor, vector.words))).toBe(vector.posix)
  })

  const refused = VECTORS.words_to_text.refused as RefusedVector[]
  it.each(refused.filter((vector) => vector.renderer !== 'none').map((vector) => [vector.name, vector] as const))(
    '%s: the renderer leaves the option out or keeps it as text',
    (_name, vector) => {
      const option = vector.words.find((word) => 'opt' in word)
      const word = optionWord(factsOf(vector.vendor), option.opt, option.value)
      if (vector.renderer === 'omitted') expect(word).toBeNull()
      else expect(word).toEqual({ lit: vector.literal })
    },
  )
})

describe('the renderer reads a resume id as the shared vectors do', () => {
  const vectors = VECTORS.resume_ids.vectors as ResumeVector[]
  const shared = vectors.filter((vector) => (vector.readers ?? ['typescript']).includes('typescript'))

  it.each(shared.map((vector) => [`${vector.vendor}: ${vector.line}`, vector] as const))('%s', (_name, vector) => {
    expect(resumeIdForAgent(vector.vendor, vector.line)).toBe(vector.id)
  })

  // The named rules the renderer has no counterpart of: the backend refuses these lines and the renderer reads the id in them.
  it('reads the id out of a line the backend refuses (plain_only, and Grok accepting only a UUID)', () => {
    const python = vectors.filter((vector) => vector.readers?.length === 1 && vector.readers[0] === 'python')
    expect(python.length).toBeGreaterThan(0)
    for (const vector of python) expect(resumeIdForAgent(vector.vendor, vector.line)).not.toBe('')
  })
})

describe('the renderer tokenizer agrees with the POSIX shlex vectors', () => {
  const split = load('g2_split_vectors.json').vectors as { input: string; words?: string[]; error?: true }[]

  it.each(split.filter((vector) => !vector.error).map((vector) => [JSON.stringify(vector.input), vector] as const))(
    '%s',
    (_name, vector) => {
      expect(lineTokens(vector.input)).toEqual(vector.words)
    },
  )

  // The backend reads a line it cannot tokenize (an unbalanced quote, a trailing backslash) as whitespace-separated words
  // (`cli_launch.read_literal`); the renderer does the same.
  it.each(split.filter((vector) => vector.error).map((vector) => [JSON.stringify(vector.input), vector] as const))(
    'falls back to whitespace words for the unreadable line %s',
    (_name, vector) => {
      expect(lineTokens(vector.input)).toEqual(vector.input.split(/\s+/).filter(Boolean))
    },
  )

  it('reads adjacent quotes as one word, as shlex does', () => {
    expect(lineTokens('a"b c"')).toEqual(['ab c'])
    expect(lineTokens("a'b c'd")).toEqual(['ab cd'])
  })
})
