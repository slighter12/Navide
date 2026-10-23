import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { expect } from 'vitest'
import { redactDiagnostic } from './baselineArtifacts'

// The disposable Python driver shared by the ticket-05 production-seam tests
// (moved verbatim from runtimeRecovery.test.ts so a second file can use it).

export async function until(predicate: () => boolean | Promise<boolean>, message: string, timeout = 15_000): Promise<void> {
  const deadline = Date.now() + timeout
  while (!(await predicate())) {
    if (Date.now() >= deadline) throw new Error(message)
    await delay(20)
  }
}

export type Receipt = Record<string, any>
export interface Driver {
  root: string
  wire: any
  ctl: <T = any>(command: string, argument?: string) => Promise<T>
  receipts: () => Receipt[]
  waitFile: (name: string, timeout?: number) => Promise<any>
  /** Ends the current driver phase (the backend-death scenario has two). */
  stop: () => void
}

export async function withDriver(scenario: string, run: (driver: Driver) => Promise<void>): Promise<void> {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-t05-')))
  const python = resolve(process.env.UV_PROJECT_ENVIRONMENT ?? 'backend/.venv', 'bin/python')
  const driver = spawn(python, ['-m', 'tests.cli_regression.support.runtime_recovery_driver', root, scenario], {
    cwd: resolve('backend'), stdio: ['pipe', 'pipe', 'pipe'],
  })
  let log = ''
  let code: number | null | undefined
  driver.stdout!.on('data', (data) => { log += data })
  driver.stderr!.on('data', (data) => { log += data })
  driver.on('error', (error) => { log += String(error); code = -1 })
  // The driver exits on its own after its last phase, so a stop sent as cleanup can race its exit:
  // a write to a pipe whose reader is already gone is reported as an 'error' event on stdin
  // (EPIPE / ERR_STREAM_DESTROYED). That single documented closed-pipe case means "nothing left to
  // tell it"; every other stdin error is recorded and fails the case.
  let stdinFault: string | undefined
  driver.stdin!.on('error', (error: NodeJS.ErrnoException) => {
    if (error.code === 'EPIPE' || error.code === 'ERR_STREAM_DESTROYED') return
    stdinFault = `${error.code ?? ''} ${error.message}`
  })
  const send = (line: string): void => {
    if (code !== undefined || driver.stdin!.destroyed || !driver.stdin!.writable) return // never write after exit
    driver.stdin!.write(line)
  }
  driver.on('exit', (value) => { code = value })
  let sequence = 0
  const waitFile = async (name: string, timeout = 60_000): Promise<any> => {
    await until(() => {
      if (existsSync(join(root, name))) return true // a driver that wrote it and exited is fine
      if (code !== undefined) throw new Error(redactDiagnostic(log) || `driver exited (${code}) before writing ${name}`)
      return false
    }, `driver file ${name}`, timeout)
    return JSON.parse(readFileSync(join(root, name), 'utf8'))
  }
  const ctl = async <T = any>(command: string, argument = ''): Promise<T> => {
    const seq = String(++sequence)
    send(`${seq} ${command} ${argument}\n`)
    const reply = await waitFile(`reply-${seq}.json`, 60_000)
    if (reply && typeof reply === 'object' && 'error' in reply) throw new Error(`${command}: ${reply.error}`)
    return reply as T
  }
  const receipts = (): Receipt[] => existsSync(join(root, 'runtime-receipts.jsonl'))
    ? readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map((line) => JSON.parse(line))
    : []
  let bodyFailed = false
  try {
    const wire = await waitFile('wire.json', 60_000)
    await run({ root, wire, ctl, receipts, waitFile, stop: () => { send(`${++sequence} stop\n`) } })
  } catch (error) {
    bodyFailed = true // the cleanup assertions below must never mask the real failure
    throw error
  } finally {
    send('0 stop\n')
    send('0 stop\n')
    if (code === undefined && !driver.stdin!.destroyed) driver.stdin!.end()
    await until(() => code !== undefined, 'driver cleanup', 90_000)
    const reports = resolve('test-results/ci/runtime-recovery', `${scenario}-${basename(root)}`)
    mkdirSync(reports, { recursive: true })
    writeFileSync(join(reports, 'driver.log'), redactDiagnostic(log))
    for (const name of readdirSync(root)) {
      // No authenticated endpoint URL crosses the artifact boundary.
      if (/^(.*backend\.log|.*scenario\.json|runtime-receipts\.jsonl|.*process-identities\.jsonl|backend-death\.json|observation-during-loss\.json|observation-early\.json|orphan-rescue\.json|durable\.json|closed\.json)$/.test(name)) {
        writeFileSync(join(reports, name), redactDiagnostic(readFileSync(join(root, name), 'utf8')))
      }
    }
    // The second backend of the two-runtime scenarios keeps its artifacts in `other/`: without them a failure on that backend
    // (a survivor that could not be killed) could not be diagnosed from receipts, only retried.
    if (existsSync(join(root, 'other'))) {
      for (const name of readdirSync(join(root, 'other'))) {
        if (/^(.*backend\.log|runtime-receipts\.jsonl|.*process-identities\.jsonl)$/.test(name)) {
          writeFileSync(join(reports, `other-${name}`), redactDiagnostic(readFileSync(join(root, 'other', name), 'utf8')))
        }
      }
    }
    if (!bodyFailed) expect(stdinFault, 'driver stdin fault').toBeUndefined()
    if (!bodyFailed) expect(code, redactDiagnostic(log)).toBe(0)
    if (!bodyFailed && existsSync(join(root, 'orphan-rescue.json'))) {
      // The driver's emergency kill of a leaked child is a failure, never a pass condition.
      expect(JSON.parse(readFileSync(join(root, 'orphan-rescue.json'), 'utf8')).rescued).toEqual([])
    }
    rmSync(root, { recursive: true, force: true })
  }
}
