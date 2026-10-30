// G3-r2-hosted, Windows only: the entry of the `packaged-smoke` and `packaged-cold` steps of a Windows runner (the hosted plan names this file there instead of g3Matrix.test.ts; Linux and macOS keep g3Matrix.test.ts).
// Gated like g3Matrix.test.ts: the two attempts run only when ACR_G3 selects them. Copied from g3Matrix.test.ts, unchanged: `helper`, `packagedBackend`, `packagedProblems`, `freezeProblems`, the F0 load, the rehearsal refusal,
// both `it` bodies except the attempt function (`runPackagedAttemptWindows`, which takes the pane command from ACR_G3H_READY_COMMAND). The two unit tests at the bottom always run (they need no environment).
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { expect, it } from 'vitest'
import { runPackagedChain } from './g3PackagedRun'
import { PACKAGED_SCORED, PACKAGED_WARMUPS } from './g3Packaged'
import { LaunchReady } from './g3Ready'
import { isRehearsal, rehearsalPackaged, rehearsalRefusal } from './g3Rehearsal'
import { runPackagedAttemptWindows, VtNormalizer } from './g3hPackagedWindows'

const selected = process.env.ACR_G3 ?? ''
const python = resolve('backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const helper = (args: string[]): string => execFileSync(python, ['-B', '-m', 'tests.cli_regression.support.g3.helper', ...args], { cwd: resolve('backend'), encoding: 'utf8', timeout: 180_000, maxBuffer: 64 * 1024 * 1024 })
const out = resolve(process.env.ACR_G3_OUT ?? join('test-results/ci/g3', selected || 'none'))
const sidecar = process.env.ACR_G3_SIDECAR ? resolve(process.env.ACR_G3_SIDECAR) : null
const sidecarArg = sidecar ?? '-'
const root = resolve('.')
const rehearsal = isRehearsal()
const rehearsalProblem = selected ? rehearsalRefusal(out) : null      // a stray ACR_G3_REHEARSAL in the registered run fails here, before any attempt
if (rehearsalProblem) throw new Error(rehearsalProblem)

const readyCommand = (): string => {
  const command = process.env.ACR_G3H_READY_COMMAND ?? ''
  expect(command, 'ACR_G3H_READY_COMMAND (the hosted plan\'s Windows READY command) is required').not.toBe('')
  return command
}
const packagedBackend = (): string => {
  const bin = resolve(process.env.ACR_G3_PACKAGED_BACKEND ?? '')
  expect(existsSync(bin), 'ACR_G3_PACKAGED_BACKEND (the frozen backend of the --dir layout) is required').toBe(true)
  return bin
}
const packagedProblems = (): string[] => {
  const record = resolve(process.env.ACR_G3_PACKAGED_RECORD ?? '')
  expect(existsSync(record), 'ACR_G3_PACKAGED_RECORD (packaged-build.json) is required').toBe(true)
  expect(sidecar, 'ACR_G3_SIDECAR is required').not.toBeNull()
  try { return (JSON.parse(helper(['packaged-verify', root, sidecar!, record])) as { problems: string[] }).problems } catch (error) { return [`packaged-verify failed: ${String(error).slice(0, 100)}`] }
}
const freezeProblems = (f0: string): string[] => (JSON.parse(helper(['freeze-verify', root, f0, sidecarArg])) as { problems: string[] }).problems

it.runIf(selected === 'packaged-smoke')('G3 packaged cold-start smoke (Windows READY command): one attempt, pass/fail only', async () => {
  mkdirSync(out, { recursive: true })
  const problems = packagedProblems()
  const receipt = problems.length ? { passed: false } : await runPackagedAttemptWindows({ backendBin: packagedBackend(), python, repeat: 0, warmup: true, smoke: true, readyCommand: readyCommand() })
  writeFileSync(join(out, 'packaged-smoke.json'), `${JSON.stringify({ notEvidence: true, passed: receipt.passed === true, artifactProblems: problems }, null, 1)}\n`)
  process.stdout.write(`packaged smoke: ${receipt.passed === true ? 'clean' : 'invalid'}\n`)
  expect(receipt.passed).toBe(true)
}, 600_000)

it.runIf(selected === 'packaged')('G3 packaged cold start (Windows READY command, descriptive): one warm-up and 10 scored attempts, the first invalid one ends the run', async () => {
  const f0 = resolve(process.env.ACR_G3_FREEZE!)
  expect(existsSync(f0), 'ACR_G3_FREEZE (F0) is required').toBe(true)
  expect(sidecar, 'ACR_G3_SIDECAR is required (the toolchain check covers the sidecar before every attempt)').not.toBeNull()
  const bin = packagedBackend()
  const command = readyCommand()
  const directory = join(out, 'packaged-trials')
  mkdirSync(directory, { recursive: true })
  const planned = [...Array.from({ length: PACKAGED_WARMUPS }, (_, repeat) => ({ repeat, warmup: true })), ...Array.from({ length: PACKAGED_SCORED }, (_, repeat) => ({ repeat, warmup: false }))]
  const attempts = rehearsal ? rehearsalPackaged(planned) : planned
  const record = await runPackagedChain(attempts, {
    freezeProblems: () => freezeProblems(f0), artifactProblems: packagedProblems,
    run: (attempt) => runPackagedAttemptWindows({ backendBin: bin, python, repeat: attempt.repeat, warmup: attempt.warmup, readyCommand: command }),
    save: (name, receipt) => writeFileSync(join(directory, `${name}.json`), `${JSON.stringify(receipt, null, 1)}\n`),
    emit: (line) => process.stdout.write(`${line}\n`),
  })
  writeFileSync(join(directory, 'run.json'), `${JSON.stringify(record, null, 1)}\n`)
  expect(record.complete, 'the packaged run ended early: the step fails and the run record carries it').toBe(true)
}, 1_800_000)

// ---- unit tests of the normaliser (no environment, no process)
const bytes = (text: string): Uint8Array => Buffer.from(text, 'latin1')
const text = (data: Uint8Array): string => Buffer.from(data).toString('latin1')

it('VtNormalizer removes title, cursor and mode sequences and keeps the text and the line ends', () => {
  const normalizer = new VtNormalizer()
  expect(text(normalizer.push(bytes('\x1b[?25l\x1b[2J\x1b[H\x1b]0;C:\\py\\python.exe\x07BASELINE_READY {"packaged":true}\r\n\x1b[?25h')))).toBe('BASELINE_READY {"packaged":true}\r\n')
  expect(text(normalizer.push(bytes('\x1b]0;title\x1b\\x')))).toBe('x')
})

it('VtNormalizer turns a cursor-forward move into blanks and holds a split sequence until its end arrives', () => {
  const normalizer = new VtNormalizer()
  expect(text(normalizer.push(bytes('BASELINE_READY\x1b[1C{')))).toBe('BASELINE_READY {')
  expect(text(normalizer.push(bytes('a\x1b[3Cb')))).toBe('a   b')
  const first = normalizer.push(bytes('x\x1b['))
  const second = normalizer.push(bytes('2Cy'))
  expect(text(first) + text(second)).toBe('x  y')
})

it('the READY waiter matches the ConPTY-shaped output only after normalisation', () => {
  const frame = bytes('\x1b[?25l\x1b]0;python.exe\x07BASELINE_READY\x1b[1C{"packaged":true}\r\n')
  const raw = new LaunchReady()
  raw.onFrame('t', 1, frame)
  expect(raw.stream.toBuffer().toString('latin1').includes('BASELINE_READY ')).toBe(false)
  const normalizer = new VtNormalizer()
  const ready = new LaunchReady()
  ready.onFrame('t', 1, normalizer.push(frame))
  const seen = ready.stream.toBuffer().toString('latin1')
  expect(seen.includes('BASELINE_READY ') && seen.includes('\n', seen.indexOf('BASELINE_READY '))).toBe(true)
})
