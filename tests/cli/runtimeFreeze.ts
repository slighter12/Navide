import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// Run-time binding of the measured matrix to its frozen method (ticket 06). Test-only.
export type FreezeVerdict = { ok: boolean; failures: string[]; freezeSha256: string | null; checkedInputs: number }

const sha256 = (data: Buffer | string): string => createHash('sha256').update(data).digest('hex')
const git = (repo: string, args: string[]): Buffer => execFileSync('git', args, { cwd: repo, maxBuffer: 256 * 1024 * 1024, timeout: 30_000 })

/** Re-hashes the freeze file, every frozen input (runner, checker, summariser, binary...) and the git state it recorded. */
export function verifyFreeze(repo: string, freezePath: string, digestPath: string): FreezeVerdict {
  const failures: string[] = []
  if (!existsSync(freezePath) || !existsSync(digestPath)) return { ok: false, failures: ['freeze file or its digest file is missing'], freezeSha256: null, checkedInputs: 0 }
  const raw = readFileSync(freezePath)
  const freezeSha256 = sha256(raw)
  const recorded = readFileSync(digestPath, 'utf8').trim().split(/\s+/)[0]
  if (recorded !== freezeSha256) failures.push(`freeze file digest ${freezeSha256} differs from the recorded ${recorded}`)
  const freeze = JSON.parse(raw.toString('utf8')) as { inputs: Record<string, string>; head: string; trackedDiffSha256: string; statusPorcelain: string[]; sidecarSha256: string }
  const binaryName = 'native/navide-cli-runtime/target/release/navide-cli-runtime'
  const hex = (value: unknown): boolean => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
  if (!freeze.inputs || typeof freeze.inputs !== 'object' || Object.keys(freeze.inputs).length === 0) failures.push('freeze lists no frozen inputs')
  if (!hex(freeze.sidecarSha256)) failures.push('freeze sidecar hash is missing or malformed')
  if (!(freeze.inputs && binaryName in freeze.inputs)) failures.push(`freeze has no sidecar binary input entry (${binaryName})`)
  let checkedInputs = 0
  for (const [name, expected] of Object.entries(freeze.inputs ?? {})) {
    checkedInputs++
    if (!hex(expected)) { failures.push(`frozen input hash is malformed: ${name}`); continue }
    const path = join(repo, name)
    if (!existsSync(path)) { failures.push(`frozen input is missing: ${name}`); continue }
    if (sha256(readFileSync(path)) !== expected) failures.push(`frozen input changed: ${name}`)
  }
  if (freeze.inputs?.[binaryName] !== freeze.sidecarSha256) failures.push('freeze sidecar hash and its binary input entry disagree')
  const head = git(repo, ['rev-parse', 'HEAD']).toString('utf8').trim()
  if (head !== freeze.head) failures.push(`HEAD ${head} differs from the frozen ${freeze.head}`)
  if (sha256(git(repo, ['diff', 'HEAD'])) !== freeze.trackedDiffSha256) failures.push('tracked diff against HEAD differs from the frozen one')
  const status = git(repo, ['status', '--porcelain']).toString('utf8').split('\n').filter(Boolean)
  if (JSON.stringify(status) !== JSON.stringify(freeze.statusPorcelain)) failures.push('git status --porcelain differs from the frozen one')
  return { ok: failures.length === 0, failures, freezeSha256, checkedInputs }
}

/** Bounded, non-identifying code for a failed system inspection: never message text, argv or output. */
export function boundedErrorCode(error: unknown): string {
  const code = (error as { code?: unknown } | null)?.code
  if (typeof code === 'string' && /^[A-Z][A-Z0-9_]{1,23}$/.test(code)) return code
  const status = (error as { status?: unknown } | null)?.status
  return typeof status === 'number' && Number.isInteger(status) && status >= 0 && status < 256 ? `EXIT_${status}` : 'ERROR'
}
