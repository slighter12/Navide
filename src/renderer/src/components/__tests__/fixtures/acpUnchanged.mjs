// Shared by App.tuiUnchanged.test.ts and its fixture regeneration.
//
// ACP is additive (user, 2026-10-23: the existing modes stay exactly as they
// are). Each existing App.vue function or template block ACP reaches gets
// added lines only. acp-unchanged-main.json holds the TUI text of each one
// (`main`) and the exact lines ACP adds to it (`hooks`); the test removes the
// hooks and requires `main` back, byte for byte.
//
// `main` is the section text of a source with the hooks taken out, so the
// command below reproduces the committed fixture from the source it was made
// from, and `hooks` is never regenerated: a hook that moved is edited by hand
// in the fixture, where the test then requires it exactly once.
//
// On this branch the pane-retirement rule changed `onKill` (and the pane
// template got the runtime-lost event) on purpose, so for those sections the
// baseline is the branch's text, not origin/main's. Regenerate after main
// itself changes one of these, or after a deliberate change to a non-ACP line:
//   node src/renderer/src/components/__tests__/fixtures/acpUnchanged.mjs <commit | WORKTREE>
// `WORKTREE` reads the files on disk.

import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const FIXTURE = resolve(dirname(fileURLToPath(import.meta.url)), 'acp-unchanged-main.json')

/** A top-level `function name(` / `async function name(` through its closing `}` line. */
function functionText(lines, name) {
  const start = lines.findIndex((l) => l.startsWith(`function ${name}(`) || l.startsWith(`async function ${name}(`))
  if (start < 0) throw new Error(`no function ${name}`)
  const end = lines.findIndex((l, i) => i > start && l === '}')
  return lines.slice(start, end + 1).join('\n')
}

/** From the line equal to `start` through its matching `</template>`. */
function blockText(lines, start) {
  const first = lines.indexOf(start)
  if (first < 0) throw new Error(`no line ${start}`)
  let depth = 0
  for (let i = first; i < lines.length; i++) {
    if (/<template[\s>]/.test(lines[i])) depth++
    if (lines[i].includes('</template>')) depth--
    if (depth === 0) return lines.slice(first, i + 1).join('\n')
  }
  throw new Error(`unclosed block ${start}`)
}

export function sectionText(source, entry) {
  const lines = source.split('\n')
  return entry.kind === 'block' ? blockText(lines, entry.start) : functionText(lines, entry.name)
}

/** `text` without each hook (one or more whole lines), each required exactly once. */
export function withoutHooks(text, hooks) {
  let out = `\n${text}\n`
  for (const hook of hooks) {
    const needle = `\n${hook}\n`
    const at = out.indexOf(needle)
    if (at < 0 || out.indexOf(needle, at + 1) >= 0) throw new Error(`hook must appear exactly once: ${hook}`)
    out = out.slice(0, at) + out.slice(at + needle.length - 1)
  }
  return out.slice(1, -1)
}

/** `text` without each hook that it holds (once); a source that has none of them is main's. */
function withoutPresentHooks(text, hooks) {
  return withoutHooks(text, hooks.filter((hook) => `\n${text}\n`.includes(`\n${hook}\n`)))
}

function readSource(source, file) {
  if (source === 'WORKTREE') return readFileSync(resolve(dirname(FIXTURE), '../../../../../..', file), 'utf8')
  return execFileSync('git', ['show', `${source}:${file}`], { encoding: 'utf8', maxBuffer: 64 << 20 })
}

function regenerate(source) {
  const entries = JSON.parse(readFileSync(FIXTURE, 'utf8'))
  const cache = new Map()
  for (const entry of entries) {
    if (!cache.has(entry.file)) cache.set(entry.file, readSource(source, entry.file))
    entry.main = withoutPresentHooks(sectionText(cache.get(entry.file), entry), entry.hooks)
  }
  writeFileSync(FIXTURE, `${JSON.stringify(entries, null, 1)}\n`)
}

if (process.argv[1] === fileURLToPath(import.meta.url)) regenerate(process.argv[2])
