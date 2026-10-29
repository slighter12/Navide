// The loop of the packaged cold-start run (U7; review-3 findings 3 and 4). The row is descriptive, but it is part of the ONE registered run (section 5.3): the first invalid warm-up or scored attempt
// ends it, the record says `complete: false` and the caller fails the step, so the failure reaches the run record and the early outcome. Before EVERY attempt the freeze is re-checked and the packaged
// artifact is rehashed (backend and runtime bytes, head, backend file time); a mismatch invalidates the run before that attempt starts. Dependencies are injected: the loop launches nothing by itself.
export type PackagedPlanned = { repeat: number; warmup: boolean }
export type PackagedStop = 'freeze-mismatch' | 'artifact-mismatch' | 'attempt-invalid'
export type PackagedRunRecord = { schema: 'acr-g3-packaged-run/1'; attempts: Array<{ name: string; validity: 'clean' | 'invalid'; stopReason: PackagedStop | null }>; stopReason: PackagedStop | null; complete: boolean }

export type PackagedDeps = {
  freezeProblems: () => string[]
  /** Rehash of the packaged app now (`helper packaged-verify`): empty = the bytes about to be measured are the bound ones. */
  artifactProblems: () => string[]
  run: (attempt: PackagedPlanned) => Promise<Record<string, unknown>>
  save: (name: string, receipt: Record<string, unknown>) => void
  emit: (line: string) => void
}

export const packagedName = (attempt: PackagedPlanned): string => (attempt.warmup ? `warmup-${attempt.repeat}` : `trial-${attempt.repeat}`)

export async function runPackagedChain(attempts: PackagedPlanned[], deps: PackagedDeps): Promise<PackagedRunRecord> {
  const record: PackagedRunRecord = { schema: 'acr-g3-packaged-run/1', attempts: [], stopReason: null, complete: false }
  const note = (name: string, validity: 'clean' | 'invalid', stopReason: PackagedStop | null): void => {
    record.attempts.push({ name, validity, stopReason })
    deps.emit(`packaged ${name}: ${validity}${stopReason ? ` stop=${stopReason}` : ''}`)
  }
  for (const attempt of attempts) {
    const name = packagedName(attempt)
    if (deps.freezeProblems().length) { record.stopReason = 'freeze-mismatch'; note(name, 'invalid', 'freeze-mismatch'); break }
    if (deps.artifactProblems().length) { record.stopReason = 'artifact-mismatch'; note(name, 'invalid', 'artifact-mismatch'); break }
    const receipt = await deps.run(attempt)
    deps.save(name, receipt)
    if (receipt.passed !== true) { record.stopReason = 'attempt-invalid'; note(name, 'invalid', 'attempt-invalid'); break }
    note(name, 'clean', null)
  }
  // After the LAST attempt the packaged bytes are rehashed once more (section 1.1: identity at start and at end); a change during or after the last attempt invalidates the whole run.
  if (record.stopReason === null && record.attempts.length === attempts.length && deps.artifactProblems().length) {
    record.stopReason = 'artifact-mismatch'
    note('final-check', 'invalid', 'artifact-mismatch')
  }
  record.complete = record.stopReason === null && record.attempts.length === attempts.length
  return record
}
