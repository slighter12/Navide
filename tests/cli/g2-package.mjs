// G2 ticket 08: verify a real `electron-builder --dir` unpacked app on this host.
//
//   node tests/cli/g2-package.mjs verify --unpacked DIR --arch x64|arm64 --out DIR
//
// 1. DIR is the unpacked app (`dist-release/<os>-unpacked`, `mac-arm64/Navide.app/Contents/Resources` parent resolved
//    here); its `<resources>/bin` listing must equal what tests/cli/packagedLayout.mjs assembles from package.json
//    out of the same build outputs (runtime, backend, and STT or the sidecars that were built).
// 2. The packaged smoke (tests/cli/packaged_runtime_smoke.py) runs its four scenarios against that real resources dir.
// 3. A redacted summary (revision, target, scenarios, outcomes, listing) is written to OUT; no logs, tokens or paths
//    outside the unpacked app are archived. Exit 1 unless every scenario passed and the listings are equal.

import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
// @ts-ignore -- plain ESM helper
import { assembleLayout, platformKey } from './packagedLayout.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const scenarios = ['positive', 'missing', 'wrong-arch', 'protocol']

function resourcesOf(unpacked) {
  for (const candidate of [join(unpacked, 'resources'), join(unpacked, 'Navide.app', 'Contents', 'Resources')]) {
    if (existsSync(join(candidate, 'bin'))) return candidate
  }
  throw new Error(`no resources/bin under ${unpacked}`)
}

function listing(dir) {
  return readdirSync(dir).sort()
}

function main() {
  const args = process.argv.slice(2)
  if (args[0] !== 'verify') throw new Error('usage: verify --unpacked DIR --arch ARCH --out DIR')
  const option = (name) => args[args.indexOf(name) + 1]
  const unpacked = resolve(option('--unpacked'))
  const out = resolve(option('--out'))
  const arch = option('--arch')
  mkdirSync(out, { recursive: true })

  const resources = resourcesOf(unpacked)
  const real = listing(join(resources, 'bin'))
  const simulatedRoot = join(out, 'simulated')
  const { staged } = assembleLayout({ out: simulatedRoot, key: platformKey() })
  const simulated = listing(join(simulatedRoot, 'bin'))
  const listingEqual = JSON.stringify(real) === JSON.stringify(simulated)
  console.log(`real bin:      ${real.join(', ')}`)
  console.log(`simulated bin: ${simulated.join(', ')} (staged ${staged.join(', ')})`)

  const results = []
  for (const scenario of scenarios) {
    const run = spawnSync('uv', ['--project', 'backend', 'run', 'python', '-I', 'tests/cli/packaged_runtime_smoke.py',
      '--layout', resources, '--scenario', scenario, '--out', join(out, 'smoke')], { cwd: root, encoding: 'utf8' })
    process.stdout.write(run.stdout ?? '')
    process.stderr.write(run.stderr ?? '')
    let detail = {}
    try { detail = JSON.parse(readFileSync(join(out, 'smoke', `${scenario}.json`), 'utf8')) } catch { /* no result file */ }
    if (detail.runtime_diagnostics || detail.binfmt_misc) {
      console.log(`DETAIL ${scenario} host_spawn=${JSON.stringify(detail.host_spawn_of_the_same_file)} diagnostics=${JSON.stringify(detail.runtime_diagnostics)} binfmt_misc=${JSON.stringify(detail.binfmt_misc)} failures=${JSON.stringify(detail.failures)}`)
    }
    results.push({ scenario, outcome: run.status === 0 ? 'pass' : 'fail', ...detail })
  }

  const summary = {
    revision: process.env.GITHUB_SHA ?? 'local', platform: process.platform, arch, listingEqual,
    realBin: real, simulatedBin: simulated, scenarios: results
  }
  writeFileSync(join(out, 'summary.json'), JSON.stringify(summary, null, 1))
  const failed = results.filter((result) => result.outcome !== 'pass')
  console.log(`PACKAGE-SUMMARY revision=${summary.revision} platform=${process.platform} arch=${arch} listingEqual=${listingEqual} ` +
    results.map((result) => `${result.scenario}=${result.outcome}`).join(' '))
  process.exit(listingEqual && failed.length === 0 ? 0 : 1)
}

main()
