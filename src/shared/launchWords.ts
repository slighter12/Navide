/**
 * The words a CLI launch is composed from, and the one place the renderer spells a vendor's
 * options from the generated facts (`vendorFacts.ts`).
 *
 * The sidecar composes a launch from these words, never from a command line read back. Each
 * `opt` word names an option (model, effort, ...) and its value; the spelling comes from the
 * facts, so a vendor's flag is written down once. The renderer also needs the text view (the
 * pane's visible command, its stored command), so an `opt` word it builds carries `text`, the
 * spelling with its leading space, and the text of a launch is the concatenation of its words.
 *
 * Only an argument that needs no shell quoting becomes an `opt` or `arg` word. The renderer
 * has no quoting rules of its own: anything else stays a `lit` word, the text as written.
 */
import { OPTION_RULES, VENDOR_FACTS, fillId, type LaunchOption } from './vendorFacts'

export type { LaunchOption }

export type LaunchWord =
  | { lit: string }
  | { exe: string }
  | { arg: string }
  | { opt: LaunchOption; value?: string; text?: string }

export type VendorFacts = (typeof VENDOR_FACTS)[keyof typeof VENDOR_FACTS]

/** Text both the POSIX and the Windows quoter leave as it is. */
const UNQUOTED = /^[A-Za-z0-9_@%+=:,./-]+$/

export function wordsText(words: readonly LaunchWord[]): string {
  return words.map((word) => {
    if ('lit' in word) return word.lit
    if ('exe' in word) return word.exe
    if ('arg' in word) return ` ${word.arg}`
    return word.text ?? ''
  }).join('')
}

/** The words of a command that is only text: one literal, rendered as it stands. */
export function literalWords(text: string): LaunchWord[] {
  return [{ lit: text }]
}

/** The program word. A program that needs quoting stays a literal. */
export function programWord(program: string): LaunchWord {
  return UNQUOTED.test(program) ? { exe: program } : { lit: program }
}

/** One more argument. An argument that needs quoting stays a literal with its own leading space. */
export function argWord(arg: string): LaunchWord {
  return UNQUOTED.test(arg) ? { arg } : { lit: ` ${arg}` }
}

/** The spelling of one option, with its leading space; `null` when the vendor declares none. One lookup over the generated
 *  option table (`OPTION_RULES`): the flag (or, for a template, the text holding `{id}`), then the value when the option takes
 *  one. The renderer has no quoting rules: the value is written as it is. */
export function optionText(facts: VendorFacts, option: LaunchOption, value?: string): string | null {
  const rule = OPTION_RULES[option]
  const flag = facts[rule.field] as string | null
  if (!flag) return null
  if (!rule.takes_value) return ` ${flag}`
  if (!value) return null
  return ` ${rule.template ? fillId(flag, value) : `${flag} ${value}`}`
}

/** The word for one option, or `null` when the vendor declares none. A value that needs quoting
 *  becomes a literal of the same text, and so does a value the facts do not list (an effort outside `known_efforts`): the
 *  sidecar refuses such an `opt`, so the value travels as the text the user chose. */
export function optionWord(facts: VendorFacts, option: LaunchOption, value?: string): LaunchWord | null {
  const text = optionText(facts, option, value)
  if (text === null) return null
  if (value !== undefined && !UNQUOTED.test(value)) return { lit: text }
  const accepted = OPTION_RULES[option].values
  if (accepted !== null && !((facts[accepted] as readonly string[] | null) ?? []).includes(value ?? '')) return { lit: text }
  return value === undefined ? { opt: option, text } : { opt: option, value, text }
}

function factsFor(agentKey: string): VendorFacts | undefined {
  return (VENDOR_FACTS as Record<string, VendorFacts | undefined>)[agentKey]
}

/** The words of a fresh launch: the program, the vendor's own pane argument, then each option the
 *  launch carries. An option the vendor declares no spelling for is left out, so the words then
 *  disagree with the command and `verifiedWords` drops them. */
export function freshLaunchWords(input: {
  agentKey: string
  program: string
  paneArg?: string
  skipPermissions: boolean
  model?: string
  effort?: string
}): LaunchWord[] {
  const facts = factsFor(input.agentKey)
  const words: LaunchWord[] = [programWord(input.program)]
  if (input.paneArg) words.push({ lit: ` ${input.paneArg}` })
  const add = (option: LaunchOption, value?: string) => {
    const word = facts && optionWord(facts, option, value)
    if (word) words.push(word)
  }
  if (input.skipPermissions) add('skip_permissions')
  if (input.model) add('model', input.model)
  if (input.effort) add('effort', input.effort)
  return words
}

/** The launch appended with the session id the pane is pinned to at launch. */
export function withSessionIdWords(agentKey: string, words: readonly LaunchWord[], id: string): LaunchWord[] {
  const facts = factsFor(agentKey)
  const word = facts && optionWord(facts, 'session_id', id)
  return word ? [...words, word] : [...words]
}

type ResumeAliases = { programs: readonly string[]; flags: readonly string[] } | null
type Subcommand = { word: string; aliases: readonly string[] } | null

/** `shlex.split` (POSIX mode, no comments), the grammar the backend reads a line with on POSIX (`cli_launch.read_literal`):
 *  whitespace is space, tab, CR and LF; a quote brackets text (adjacent quoted and plain text is one word, `a"b c"` is `ab c`);
 *  a backslash makes the next character literal, and in double quotes only before `"` or `\`. `null` for a line shlex refuses
 *  (an unbalanced quote, a trailing backslash). */
function shlexSplit(text: string): string[] | null {
  const words: string[] = []
  let word = ''
  let started = false
  let quote = ''
  for (let at = 0; at < text.length; at += 1) {
    const char = text[at]
    if (quote === "'") {
      if (char === "'") quote = ''
      else word += char
    } else if (char === '\\') {
      const next = text[at + 1]
      if (next === undefined) return null
      at += 1
      word += quote === '"' && next !== '"' && next !== '\\' ? `\\${next}` : next
      started = true
    } else if (quote === '"') {
      if (char === '"') quote = ''
      else word += char
    } else if (char === "'" || char === '"') {
      quote = char
      started = true
    } else if (char === ' ' || char === '\t' || char === '\r' || char === '\n') {
      if (started) words.push(word)
      word = ''
      started = false
    } else {
      word += char
      started = true
    }
  }
  if (quote) return null
  if (started) words.push(word)
  return words
}

/** A line split into its words. Read as the backend reads a POSIX line (`shlex`); a line it cannot read is whitespace-separated
 *  words, as `read_literal` falls back to. The renderer reads every line this way. A Windows line is read by the backend with the
 *  Windows grammar (backslash is a plain character, `'` is not a quote), so the two disagree only on a line that holds a backslash
 *  or a single quote; the consequence is a resume id the renderer reads differently, and the backend's reading decides. */
export function lineTokens(text: string): string[] {
  return shlexSplit(text) ?? text.split(/\s+/).filter(Boolean)
}

/** The session id a command line resumes, by the vendor's declared spelling; '' when it resumes none.
 *
 *  The one reader the renderer has for that (the backend's `resume_id_from_command` reads the same facts the same
 *  way): the program (`command`, or one of `resume_aliases.programs`) first, then the `subcommand` if declared, then
 *  the resume word right after them (`codex resume <id>`) or the resume flag (the one `resume_args` ends on, or one of
 *  `resume_aliases.flags`) anywhere after, as `<flag> <id>` or `<flag>=<id>`, up to a bare `--`. */
export function resumeIdFromCommand(facts: VendorFacts, text: string): string {
  const template = facts.resume_args as string | null
  if (!template) return ''
  const tokens = lineTokens(text)
  const aliases = facts.resume_aliases as ResumeAliases
  if (!tokens.length || ![facts.command as string, ...(aliases?.programs ?? [])].includes(tokens[0])) return ''
  let rest = tokens.slice(1)
  const subcommand = facts.subcommand as Subcommand
  if (subcommand) {
    if (!rest.length || ![subcommand.word, ...subcommand.aliases].includes(rest[0])) return ''
    rest = rest.slice(1)
  }
  const spelled = template.split(' ')
  const marker = spelled[spelled.length - 1]
  let flags: string[]
  if (marker === '{id}') {
    const lead = spelled[spelled.length - 2]
    if (!lead.startsWith('-')) return rest.length > 1 && rest[0] === lead ? rest[1] : ''
    flags = [lead, ...(aliases?.flags ?? [])]
  } else {
    flags = [marker.replace(/=\{id\}$/, ''), ...(aliases?.flags ?? [])]
  }
  for (let index = 0; index < rest.length; index += 1) {
    const token = rest[index]
    if (token === '--') break
    let value = ''
    if (flags.includes(token) && index + 1 < rest.length) value = rest[index + 1]
    else for (const flag of flags) if (token.startsWith(`${flag}=`)) value = token.slice(flag.length + 1)
    if (value) return value.startsWith('-') ? '' : value
  }
  return ''
}

/** `resumeIdFromCommand` for an agent by key; '' for an agent the facts do not describe. */
export function resumeIdForAgent(agentKey: string, text: string): string {
  const facts = factsFor(agentKey)
  return facts ? resumeIdFromCommand(facts, text) : ''
}

/** The words when they spell exactly the command, else `undefined`: the backend then takes the
 *  command as one literal. A launch is never refused over a spelling the renderer got wrong. */
export function verifiedWords(command: string, words: readonly LaunchWord[]): LaunchWord[] | undefined {
  return wordsText(words) === command ? [...words] : undefined
}

/** The per-vendor option spellings every spec shares, derived from the facts.
 *  A field the facts leave null is absent, so the spec declares no such option. */
export function factOptions(facts: VendorFacts) {
  return {
    ...(facts.model_flag ? { modelArgs: (model: string) => optionText(facts, 'model', model)!.trim() } : {}),
    ...(facts.effort_flag ? { effortArgs: (effort: string) => optionText(facts, 'effort', effort)!.trim() } : {}),
    ...(facts.known_efforts ? { knownEfforts: facts.known_efforts as readonly string[] } : {}),
    ...(facts.permission_flag ? { skipPermissionFlag: facts.permission_flag as string } : {}),
    ...(facts.resume_args ? { resumeArgs: (id: string) => optionText(facts, 'resume', id)!.trim() } : {}),
  }
}
