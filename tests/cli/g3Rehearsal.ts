// G3-r2 rehearsal mode (not evidence). `ACR_G3_REHEARSAL=1` reduces the repetition counts of the g3Matrix.test.ts modes so that every step of the cost driver runs end to end on the real artifacts in minutes.
// It never changes a registered count: without the variable every function here is the identity. The variable is honoured only when the output directory is the rehearsal one, so a stray variable
// in the registered run fails loudly instead of silently reducing the matrix.
import { resolve, sep } from 'node:path'

export const REHEARSAL_DIR_SEGMENT = `${sep}g3r2${sep}rehearsal`

export const isRehearsal = (env: NodeJS.ProcessEnv = process.env): boolean => env.ACR_G3_REHEARSAL === '1'

/** Refusal text when the flag is set and `out` is not under .../g3r2/rehearsal (or the flag is set to anything but "1"), else null. */
export function rehearsalRefusal(out: string, env: NodeJS.ProcessEnv = process.env): string | null {
  if (env.ACR_G3_REHEARSAL === undefined) return null
  if (env.ACR_G3_REHEARSAL !== '1') return `ACR_G3_REHEARSAL must be exactly "1" or unset`
  const resolved = `${resolve(out)}${sep}`
  if (resolved.includes(`${REHEARSAL_DIR_SEGMENT}${sep}`) || resolved.includes(`${sep}g3r3${sep}rehearsal`)) return null      // G3-r3: any g3r3/rehearsal* directory
  return `ACR_G3_REHEARSAL is set but ${out} is not under g3r2/rehearsal or g3r3/rehearsal*: a rehearsal never writes elsewhere`
}

/** Shell calibration: the discarded warm-up and slot 0 of each chain (3 attempts instead of 21). */
export const rehearsalShellCalibration = <T extends { warmup: boolean; slot: number | null }>(order: T[]): T[] => order.filter((entry) => entry.warmup || entry.slot === 0)

/** Matrix: the first pair (repeat 0, both arms) of each cell, in schedule order (4 attempts instead of 40). */
export const rehearsalMatrix = <T extends { repeat: number }>(attempts: T[]): T[] => attempts.filter((attempt) => attempt.repeat === 0)

/** Launch calibration: each chain's warm-up and its first scored attempt (8 instead of 44). */
export const rehearsalLaunchCalibration = <T extends { warmup: boolean; slot?: number | null }>(order: T[]): T[] => order.filter((entry) => entry.warmup || entry.slot === 0)

/** Launch matrix: each chain's two warm-ups and its first pair (16 instead of 88). */
export const rehearsalLaunchMatrix = <T extends { warmup: boolean; repeat: number }>(plan: T[]): T[] => plan.filter((entry) => entry.warmup || entry.repeat === 0)

/** Packaged cold start: the warm-up and the first scored start (2 instead of 11). */
export const rehearsalPackaged = <T extends { warmup: boolean; repeat: number }>(attempts: T[]): T[] => attempts.filter((entry) => entry.warmup || entry.repeat === 0)
