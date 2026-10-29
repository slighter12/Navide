// The loop of a G3 agent-launch run (review-1 finding 2). Every attempt's receipt is validated against the section 4 identity and integrity gate BEFORE it is recorded, so the first invalid
// attempt (missing `owned`, wrong argv_source, wrong or present ready digest, restart, survivor, missing stamp) is recorded `invalid` at once and no later attempt runs. Injected dependencies
// keep the loop testable without launching anything.
import { classifyAttempt, RunLog, blindLine, type RunKind } from './g1eRun'
import type { AgentLaunchAttempt } from './g3AgentLaunch'

export type LaunchPlanned = AgentLaunchAttempt & { slot?: number | null }

export const launchLabel = (attempt: LaunchPlanned): string =>
  attempt.warmup ? `warmup-${attempt.mode}-${attempt.vendor}-${attempt.cell}` : `trial-${attempt.mode}-${attempt.vendor}-${attempt.cell}-${attempt.slot ?? attempt.repeat}`

export type ChainDeps = {
  freezeProblems: () => string[]
  toolchain: () => { match: boolean }
  run: (attempt: LaunchPlanned, toolchain: { match: boolean }) => Promise<Record<string, unknown>>
  /** The Python-side validity of one receipt (`g3/launch.py receipt_problems`): empty = valid. */
  problems: (attempt: LaunchPlanned, receipt: Record<string, unknown>) => string[]
  save: (label: string, receipt: Record<string, unknown>) => void
  emit: (line: string) => void
}

export async function runLaunchChain(kind: RunKind, attempts: LaunchPlanned[], deps: ChainDeps): Promise<RunLog> {
  const log = new RunLog(kind)
  for (const attempt of attempts) {
    const label = launchLabel(attempt)
    const name = `launch:${label}`
    if (deps.freezeProblems().length) log.record(name, 'invalid', 'freeze-mismatch')
    else {
      const check = deps.toolchain()
      if (!check.match) log.record(name, 'invalid', 'toolchain-mismatch')
      else {
        const receipt = await deps.run(attempt, check)
        deps.save(label, receipt)
        if (deps.problems(attempt, receipt).length) log.record(name, 'invalid', 'attempt-invalid')
        else {
          const { validity, stopReason } = classifyAttempt(receipt)
          log.record(name, validity, stopReason)
        }
      }
    }
    deps.emit(blindLine(kind, log.attempts.at(-1)!))
    if (log.stopped) break
  }
  return log
}
