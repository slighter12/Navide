// G3 entry point (ticket 10, procedure step 3). Gated like g1eMatrix.test.ts: it does nothing unless ACR_G3 selects a mode, and it never runs by default.
//   ACR_G3=smoke             the pre-F0 plumbing smoke (not evidence; needs ACR_G3_SIDECAR and ACR_G3_OUT, no F0)
//   ACR_G3=packaged-smoke    pre-F0: one packaged cold-start attempt, pass/fail only (ACR_G3_PACKAGED_BACKEND, ACR_G3_OUT)
//   ACR_G3=packaged          descriptive packaged cold start, after the launch chains: 1 warm-up + 10 scored (needs ACR_G3_FREEZE, ACR_G3_SIDECAR, ACR_G3_PACKAGED_BACKEND)
//   ACR_G3=calibrate         one discarded Python warm-up + 20 Python-only shell attempts (the g1e calibration order for the G3 seed); needs ACR_G3_FREEZE (F0)
//   ACR_G3=launch-calibrate  Python-only agent-launch chains (4 warm-ups + 40 scored)
//   ACR_G3=matrix            two discarded warm-ups (cell 4, Python then Rust), then the 40 attempts of the frozen schedule; needs ACR_G3_FREEZE and ACR_G3_F1
//   ACR_G3=launch            the four agent-launch chains (8 warm-ups + 80 scored); needs ACR_G3_FREEZE and ACR_G3_F1
// Common: ACR_G3_OUT, ACR_G3_SIDECAR (smoke: the F0/F1 variables are not read). The shell attempt is `g3ShellAttempt.runAttempt` (g1eAttempt.ts plus the one declared wire-version substitution, see attempt_copy.py); `freezeCheckedInputs` is the helper's `checked-inputs` count; before EVERY attempt the freeze (F0, or F1 and F0) and the toolchain are re-checked through the G3
// helper (backend g3/helper.py), exactly as g1eMatrix.test.ts does with the g1e helper. An invalid attempt ends the run (no retry). Matrix and launch runs are blind: only `clean|invalid` is printed.
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { expect, it } from 'vitest'
import { redactDiagnostic } from './baselineArtifacts'
import { runAttempt, type AttemptSpec, type FreezeBinding } from './g3ShellAttempt'
import { trialFileNames, warmupFileNames } from './g1eReceipt'
import { attemptName, blindLine, classifyAttempt, isBlind, keepRefusal, RunLog, toolchainMismatchReceipt, type RunKind, type ToolchainRecord } from './g1eRun'
import { attemptsFromSchedule, calibrationOrder, makeSchedule, matrixWarmups } from './g1eSchedule'
import { runLaunchAttempt } from './g3LaunchAttempt'
import { launchLabel, runLaunchChain, type LaunchPlanned } from './g3LaunchRun'
import { runSmoke } from './g3Smoke'
import { runPackagedChain } from './g3PackagedRun'
import { PACKAGED_SCORED, PACKAGED_WARMUPS, runPackagedAttempt } from './g3Packaged'
import { agentLaunchPlan, type AgentLaunchAttempt } from './g3AgentLaunch'
import { isRehearsal, rehearsalLaunchCalibration, rehearsalLaunchMatrix, rehearsalMatrix, rehearsalPackaged, rehearsalRefusal, rehearsalShellCalibration } from './g3Rehearsal'
import type { RuntimeMode } from './runtimeMatched'

const selected = process.env.ACR_G3 ?? ''
const python = resolve('backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const helper = (args: string[]): string => execFileSync(python, ['-B', '-m', 'tests.cli_regression.support.g3.helper', ...args], { cwd: resolve('backend'), encoding: 'utf8', timeout: 180_000, maxBuffer: 64 * 1024 * 1024 })
const sha256 = (data: Buffer | string): string => createHash('sha256').update(data).digest('hex')
const out = resolve(process.env.ACR_G3_OUT ?? join('test-results/ci/g3', selected || 'none'))
const sidecar = process.env.ACR_G3_SIDECAR ? resolve(process.env.ACR_G3_SIDECAR) : null
const sidecarArg = sidecar ?? '-'
const root = resolve('.')
const rehearsal = isRehearsal()
const rehearsalProblem = rehearsalRefusal(out)      // G3-r2: a stray ACR_G3_REHEARSAL in the registered run fails here, before any attempt
if (rehearsalProblem) throw new Error(rehearsalProblem)

type Planned = Omit<AttemptSpec, 'freeze' | 'python' | 'sidecarBin' | 'fixtureRegistrySha256' | 'logPolicySha256' | 'toolchain' | 'expectedReadyVersion'> & { warmupIndex?: number }
type Paths = { f0: string; f1: string | null }

const freezeProblems = (paths: Paths): string[] => (JSON.parse(helper(['freeze-verify', root, paths.f0, sidecarArg, ...(paths.f1 ? [paths.f1] : [])])) as { problems: string[] }).problems
const toolchainCheck = (paths: Paths): ToolchainRecord => JSON.parse(helper(['toolchain-check', root, paths.f0, sidecarArg])) as ToolchainRecord

function binding(paths: Paths): FreezeBinding {
  const freeze = JSON.parse(readFileSync(paths.f0, 'utf8'))
  return { sha256: sha256(readFileSync(paths.f1 ?? paths.f0)), checkedInputs: (JSON.parse(helper(['checked-inputs', paths.f0])) as { checkedInputs: number }).checkedInputs, evidenceRevision: freeze.evidenceRevision, methodRevision: freeze.methodRevision, sidecarSha256: freeze.sidecarSha256, vendors: freeze.vendors }
}

function save(directory: string, attempt: Planned, receipt: Record<string, unknown>, resourcesJsonl: string): void {
  const names = attempt.warmup ? warmupFileNames(attempt.mode, attempt.warmupIndex ?? 0) : trialFileNames(attempt.mode, attempt.count, attempt.repeat)
  const target = join(directory, names.trial)
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, `${redactDiagnostic(JSON.stringify(receipt, null, 1))}\n`)
  writeFileSync(join(directory, names.resources), resourcesJsonl)
}

function finish(kind: RunKind, log: RunLog, total: number, directory: string): void {
  writeFileSync(join(directory, 'run.json'), `${JSON.stringify(log.toRecord(total), null, 1)}\n`)
  writeFileSync(join(directory, `${kind}-summary.txt`), `${log.lines().join('\n')}\n`)
}

async function runShell(kind: RunKind, attempts: Planned[], directory: string, paths: Paths): Promise<RunLog> {
  const refusal = keepRefusal(kind, process.env.ACR_G1E_KEEP)
  if (refusal) throw new Error(refusal)
  const fixture = JSON.parse(helper(['constants']))
  const f0 = JSON.parse(readFileSync(paths.f0, 'utf8'))
  const log = new RunLog(kind)
  mkdirSync(directory, { recursive: true })
  for (const attempt of attempts) {
    const name = attemptName(attempt.warmup ? `warmup-${attempt.mode}` : attempt.mode, attempt.count, attempt.repeat)
    if (freezeProblems(paths).length) log.record(name, 'invalid', 'freeze-mismatch')
    else {
      const check = toolchainCheck(paths)
      const freeze = binding(paths)
      if (!check.match) {
        const base = { schema: 'acr-g1e-trial/1', method: 'g1e', methodRevision: freeze.methodRevision, kind: attempt.kind, selectedRuntime: attempt.mode, count: attempt.count, repeat: attempt.repeat,
          measuring: attempt.kind === 'measured' && attempt.warmup !== true, warmup: attempt.warmup === true, slot: attempt.slot, firstArm: attempt.firstArm, freezeSha256: freeze.sha256, evidenceRevision: freeze.evidenceRevision,
          expectedSidecarSha256: attempt.mode === 'rust-shell' ? freeze.sidecarSha256 : null, freezeCheckedInputs: freeze.checkedInputs, fixtureRegistrySha256: fixture.fixtureRegistrySha256, logPolicySha256: fixture.logPolicySha256 }
        save(directory, attempt, toolchainMismatchReceipt(base, check), '')
        log.record(name, 'invalid', 'toolchain-mismatch')
      } else {
        const { warmupIndex: _index, ...planned } = attempt
        const spec: AttemptSpec = { ...planned, freeze, python, sidecarBin: sidecar, fixtureRegistrySha256: fixture.fixtureRegistrySha256, logPolicySha256: fixture.logPolicySha256, toolchain: check, expectedReadyVersion: f0.toolchain.sidecar.readyVersion }
        const result = await runAttempt(spec, directory)
        save(directory, attempt, result.receipt, result.resourcesJsonl)
        const { validity, stopReason } = classifyAttempt(result.receipt)
        log.record(name, validity, stopReason)
      }
    }
    process.stdout.write(`${blindLine(kind, log.attempts.at(-1)!)}\n`)
    if (log.stopped) break
  }
  finish(kind, log, attempts.length, directory)
  return log
}

async function runLaunch(kind: RunKind, attempts: LaunchPlanned[], directory: string, paths: Paths): Promise<RunLog> {
  mkdirSync(join(directory, 'launch-trials'), { recursive: true })
  const freeze = binding(paths)
  const fileOf = (label: string): string => join(directory, 'launch-trials', `${label}.json`)
  // Review-1 finding 2: each receipt is validated against the section 4 gate (identity, integrity, stamps) by the Python recomputation BEFORE it is recorded; the first invalid attempt stops the run.
  const log = await runLaunchChain(kind, attempts, {
    freezeProblems: () => freezeProblems(paths), toolchain: () => toolchainCheck(paths),
    run: (attempt, check) => runLaunchAttempt({ kind: kind === 'matrix' ? 'measured' : 'calibration', mode: attempt.mode, vendor: attempt.vendor, cell: attempt.cell, repeat: attempt.repeat, slot: attempt.slot ?? null,
      firstArm: attempt.firstArm ?? null, warmup: attempt.warmup, freezeSha256: freeze.sha256, python, sidecarBin: sidecar, toolchain: check }),
    save: (label, receipt) => writeFileSync(fileOf(label), `${redactDiagnostic(JSON.stringify(receipt, null, 1))}\n`),
    problems: (attempt, receipt) => (JSON.parse(helper(['launch-check', fileOf(launchLabel(attempt)), attempt.mode, attempt.vendor, String(attempt.cell), String(attempt.repeat), freeze.sha256, freeze.sidecarSha256, attempt.warmup ? '1' : '0'])) as { problems: string[] }).problems,
    emit: (line) => process.stdout.write(`${line}\n`),
  })
  writeFileSync(join(directory, 'launch-trials', 'run.json'), `${JSON.stringify(log.toRecord(attempts.length), null, 1)}\n`)
  return log
}

const loadF0 = (): { f0: string; freeze: any } => {
  const f0 = resolve(process.env.ACR_G3_FREEZE!)
  expect(existsSync(f0), 'ACR_G3_FREEZE (F0) is required').toBe(true)
  expect(sidecar, 'ACR_G3_SIDECAR is required (the toolchain check covers the sidecar before every attempt)').not.toBeNull()
  return { f0, freeze: JSON.parse(readFileSync(f0, 'utf8')) }
}
const loadF1 = (): string => {
  const f1 = resolve(process.env.ACR_G3_F1!)
  expect(existsSync(f1), 'ACR_G3_F1 is required').toBe(true)
  return f1
}

// The pre-F0 plumbing smoke (not evidence): `g3Smoke.runSmoke` (smoke mode of the launch runner: no stamp, no sampler, no `runAttempt`); `g3/smoke.py` checks the entries it writes.
it.runIf(selected === 'smoke')('G3 pre-F0 plumbing smoke: 4 launch attempts and 2 shell-pane attempts, pass/fail and arm identity only', async () => {
  expect(sidecar, 'ACR_G3_SIDECAR is required').not.toBeNull()
  const entries = await runSmoke(out, python, sidecar!)
  process.stdout.write(`smoke: ${entries.length} attempts\n`)
}, 1_800_000)

// The packaged cold-start row (U7, descriptive only). `packaged-smoke` (pre-F0): one attempt, pass/fail only, no stamp. `packaged` (after the launch chains): one discarded warm-up + 10 scored cold starts of the
// frozen backend named by ACR_G3_PACKAGED_BACKEND; the first invalid attempt, a freeze difference or an artifact mismatch ends the run (section 5.3) and fails the step (g3PackagedRun.ts).
const packagedBackend = (): string => {
  const bin = resolve(process.env.ACR_G3_PACKAGED_BACKEND ?? '')
  expect(existsSync(bin), 'ACR_G3_PACKAGED_BACKEND (the frozen backend of the --dir layout) is required').toBe(true)
  return bin
}
// The packaged app is rehashed (backend and runtime bytes against the packaged-build record and the sidecar, head, backend file time) before the smoke and before EVERY attempt.
const packagedProblems = (): string[] => {
  const record = resolve(process.env.ACR_G3_PACKAGED_RECORD ?? '')
  expect(existsSync(record), 'ACR_G3_PACKAGED_RECORD (packaged-build.json) is required').toBe(true)
  expect(sidecar, 'ACR_G3_SIDECAR is required').not.toBeNull()
  try { return (JSON.parse(helper(['packaged-verify', root, sidecar!, record])) as { problems: string[] }).problems } catch (error) { return [`packaged-verify failed: ${String(error).slice(0, 100)}`] }
}
it.runIf(selected === 'packaged-smoke')('G3 packaged cold-start smoke: one attempt, pass/fail only', async () => {
  mkdirSync(out, { recursive: true })
  const problems = packagedProblems()
  const receipt = problems.length ? { passed: false } : await runPackagedAttempt({ backendBin: packagedBackend(), python, repeat: 0, warmup: true, smoke: true })
  writeFileSync(join(out, 'packaged-smoke.json'), `${JSON.stringify({ notEvidence: true, passed: receipt.passed === true, artifactProblems: problems }, null, 1)}\n`)
  process.stdout.write(`packaged smoke: ${receipt.passed === true ? 'clean' : 'invalid'}\n`)
  expect(receipt.passed).toBe(true)
}, 600_000)

it.runIf(selected === 'packaged')('G3 packaged cold start (descriptive): one warm-up and 10 scored attempts, the first invalid one ends the run', async () => {
  const { f0 } = loadF0()
  const bin = packagedBackend()
  const directory = join(out, 'packaged-trials')
  mkdirSync(directory, { recursive: true })
  const planned = [...Array.from({ length: PACKAGED_WARMUPS }, (_, repeat) => ({ repeat, warmup: true })), ...Array.from({ length: PACKAGED_SCORED }, (_, repeat) => ({ repeat, warmup: false }))]
  const attempts = rehearsal ? rehearsalPackaged(planned) : planned
  const record = await runPackagedChain(attempts, {
    freezeProblems: () => freezeProblems({ f0, f1: null }), artifactProblems: packagedProblems,
    run: (attempt) => runPackagedAttempt({ backendBin: bin, python, repeat: attempt.repeat, warmup: attempt.warmup }),
    save: (name, receipt) => writeFileSync(join(directory, `${name}.json`), `${JSON.stringify(receipt, null, 1)}\n`),
    emit: (line) => process.stdout.write(`${line}\n`),
  })
  writeFileSync(join(directory, 'run.json'), `${JSON.stringify(record, null, 1)}\n`)
  expect(record.complete, 'the packaged run ended early: the step fails and the run record carries it').toBe(true)
}, 1_800_000)

// G3-r2 real-artifact check (not evidence): one discarded Python-arm and one discarded Rust-arm shell attempt (cell 1) through the unchanged per-attempt checks (freeze, toolchain, ready-line version,
// runtime proof, identity, cleanup, log finals) against a scratch F0 built by the G3 builder. The receipts are read only through `helper attempt-report`, which prints pass/fail fields and no value.
it.runIf(selected === 'check')('G3-r2 real-artifact check: one Python-arm and one Rust-arm attempt through the per-attempt checks', async () => {
  const { f0 } = loadF0()
  const attempts: Planned[] = (['python', 'rust-shell'] as RuntimeMode[]).map((mode) => ({ kind: 'calibration', mode, count: 1, repeat: 0, slot: null, firstArm: null, warmup: true, warmupIndex: 0 }))
  const log = await runShell('calibration', attempts, join(out, 'shell'), { f0, f1: null })
  expect(log.toRecord(attempts.length).complete).toBe(true)
}, 1_800_000)

it.runIf(selected === 'calibrate')('G3 Python-only shell calibration: one discarded warm-up, then 20 attempts in two seeded chains', async () => {
  const { f0, freeze } = loadF0()
  const order = calibrationOrder(freeze.scheduleSeed)
  const attempts: Planned[] = (rehearsal ? rehearsalShellCalibration(order) : order).map((entry) => entry.warmup
    ? { kind: 'calibration', mode: 'python', count: entry.cell, repeat: 0, slot: null, firstArm: null, warmup: true, warmupIndex: 0 }
    : { kind: 'calibration', mode: 'python', count: entry.cell, repeat: entry.k!, slot: entry.slot, firstArm: null })
  expect(attempts).toHaveLength(rehearsal ? 3 : 21)
  const log = await runShell('calibration', attempts, join(out, 'calibration'), { f0, f1: null })
  expect(log.toRecord(attempts.length).complete).toBe(true)
}, 3_600_000)

it.runIf(selected === 'launch-calibrate')('G3 Python-only agent-launch calibration', async () => {
  const { f0, freeze } = loadF0()
  const order = (JSON.parse(helper(['launch-calibration-plan', freeze.scheduleSeed])) as Array<{ vendor: AgentLaunchAttempt['vendor']; cell: number; repeat: number; slot: number | null; warmup: boolean }>)
  const attempts: LaunchPlanned[] = (rehearsal ? rehearsalLaunchCalibration(order) : order).map((a) => ({ vendor: a.vendor, cell: a.cell, repeat: a.repeat, slot: a.slot, mode: 'python' as RuntimeMode, firstArm: 'python' as RuntimeMode, warmup: a.warmup }))
  expect(attempts).toHaveLength(rehearsal ? 8 : 44)
  const log = await runLaunch('calibration', attempts, out, { f0, f1: null })
  expect(log.toRecord(attempts.length).complete).toBe(true)
}, 3_600_000)

it.runIf(selected === 'matrix')('G3 frozen matrix: two discarded warm-ups, then the 40 attempts of the frozen schedule', async () => {
  const { f0, freeze } = loadF0()
  const f1 = loadF1()
  const warmups: Planned[] = matrixWarmups().map((warm) => ({ kind: 'measured', mode: warm.mode, count: warm.cell, repeat: 0, slot: null, firstArm: null, warmup: true, warmupIndex: warm.index }))
  const full = attemptsFromSchedule(makeSchedule(freeze.scheduleSeed))
  const scheduled: Planned[] = (rehearsal ? rehearsalMatrix(full) : full).map((entry) => ({ kind: 'measured', mode: entry.mode, count: entry.cell, repeat: entry.repeat, slot: entry.slot, firstArm: entry.firstArm }))
  expect(scheduled).toHaveLength(rehearsal ? 4 : 40)
  const log = await runShell('matrix', [...warmups, ...scheduled], join(out, 'matrix'), { f0, f1 })
  expect(log.toRecord(warmups.length + scheduled.length).complete).toBe(true)
}, 7_200_000)

it.runIf(selected === 'launch')('G3 agent-launch chains: 8 warm-ups and 80 scored attempts', async () => {
  const { f0, freeze } = loadF0()
  const f1 = loadF1()
  const full: LaunchPlanned[] = agentLaunchPlan(freeze.scheduleSeed)
  const attempts = rehearsal ? rehearsalLaunchMatrix(full) : full
  expect(attempts.filter((a) => !a.warmup)).toHaveLength(rehearsal ? 8 : 80)
  const log = await runLaunch('matrix', attempts, out, { f0, f1 })
  expect(log.toRecord(attempts.length).complete).toBe(true)
}, 7_200_000)
