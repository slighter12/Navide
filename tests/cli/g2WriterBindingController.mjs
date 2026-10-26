// G2 ticket 10: a minimal controller for the ticket 04 writer binding (tests/cli/contractReporter.ts).
//
//   node tests/cli/g2WriterBindingController.mjs --dir ABSOLUTE_DIR --run-id 32HEX [--wait-ms N]
//
// The reporter writes `writer-header.json` into DIR and waits up to 2 s for `release` to hold exactly
// `RELEASE <sha256 of the header text>`. This controller waits for the header, checks that it names this run id and a
// live writer pid whose command line is a node/vitest process, writes the release, and exits 0. It is not the
// original ticket 04 owned-capture controller: it proves the writer is a live local node process and answers the
// handshake, nothing more. It exits 1 with the reason on any mismatch and writes no release then.
import { createHash } from 'node:crypto'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, all) => (index % 2 === 0 ? [...pairs, [value.replace(/^--/, ''), all[index + 1]]] : pairs), []))
const { dir, 'run-id': runId } = args
const waitMs = Number(args['wait-ms'] ?? 120000)
if (!dir || !runId || !/^[0-9a-f]{32}$/.test(runId)) {
  console.error('usage: --dir ABSOLUTE_DIR --run-id 32HEX [--wait-ms N]')
  process.exit(2)
}

const header = join(dir, 'writer-header.json')
const started = Date.now()
while (!existsSync(header)) {
  if (Date.now() - started > waitMs) {
    console.error('controller: no writer header before the deadline')
    process.exit(1)
  }
  await new Promise((resolve) => setTimeout(resolve, 10))
}
const text = readFileSync(header, 'utf8')
const record = JSON.parse(text)
const command = spawnSync('ps', ['-o', 'command=', '-p', String(record.writer_pid)], { encoding: 'utf8' }).stdout.trim()
const problems = []
if (record.scope !== 't04-writer-binding' || record.header_version !== 1) problems.push('unexpected header scope or version')
if (record.run_id !== runId) problems.push('run id mismatch')
if (!Number.isInteger(record.writer_pid) || !command) problems.push('writer pid is not a live process')
else if (!/node|vitest|bun/.test(command)) problems.push(`writer is not a node process: ${command.split(' ')[0]}`)
if (problems.length) {
  console.error(`controller: refused: ${problems.join('; ')}`)
  process.exit(1)
}
const digest = createHash('sha256').update(text).digest('hex')
writeFileSync(join(dir, 'release'), `RELEASE ${digest}`, { flag: 'wx' })
console.log(`controller: released run ${runId} writer_pid=${record.writer_pid} header_sha256=${digest}`)
