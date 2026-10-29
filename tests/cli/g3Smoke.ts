// The pre-F0 plumbing smoke (NOT EVIDENCE; review-1 finding 1): 4 agent-launch attempts (2 arms x claude, aider) and 2 plain shell-pane attempts (1 per arm), all through `runLaunchAttempt` in smoke mode.
// This path takes no stamp, starts no resource sampler and never calls `g1eAttempt.runAttempt`; it keeps only pass/fail and the arm-identity facts. `g3/smoke.py` checks the file it writes.
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { launchSmokeEntry, runLaunchAttempt, type LaunchVendor } from './g3LaunchAttempt'
import type { RuntimeMode } from './runtimeMatched'

export const SMOKE_ATTEMPTS: Array<{ mode: RuntimeMode; vendor: LaunchVendor }> = (['python', 'rust-shell'] as RuntimeMode[]).flatMap((mode) =>
  (['claude', 'aider', 'shell'] as LaunchVendor[]).map((vendor) => ({ mode, vendor })))

export async function runSmoke(out: string, python: string, sidecar: string): Promise<Record<string, unknown>[]> {
  const entries: Record<string, unknown>[] = []
  for (const { mode, vendor } of SMOKE_ATTEMPTS) {
    const receipt = await runLaunchAttempt({ kind: 'calibration', mode, vendor, cell: 1, repeat: 0, slot: null, firstArm: null, warmup: true, freezeSha256: 'smoke', python, sidecarBin: sidecar, toolchain: { match: true }, smoke: true })
    entries.push(launchSmokeEntry({ mode, vendor }, receipt))
  }
  mkdirSync(out, { recursive: true })
  writeFileSync(join(out, 'smoke-entries.json'), `${JSON.stringify(entries, null, 1)}\n`)
  return entries
}
