// G2 ticket 10 part A helpers: the packaged-artifact freshness check and the required-case matrix.
//
//   artifact-check --head SHA --summary FILE... [--out FILE]
//       Fails if a packaged `summary.json` names a revision other than HEAD and the code that is packaged
//       (native/, backend/agent_team_backend/, shared/, package.json, src/) differs between that revision and HEAD,
//       or if its listing differs, a scenario did not pass, or the staged and executed runtime digests differ.
//   matrix --out FILE --label=PATH...  (labels: linux-x64 linux-arm64 windows-x64 windows-arm64 mac)
//       Reads the redacted outcomes logs (case= status= stage=) and, for macOS, a pytest junit file, and writes
//       a per-function matrix plus the list of every id that is not `passed` on a platform that lists it.
import { spawnSync } from 'node:child_process'
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { readJunit, requiredIds, stageOf } from './g2Native.mjs'

const PACKAGED_PATHS = ['native', 'backend/agent_team_backend', 'shared', 'package.json', 'src']

function git(...args) {
  const run = spawnSync('git', args, { encoding: 'utf8' })
  return { status: run.status, out: (run.stdout ?? '').trim(), err: (run.stderr ?? '').trim() }
}

function artifactCheck(args) {
  const lines = []
  let failed = 0
  for (const file of args.summary) {
    const summary = JSON.parse(readFileSync(file, 'utf8'))
    const target = `${summary.platform}-${summary.arch}`
    const problems = []
    let note = 'revision equals head'
    if (summary.revision !== args.head) {
      const ancestor = git('merge-base', '--is-ancestor', summary.revision, args.head).status === 0
      const changed = git('diff', '--name-only', summary.revision, args.head, '--', ...PACKAGED_PATHS)
      if (!ancestor || changed.status !== 0) problems.push(`revision ${summary.revision} is not an ancestor of head`)
      else if (changed.out) problems.push(`predates the final code: ${changed.out.split('\n').length} packaged-path files differ (${changed.out.split('\n')[0]} ...)`)
      else note = `revision ${summary.revision} differs from head but no packaged path changed`
    }
    if (!summary.listingEqual) problems.push('real and simulated bin listings differ')
    if (summary.base_negative?.outcome !== 'pass') problems.push('base negative did not pass')
    const positive = summary.scenarios.find(s => s.scenario === 'positive')
    if (positive?.identity?.executableSha256 !== positive?.identity?.stagedSha256) problems.push('executed runtime digest differs from the staged one')
    for (const scenario of summary.scenarios) if (scenario.outcome !== 'pass') problems.push(`scenario ${scenario.scenario} ${scenario.outcome}`)
    if (problems.length) failed += 1
    lines.push(`${problems.length ? 'FAIL' : 'ok  '} ${target} revision=${summary.revision} runtimeSha256=${positive?.identity?.executableSha256 ?? 'none'} ${problems.length ? problems.join('; ') : note}`)
  }
  lines.push(`head=${args.head} artifacts=${args.summary.length} failed=${failed}`)
  console.log(lines.join('\n'))
  if (args.out) writeFileSync(args.out, `${lines.join('\n')}\n`)
  return failed === 0 ? 0 : 1
}

function readOutcomes(file) {
  const results = new Map()
  for (const line of readFileSync(file, 'utf8').split('\n')) {
    const match = /case=(\S+(?: \S+)*?) status=(\S+) stage=/.exec(line)
    if (match) results.set(match[1], match[2])
  }
  return results
}

const split = id => {
  const [file, rest] = id.split('::')
  const open = rest.indexOf('[')
  return { fn: `${file.split('/').pop()}::${open < 0 ? rest : rest.slice(0, open)}`, param: open < 0 ? '' : rest.slice(open + 1, -1) }
}

function matrix(args) {
  const platforms = ['mac', 'linux-x64', 'linux-arm64', 'windows-x64', 'windows-arm64']
  const data = new Map()
  for (const name of platforms) {
    if (!args[name]) continue
    data.set(name, name === 'mac'
      ? new Map(args[name].split(',').flatMap(file => [...readJunit(readFileSync(file, 'utf8'))]))
      : readOutcomes(args[name]))
  }
  // macOS runs the Linux required list: its ids are the Linux ones that exist in the junit.
  const linuxIds = requiredIds(readFileSync(args['linux-required'], 'utf8'))
  const mac = data.get('mac')
  const rows = new Map()
  const bad = []
  const cell = (name, id) => {
    const status = data.get(name)?.get(id)
    return status
  }
  const universe = new Set([...linuxIds, ...(data.get('windows-x64')?.keys() ?? [])])
  for (const id of universe) {
    const { fn } = split(id)
    const row = rows.get(fn) ?? { stage: stageOf(id), counts: Object.fromEntries(platforms.map(p => [p, [0, 0]])) }
    for (const name of platforms) {
      if (!data.has(name)) continue
      const isWindowsId = id.includes('windows') || id.includes('_windows')
      const listed = name.startsWith('windows') ? (data.get(name).has(id)) : linuxIds.includes(id)
      if (!listed) continue
      const status = cell(name, id) ?? 'missing'
      row.counts[name][1] += 1
      if (status === 'passed') row.counts[name][0] += 1
      else bad.push(`${name} ${status}: ${id}`)
      void isWindowsId
    }
    rows.set(fn, row)
  }
  const out = [`| stage | case function | ${platforms.map(p => p).join(' | ')} |`, `|---|---|${platforms.map(() => '---').join('|')}|`]
  for (const [fn, row] of [...rows].sort((a, b) => a[1].stage.localeCompare(b[1].stage) || a[0].localeCompare(b[0]))) {
    out.push(`| ${row.stage} | \`${fn}\` | ${platforms.map(p => (row.counts[p][1] ? `${row.counts[p][0]}/${row.counts[p][1]}` : '–')).join(' | ')} |`)
  }
  const totals = platforms.map(p => {
    const [pass, total] = [...rows.values()].reduce((acc, r) => [acc[0] + r.counts[p][0], acc[1] + r.counts[p][1]], [0, 0])
    return `${p}: ${pass}/${total}`
  })
  // Vendor coverage: every registered vendor (shared/agent-cli/*.json) must have passed cases in each stage it is
  // expected to have; a vendor with no passed id on a platform is a visible zero, never an absent row.
  const vendors = readdirSync('shared/agent-cli').filter(name => name.endsWith('.json')).map(name => name.replace(/\.json$/, '')).sort()
  const letter = { composition: 'c', observation: 'o', 'cleanup-and-recovery': 'r', 'launch-contract': 'l' }
  const grid = [`| vendor | ${platforms.join(' | ')} |`, `|---|${platforms.map(() => '---').join('|')}|`]
  for (const vendor of vendors) {
    const pattern = new RegExp(`[\\[-]${vendor}(?=[\\]-])`)
    const cells = platforms.map(name => {
      if (!data.has(name)) return '–'
      const tally = {}
      const ids = name.startsWith('windows') ? [...data.get(name).keys()] : linuxIds
      for (const id of ids) {
        const param = id.slice(id.indexOf('['))
        if (!pattern.test(param)) continue
        if (cell(name, id) !== 'passed') continue
        const key = letter[stageOf(id)]
        tally[key] = (tally[key] ?? 0) + 1
      }
      return Object.entries(tally).map(([key, count]) => `${key}${count}`).join(' ') || '**none**'
    })
    grid.push(`| ${vendor} | ${cells.join(' | ')} |`)
  }
  out.push('', `Totals (passed/listed): ${totals.join('; ')}`, '', '## Vendor coverage (passed ids by stage: c composition, o observation, r cleanup, l launch)', '', ...grid, '', `Not passed on a platform that lists the id: ${bad.length}`, ...bad.map(line => `- ${line}`))
  writeFileSync(args.out, `${out.join('\n')}\n`)
  console.log(totals.join('\n'))
  console.log(`not passed: ${bad.length}`)
  for (const line of bad) console.log(line)
  return bad.length === 0 ? 0 : 1
}

const [command, ...rest] = process.argv.slice(2)
if (command) {
  const args = { summary: [] }
  for (let i = 0; i < rest.length; i += 2) {
    const key = rest[i].replace(/^--/, '')
    if (key === 'summary') args.summary.push(rest[i + 1])
    else args[key] = rest[i + 1]
  }
  const handlers = { 'artifact-check': artifactCheck, matrix }
  if (!handlers[command]) {
    console.error(`unknown command: ${command}`)
    process.exit(2)
  }
  process.exit(handlers[command](args))
}
