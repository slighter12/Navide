import { OWNERSHIP_KEYS, type ProofVerdict, type RuntimeMode } from './runtimeMatched'

export type FinalEvidence = { evidenceValid: boolean; proof: ProofVerdict | null; error: string | null }

/** The complete, arm-correct ownership envelope: Rust has all ten lists and a boolean flag equal to "any list non-empty"; Python has null. */
function envelopeOk(verdict: ProofVerdict, mode: RuntimeMode): boolean {
  if (verdict.facts?.mode !== mode || typeof verdict.ownershipFailure !== 'boolean') return false
  if (mode === 'python') return verdict.ownership === null && verdict.ownershipFailure === false
  const ownership = verdict.ownership as unknown as Record<string, unknown> | null
  if (!ownership || typeof ownership !== 'object') return false
  if (Object.keys(ownership).length !== OWNERSHIP_KEYS.length || !OWNERSHIP_KEYS.every((key) => Array.isArray(ownership[key]))) return false
  const anyFailure = OWNERSHIP_KEYS.some((key) => (ownership[key] as unknown[]).length > 0)
  return verdict.ownershipFailure === anyFailure && !(verdict.ok && anyFailure)
}

const consistent = (verdict: ProofVerdict | undefined | null, mode: RuntimeMode, cleanupPassed: boolean): verdict is ProofVerdict =>
  !!verdict && typeof verdict.ok === 'boolean' && Array.isArray(verdict.failures) && verdict.ok === (verdict.failures.length === 0)
  && envelopeOk(verdict, mode) && cleanupPassed

/**
 * Fails closed: evidence is valid only when the driver cleanup passed AND every proof step (receipt read/parse, executable hash,
 * checkRuntimeProof) completed and produced a consistent, successful verdict with the complete envelope for this arm. Any exception,
 * missing or inconsistent data is a single `evidenceValid=false`; the single assignment happens after all proof steps.
 */
export function finalizeEvidence(input: { mode: RuntimeMode; cleanupPassed: boolean; matched: boolean; proofStep: () => ProofVerdict }): FinalEvidence {
  if (!input.matched) return { evidenceValid: input.cleanupPassed, proof: null, error: null }
  let proof: ProofVerdict | null = null
  let error: string | null = null
  try {
    proof = input.proofStep()
  } catch (caught) {
    error = caught instanceof Error ? caught.message : String(caught)
    proof = null
  }
  const evidenceValid = error === null && consistent(proof, input.mode, input.cleanupPassed) && proof.ok
  if (error === null && !proof) error = 'proof step produced no verdict'
  return { evidenceValid, proof, error }
}
