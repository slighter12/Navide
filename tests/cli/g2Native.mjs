// G2 ticket 07 hosted-run helpers, shared by the Linux and Windows eval workflows.
//
//   guard    --platform P --arch A --required FILE --junit F... [--out FILE]
//            Fails unless every required case is in the junit files and passed: a missing,
//            skipped, xfailed, failed or errored case fails the run. Writes redacted outcomes.
//   receipts --root DIR --sha HEX [--required FILE --expect FILE --harness-sha HEX --stubs name=HEX,...]
//            Fails unless a `ready` receipt exists and every one carries the release hash, except in the
//            exact test directories the expectations file lists, which may hold only their declared kinds
//            (harness build or named stub peer) and must show each of them.
//   proof    --release BIN --harness BIN
//            Fails unless the harness binary carries the fault-hooks literal and the release one does not.
//
// Outcome lines name runtime, platform, architecture, case and stage; no failure text is copied.
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// A message literal that exists only inside #[cfg(feature = "fault-hooks")] code in posix.rs. (The
// `fault-graceful` constant is compared inline and does not survive as one contiguous string.)
const FAULT_HOOKS_LITERAL = Buffer.from('fault: exit watcher could not start')

function parseArgs(argv) {
  const args = { junit: [] }
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i].replace(/^--/, '')
    if (key === 'junit') args.junit.push(argv[i + 1])
    else args[key] = argv[i + 1]
  }
  return args
}

function unescapeXml(text) {
  return text
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/&amp;/g, '&')
}

function attr(attrs, name) {
  const match = new RegExp(`\\b${name}="([^"]*)"`).exec(attrs)
  return match ? unescapeXml(match[1]) : ''
}

const STATUS_RANK = { passed: 0, skipped: 1, xfailed: 2, failed: 3, error: 4 }

export function readJunit(text) {
  const results = new Map()
  for (const match of text.matchAll(/<testcase\b([^>]*?)(?:\/>|>([\s\S]*?)<\/testcase>)/g)) {
    const classname = attr(match[1], 'classname')
    const name = attr(match[1], 'name')
    const body = match[2] ?? ''
    // backend/tests is the pytest rootdir's tests/ package, so classname tests.cli_regression.test_x
    // maps to the node id's tests/cli_regression/test_x.py.
    const id = `${classname.replace(/\./g, '/')}.py::${name}`
    let status = 'passed'
    if (/<error\b/.test(body)) status = 'error'
    else if (/<failure\b/.test(body)) status = 'failed'
    else if (/<skipped\b/.test(body)) status = /xfail/i.test(body) ? 'xfailed' : 'skipped'
    const seen = results.get(id)
    if (!seen || STATUS_RANK[status] > STATUS_RANK[seen]) results.set(id, status)
  }
  return results
}

export function stageOf(id) {
  if (id.includes('composition')) return 'composition'
  if (id.includes('observe')) return 'observation'
  if (/recovery|lifecycle|handoff|sidecar/.test(id)) return 'cleanup-and-recovery'
  return 'launch-contract'
}

// Blank lines and `#` comments are skipped; any other line must be a node id, and an id may appear once, so
// a malformed or repeated line fails loudly instead of silently shrinking or padding the enforced set.
export function requiredIds(text) {
  const ids = new Set()
  for (const [index, raw] of text.split(/\r?\n/).entries()) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    if (!/^[\w./-]+\.py::\S/.test(line)) throw new Error(`required list line ${index + 1} is not a node id: ${line}`)
    if (ids.has(line)) throw new Error(`required list line ${index + 1} repeats an id: ${line}`)
    ids.add(line)
  }
  return [...ids]
}

function guard(args) {
  const required = requiredIds(readFileSync(args.required, 'utf8'))
  const results = new Map()
  for (const file of args.junit) {
    for (const [id, status] of readJunit(readFileSync(file, 'utf8'))) {
      const seen = results.get(id)
      if (!seen || STATUS_RANK[status] > STATUS_RANK[seen]) results.set(id, status)
    }
  }
  const prefix = `runtime=navide-cli-runtime platform=${args.platform} arch=${args.arch}`
  const lines = []
  const bad = []
  const counts = {}
  for (const id of required) {
    const status = results.get(id) ?? 'missing'
    counts[status] = (counts[status] ?? 0) + 1
    lines.push(`${prefix} case=${id} status=${status} stage=${stageOf(id)}`)
    if (status !== 'passed') bad.push(`${status}: ${id}`)
  }
  // A skip outside the required list is accepted only for the files declared platform-gated, so a case
  // that silently stopped running cannot hide among the unlisted ones.
  const gated = new Set((args['skip-allowed'] ?? '').split(',').filter(Boolean))
  for (const [id, status] of results) {
    if (status !== 'skipped' && status !== 'xfailed') continue
    if (gated.has(id.split('::')[0].split('/').pop())) continue
    counts[`unlisted-${status}`] = (counts[`unlisted-${status}`] ?? 0) + 1
    bad.push(`unlisted ${status} outside the platform-gated files: ${id}`)
  }
  if (args.out) writeFileSync(args.out, `${lines.join('\n')}\n`)
  console.log(`required=${required.length} ${Object.entries(counts).map(([k, v]) => `${k}=${v}`).join(' ')}`)
  if (required.length === 0) {
    console.log('no required cases listed')
    return 1
  }
  for (const entry of bad.slice(0, 25)) console.log(`NOT PASSED ${entry}`)
  if (bad.length > 25) console.log(`... and ${bad.length - 25} more (see the outcomes log)`)
  return bad.length === 0 ? 0 : 1
}

function findReceipts(root, found = []) {
  // Dirents only: a test tree holds dangling and looping symlinks that must never be followed.
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) findReceipts(path, found)
    else if (entry.isFile() && entry.name === 'runtime-receipts.jsonl') found.push(path)
  }
  return found
}

// pytest names a test's temp directory after the test function cut to 30 characters plus a counter.
export function dirStem(dir) {
  return dir.replace(/^(.*?)\d+$/, '$1')
}

// Every receipt directory not named in the expectations may hold only release-sidecar ready receipts. A
// listed directory (a test that substitutes the fault-hooks harness sidecar or its own stub peer) may hold
// only the hashes of its declared kinds, and must show every declared kind: a listed test that stopped
// spawning its sidecar fails the guard rather than passing by absence.
export function checkReceipts({ dirs, release, harness, expectations, stubHashes, requiredNames }) {
  const problems = []
  const cases = new Map((expectations?.cases ?? []).map(entry => [entry.dir, entry]))
  const hashOf = new Map([[release, 'release']])
  if (harness) hashOf.set(harness, 'harness')
  for (const [name, hash] of Object.entries(stubHashes)) hashOf.set(hash, `stub:${name}`)
  const seen = new Map()
  let ready = 0
  for (const [dir, hashes] of dirs) {
    const stem = dirStem(dir)
    const allowed = cases.get(stem)?.kinds ?? ['release']
    for (const hash of hashes) {
      ready += 1
      const kind = hashOf.get(hash)
      if (!kind || !allowed.includes(kind)) problems.push(`foreign ${kind ?? hash} in ${dir} (allowed: ${allowed.join(',')})`)
      else seen.set(stem, new Set([...(seen.get(stem) ?? []), kind]))
    }
  }
  for (const [stem, entry] of cases) {
    for (const kind of entry.kinds) if (!seen.get(stem)?.has(kind)) problems.push(`missing ${kind} evidence in ${stem}`)
    for (const test of entry.tests) if (!requiredNames.has(test)) problems.push(`expectation names ${test}, which is not a required case`)
  }
  if (ready === 0) problems.push('no ready receipt found')
  return { ready, problems }
}

function receipts(args) {
  const dirs = new Map()
  for (const file of findReceipts(args.root)) {
    const dir = file.split('/').slice(-2)[0]
    const hashes = dirs.get(dir) ?? new Set()
    for (const line of readFileSync(file, 'utf8').split('\n')) {
      if (!line.includes('"ready"')) continue
      let record
      try {
        record = JSON.parse(line)
      } catch {
        continue
      }
      if (record.event === 'ready') hashes.add(String(record.executableSha256))
    }
    if (hashes.size) dirs.set(dir, hashes)
  }
  const expectations = args.expect ? JSON.parse(readFileSync(args.expect, 'utf8')) : undefined
  const stubHashes = { ...(expectations?.stubs ?? {}) }
  for (const pair of (args.stubs ?? '').split(',').filter(Boolean)) {
    const [name, hash] = pair.split('=')
    stubHashes[name] = hash
  }
  const requiredNames = new Set(
    args.required ? requiredIds(readFileSync(args.required, 'utf8')).map(id => id.split('::')[1].split('[')[0]) : [],
  )
  const { ready, problems } = checkReceipts({
    dirs,
    release: args.sha,
    harness: args['harness-sha'],
    expectations,
    stubHashes,
    requiredNames,
  })
  console.log(`ready receipts=${ready} expected executableSha256=${args.sha} problems=${problems.length}`)
  for (const problem of problems.slice(0, 25)) console.log(`RECEIPT ${problem}`)
  return problems.length === 0 ? 0 : 1
}

function proof(args) {
  const release = readFileSync(args.release)
  const harness = readFileSync(args.harness)
  const sha = buffer => createHash('sha256').update(buffer).digest('hex')
  const inRelease = release.includes(FAULT_HOOKS_LITERAL)
  const inHarness = harness.includes(FAULT_HOOKS_LITERAL)
  console.log(`release sha256=${sha(release)} carries fault-hooks literal=${inRelease}`)
  console.log(`harness sha256=${sha(harness)} carries fault-hooks literal=${inHarness}`)
  return !inRelease && inHarness ? 0 : 1
}

const [command, ...rest] = process.argv.slice(2)
if (command && process.argv[1]?.endsWith('g2Native.mjs')) {
  const args = parseArgs(rest)
  const handlers = { guard, receipts, proof }
  if (!handlers[command]) {
    console.error(`unknown command: ${command}`)
    process.exit(2)
  }
  process.exit(handlers[command](args))
}
