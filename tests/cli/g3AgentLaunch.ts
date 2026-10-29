// G3 agent-launch rows (G3 pre-registration H4, U7). Pure planning and extraction; nothing here launches or measures.
// Row ids match backend/tests/cli_regression/support/g3/rows.py `agent_launch_specs`. The g1e schedule helpers are imported unchanged.
import type { RuntimeMode } from './runtimeMatched'
import { G1E_CELLS, G1E_PAIRS, shuffle } from './g1eSchedule'

export const AGENT_LAUNCH_METRIC = 'lat:agent_create_to_first_usable_ms'
/** vendor -> the observer of the Rust arm on macOS: claude has a Rust observer, aider has none. */
export const AGENT_LAUNCH_VENDORS = { claude: 'rust', aider: 'none' } as const
export type AgentLaunchVendor = keyof typeof AGENT_LAUNCH_VENDORS
export const PACKAGED_COLD_ROW = 'descriptive|lat:packaged_cold_to_first_usable_ms|cell1'

export type AgentLaunchAttempt = { vendor: AgentLaunchVendor; cell: number; repeat: number; mode: RuntimeMode; firstArm: RuntimeMode; warmup: boolean }

export const agentLaunchRowId = (vendor: AgentLaunchVendor, cell: number): string => `lifecycle|${AGENT_LAUNCH_METRIC}:${vendor}|cell${cell}`

export const agentLaunchRowIds = (): string[] =>
  (Object.keys(AGENT_LAUNCH_VENDORS) as AgentLaunchVendor[]).flatMap((vendor) => G1E_CELLS.map((cell) => agentLaunchRowId(vendor, cell)))

/** One chain (vendor, cell): a discarded warm-up per arm (Python then Rust), then 10 scored pairs, both arms back to back, 5 Python-first and 5 Rust-first (seeded). */
export function agentLaunchChain(seed: string, vendor: AgentLaunchVendor, cell: number): AgentLaunchAttempt[] {
  const first = shuffle<RuntimeMode>([...Array(5).fill('python'), ...Array(5).fill('rust-shell')], seed, `agent:${vendor}:first${cell}`)
  const warmups: AgentLaunchAttempt[] = (['python', 'rust-shell'] as RuntimeMode[]).map((mode) => ({ vendor, cell, repeat: 0, mode, firstArm: 'python', warmup: true }))
  const pairs = Array.from({ length: G1E_PAIRS }, (_, repeat) => {
    const second: RuntimeMode = first[repeat] === 'python' ? 'rust-shell' : 'python'
    return [first[repeat], second].map((mode): AgentLaunchAttempt => ({ vendor, cell, repeat, mode, firstArm: first[repeat], warmup: false }))
  }).flat()
  return [...warmups, ...pairs]
}

/** The four chains, run separately after the shell matrix; chain order is a seeded shuffle of (vendor, cell). 80 scored attempts. */
export function agentLaunchPlan(seed: string): AgentLaunchAttempt[] {
  const chains = (Object.keys(AGENT_LAUNCH_VENDORS) as AgentLaunchVendor[]).flatMap((vendor) => G1E_CELLS.map((cell) => ({ vendor, cell })))
  return shuffle(chains, seed, 'agent-chains').flatMap(({ vendor, cell }) => agentLaunchChain(seed, vendor, cell))
}

export type LaunchStamps = { agentCreateAtMs: unknown; firstUsableAtMs: unknown }

/** `agent_create_to_first_usable_ms`: null (the attempt gives no value) unless both stamps are finite and the order is not reversed. */
export function agentLaunchMs(stamps: LaunchStamps): number | null {
  const { agentCreateAtMs: start, firstUsableAtMs: end } = stamps
  if (typeof start !== 'number' || typeof end !== 'number' || !Number.isFinite(start) || !Number.isFinite(end) || end < start) return null
  return end - start
}
