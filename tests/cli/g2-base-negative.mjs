// G2 ticket 08: the base-commit negative on this host.
//
//   node tests/cli/g2-base-negative.mjs --base-sha SHA --out DIR
//
// Builds the BASE commit's frozen backend (its own cli_runtime.py lookup) from a detached worktree, assembles the
// packaged layout from the BASE package.json (so no CLI runtime is staged: backend, and STT when built), proves the
// layout holds no navide-cli-runtime, and runs the head's packaged positive smoke against it. The smoke must FAIL, with
// the base lookup's own "Rust runtime binary missing; build native/navide-cli-runtime explicitly" (SETUP_ERROR).
// Writes DIR/base-negative.json; exit 1 unless every one of those holds. `g2-package.mjs verify` requires that file.

import { spawnSync } from 'node:child_process'
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
// @ts-ignore -- plain ESM helper
import { assembleLayout, platformKey } from './packagedLayout.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const MISSING = 'Rust runtime binary missing; build native/navide-cli-runtime explicitly'

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, { stdio: 'inherit', cwd })
  if (result.error || result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed (${result.status ?? result.error})`)
}

const args = process.argv.slice(2)
const option = (name) => args[args.indexOf(name) + 1]
const baseSha = option('--base-sha')
const out = resolve(option('--out'))
mkdirSync(out, { recursive: true })

const worktree = join(tmpdir(), 'g2-base-worktree')
run('git', ['fetch', '--no-tags', '--depth=1', 'origin', baseSha])
run('git', ['worktree', 'add', '--detach', worktree, baseSha])
const baseBackend = join(worktree, 'backend')
run('uv', ['--project', baseBackend, 'sync', '--locked'])
const dist = join(out, 'base-dist')
run('uv', ['--project', baseBackend, 'run', '--locked', 'pyinstaller', '--noconfirm', '--distpath', dist,
  '--workpath', join(out, 'base-build'), 'agent_team_backend.spec'], baseBackend)

const layout = join(out, 'base-layout', 'resources')
const { staged, missing } = assembleLayout({
  out: layout, key: platformKey(), repo: worktree,
  maps: { 'backend/dist': dist, 'build/stt': join(root, 'build', 'stt') }
})
const bin = readdirSync(join(layout, 'bin')).sort()
console.log(`base layout bin: ${bin.join(', ')} (staged ${staged.join(', ')}; absent ${missing.join(', ')})`)
const hasRuntime = bin.some((name) => name.startsWith('navide-cli-runtime'))
const hasBackend = bin.some((name) => name.startsWith('agent_team_backend'))

spawnSync('uv', ['--project', 'backend', 'run', 'python', '-I', 'tests/cli/packaged_runtime_smoke.py', '--layout', layout,
  '--scenario', 'positive', '--out', join(out, 'base-smoke')], { stdio: 'inherit', cwd: root })
const positive = JSON.parse(readFileSync(join(out, 'base-smoke', 'positive.json'), 'utf8'))
const error = String(positive.error ?? '')
const failedAsExpected = positive.outcome === 'fail' && error.includes('SETUP_ERROR') && error.includes(MISSING)

const summary = {
  baseSha, bin, hasRuntime, hasBackend, positiveOutcome: positive.outcome,
  cause: error.includes(MISSING) ? MISSING : error.slice(0, 300), setupError: error.includes('SETUP_ERROR'),
  outcome: !hasRuntime && hasBackend && failedAsExpected ? 'pass' : 'fail'
}
writeFileSync(join(out, 'base-negative.json'), JSON.stringify(summary, null, 1))
console.log(`BASE-NEGATIVE base=${baseSha} hasRuntime=${hasRuntime} positive=${positive.outcome} cause=${summary.cause} outcome=${summary.outcome}`)
process.exit(summary.outcome === 'pass' ? 0 : 1)
