// G1-E frozen randomised, counterbalanced schedule (E2 v10 C11). Byte-for-byte the reference in
// backend/tests/cli_regression/support/g1e/reference.py (`_shuffle`, `make_schedule`, `schedule_sha`); the golden file pins both.
import { createHash } from 'node:crypto'
import type { RuntimeMode } from './runtimeMatched'

export const G1E_CELLS = [1, 4] as const
export const G1E_PAIRS = 10

export type ScheduleEntry = { cell: number; firstArm: RuntimeMode; repeat: number; slot: number }
export type G1eAttempt = ScheduleEntry & { mode: RuntimeMode }

/** Fisher-Yates from the end with one SHA-256 draw per step: `sha256(seed|label|counter)[0:8]` as a big-endian integer, modulo i+1. */
export function shuffle<T>(items: T[], seed: string, label: string): T[] {
  const out = [...items]
  let counter = 0
  for (let i = out.length - 1; i > 0; i--, counter++) {
    const digest = createHash('sha256').update(`${seed}|${label}|${counter}`).digest('hex').slice(0, 16)
    const j = Number(BigInt(`0x${digest}`) % BigInt(i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

export function makeSchedule(seed: string): ScheduleEntry[] {
  const first = new Map<number, RuntimeMode[]>()
  for (const cell of G1E_CELLS) first.set(cell, shuffle<RuntimeMode>([...Array(5).fill('python'), ...Array(5).fill('rust-shell')], seed, `first${cell}`))
  const pairs = G1E_CELLS.flatMap((cell) => Array.from({ length: G1E_PAIRS }, (_, repeat) => ({ cell, repeat })))
  return shuffle(pairs, seed, 'order').map(({ cell, repeat }, slot) => ({ cell, firstArm: first.get(cell)![repeat], repeat, slot }))
}

/** SHA-256 of the canonical JSON: keys sorted (cell, firstArm, repeat, slot), no whitespace. */
export function scheduleSha256(schedule: ScheduleEntry[]): string {
  const canonical = schedule.map((entry) => ({ cell: entry.cell, firstArm: entry.firstArm, repeat: entry.repeat, slot: entry.slot }))
  return createHash('sha256').update(JSON.stringify(canonical)).digest('hex')
}

/** The 40 attempts in execution order: both arms of a pair run back to back, the scheduled first arm first. Every attempt is a fresh backend (C11). */
export function attemptsFromSchedule(schedule: ScheduleEntry[]): G1eAttempt[] {
  return schedule.flatMap((entry) => {
    const second: RuntimeMode = entry.firstArm === 'python' ? 'rust-shell' : 'python'
    return [{ ...entry, mode: entry.firstArm }, { ...entry, mode: second }]
  })
}

export type CalibrationEntry = { cell: number; chain: number; slot: number | null; k: number | null; warmup: boolean }

/** The cell order of the two calibration chains (E2 v14-r2 V19): the seeded shuffle with label `calibration`. */
export const calibrationChainCells = (seed: string): number[] => shuffle<number>([...G1E_CELLS], seed, 'calibration')

/**
 * Python-only calibration order (E2 v14-r2 V19), the reference `calibration.calibration_order`: ONE discarded warm-up in the first chain's cell, then two back-to-back chains of ten
 * scored attempts, one per cell, each carrying its frozen `slot` 0..9 (`k` is the slot: the receipt file name). Adjacency in the estimator is the slot.
 */
export function calibrationOrder(seed: string): CalibrationEntry[] {
  const cells = calibrationChainCells(seed)
  const scored = cells.flatMap((cell, chain) => Array.from({ length: G1E_PAIRS }, (_, slot) => ({ cell, chain, slot, k: slot, warmup: false })))
  return [{ cell: cells[0], chain: 0, slot: null, k: null, warmup: true }, ...scored]
}

export type MatrixWarmup = { mode: RuntimeMode; cell: number; index: number }

/** The two discarded matrix warm-ups (E2 v14-r2 V19): cell 4, Python then Rust shell, before the first scheduled pair. Not trials: they enter no row, pair, count or statistic. */
export const matrixWarmups = (): MatrixWarmup[] => [{ mode: 'python', cell: 4, index: 0 }, { mode: 'rust-shell', cell: 4, index: 0 }]
