// G2 ticket 07 hosted-run helpers, shared by the Linux and Windows eval workflows.
//
//   guard    --platform P --arch A --required FILE --junit F... [--out FILE]
//            Fails unless every required case is in the junit files and passed: a missing,
//            skipped, xfailed, failed or errored case fails the run. Writes redacted outcomes.
//   receipts --root DIR --sha HEX
//            Fails unless at least one `ready` receipt exists and every one carries that hash.
//   proof    --release BIN --harness BIN
//            Fails unless the harness binary carries the fault-hooks literal and the release one does not.
//
// Outcome lines name runtime, platform, architecture, case and stage; no failure text is copied.
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const FAULT_HOOKS_LITERAL = Buffer.from('fault-graceful')

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

export function requiredIds(text) {
  return text.split(/\r?\n/).map(line => line.trim()).filter(line => line.includes('::') && !line.includes(' '))
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

function receipts(args) {
  let ready = 0
  const foreign = new Set()
  for (const file of findReceipts(args.root)) {
    for (const line of readFileSync(file, 'utf8').split('\n')) {
      if (!line.includes('"ready"')) continue
      let record
      try {
        record = JSON.parse(line)
      } catch {
        continue
      }
      if (record.event !== 'ready') continue
      ready += 1
      if (record.executableSha256 !== args.sha) foreign.add(String(record.executableSha256))
    }
  }
  console.log(`ready receipts=${ready} expected executableSha256=${args.sha} foreign hashes=${foreign.size}`)
  for (const hash of foreign) console.log(`FOREIGN ${hash}`)
  return ready > 0 && foreign.size === 0 ? 0 : 1
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
