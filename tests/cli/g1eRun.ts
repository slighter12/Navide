// G1-E run bookkeeping (ticket E4, assignment 3; E2 v13 V7 items 2-4 and 6). Pure functions, no I/O.
//
// BLINDNESS (V7 item 6): until a matrix or reader-measure run is complete, everything a runner prints, and every file it persists OUTSIDE the sealed receipts, carries only the
// per-attempt validity (`clean` / `invalid`) and the stop reason. Never a measured value, never a paired statistic, never a failure text (a failure text can quote a number): the failure
// detail lives in the sealed trial receipt, which nobody opens before the run is complete. Calibration and dry-run attempts are not candidate attempts and may print their detail.
export type RunKind = 'calibration' | 'reader-calibration' | 'matrix' | 'reader-measure'
export type Validity = 'clean' | 'invalid'
export type StopReason = 'toolchain-mismatch' | 'attempt-invalid' | 'freeze-mismatch' | 'operator-stop'
export type RunAttempt = { name: string; validity: Validity; stopReason: StopReason | null }
export type RunRecord = { schema: 'acr-g1e-run/1'; kind: RunKind; attempts: RunAttempt[]; stopReason: StopReason | null; complete: boolean }

/** Runs whose attempts are candidate attempts (they run the Rust arm or the Rust reader): their console output is blind. */
export const BLIND_KINDS: readonly RunKind[] = ['matrix', 'reader-measure']
export const isBlind = (kind: RunKind): boolean => BLIND_KINDS.includes(kind)

export const attemptName = (mode: string, count: number, repeat: number): string => `${mode}:${count}:${repeat}`

/** The ONLY line a blind run may print or persist for an attempt. */
export function blindLine(kind: RunKind, attempt: RunAttempt): string {
  return `${kind} ${attempt.name}: ${attempt.validity}${attempt.stopReason ? ` stop=${attempt.stopReason}` : ''}`
}

export type AttemptOutcome = { passed?: unknown; evidenceValid?: unknown; stopReason?: unknown } // an absent field is not `true`: it reads as invalid

/** Clean iff the attempt passed with valid evidence and nothing stopped it; the stop reason of an invalid attempt is the one it recorded, else `attempt-invalid`. */
export function classifyAttempt(outcome: AttemptOutcome): { validity: Validity; stopReason: StopReason | null } {
  if (outcome.passed === true && outcome.evidenceValid === true && !outcome.stopReason) return { validity: 'clean', stopReason: null }
  return { validity: 'invalid', stopReason: outcome.stopReason === 'toolchain-mismatch' ? 'toolchain-mismatch' : 'attempt-invalid' }
}

export class RunLog {
  readonly attempts: RunAttempt[] = []
  private stop: StopReason | null = null
  constructor(readonly kind: RunKind) {}

  /** Records one attempt. The first invalid attempt stops the run; nothing is recorded after it. */
  record(name: string, validity: Validity, stopReason: StopReason | null): void {
    if (this.stop) throw new Error(`run already stopped (${this.stop}); no attempt may follow`)
    this.attempts.push({ name, validity, stopReason })
    if (validity === 'invalid') this.stop = stopReason ?? 'attempt-invalid'
  }

  get stopped(): StopReason | null { return this.stop }

  /** What the runner prints / persists outside the sealed receipts, per attempt. */
  lines(): string[] { return this.attempts.map((attempt) => blindLine(this.kind, attempt)) }

  /** `run.json`: validity and the stop reason only (acr-g1e-run/1); `complete` is true only when every expected attempt ran clean. */
  toRecord(expectedAttempts: number): RunRecord {
    const complete = this.stop === null && this.attempts.length === expectedAttempts
    return { schema: 'acr-g1e-run/1', kind: this.kind, attempts: this.attempts.map((a) => ({ ...a })), stopReason: this.stop ?? (complete ? null : 'operator-stop'), complete }
  }
}

/** `ACR_G1E_KEEP=1` keeps a failed attempt's root (raw measured data outside the sealed receipts). A candidate run must not: refuse it for the matrix and the reader-measure run. */
export function keepRefusal(kind: RunKind, keepEnv: string | undefined): string | null {
  return isBlind(kind) && keepEnv === '1' ? `ACR_G1E_KEEP is refused for the ${kind} run: a kept root is measured data outside the sealed receipts (V7 item 6)` : null
}

export type RootFacts = { kind: 'measured' | 'calibration' | 'dryrun'; keepRequested: boolean; passed: boolean; evidenceValid: boolean; exited: boolean }

/**
 * What happens to an attempt's isolated root when the attempt ends. A MEASURED attempt's root is always removed (without being read), also when the driver never exited; a failed
 * non-measured attempt's root is kept only on request; an unexited non-measured one is left alone (its driver may still own it).
 */
export function rootDisposition(facts: RootFacts): 'remove' | 'keep' | 'leave' {
  if (facts.kind === 'measured') return 'remove'
  if (facts.keepRequested && !(facts.passed && facts.evidenceValid)) return 'keep'
  return facts.exited ? 'remove' : 'leave'
}

export type ToolchainRecord = {
  recorded: Record<string, unknown>; observed: Record<string, unknown>; match: boolean; mismatches: unknown[]; uv: { realPath: string | null; version: string | null; sha256: string | null }
}
export type ReadyVersionCheck = { recorded: string; observed: unknown; match: boolean }

export function readyVersionCheck(recorded: string, observed: unknown): ReadyVersionCheck {
  return { recorded, observed, match: typeof observed === 'string' && observed === recorded }
}

/** The receipt's `toolchain` field (V7 item 2): the per-attempt check record plus the Rust-arm ready-line check. */
export function receiptToolchain(check: ToolchainRecord, ready: ReadyVersionCheck | null): ToolchainRecord & { readyVersion: ReadyVersionCheck | null } {
  return { ...check, readyVersion: ready }
}

/** The receipt of an attempt that never started because the toolchain check failed: evidence-invalid, stop reason `toolchain-mismatch`. It holds identities and the failed match only. */
export function toolchainMismatchReceipt(base: Record<string, unknown>, check: ToolchainRecord): Record<string, unknown> {
  return {
    ...base, passed: false, evidenceValid: false, harnessRescued: false, stopReason: 'toolchain-mismatch', failure: 'toolchain-mismatch',
    backend: { pid: 0, start: '' }, backendEnd: { pid: 0, start: '' }, toolchain: receiptToolchain(check, null),
  }
}
