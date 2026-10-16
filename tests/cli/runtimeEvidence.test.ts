import { describe, expect, it } from 'vitest'
import { finalizeEvidence } from './runtimeEvidence'
import { OWNERSHIP_KEYS, type ProofVerdict } from './runtimeMatched'

const emptyOwnership = () => Object.fromEntries(OWNERSHIP_KEYS.map((key) => [key, [] as string[]])) as unknown as NonNullable<ProofVerdict['ownership']>
const ok = (): ProofVerdict => ({ ok: true, failures: [], facts: { mode: 'rust-shell' }, ownership: emptyOwnership(), ownershipFailure: false })
const pythonOk = (): ProofVerdict => ({ ok: true, failures: [], facts: { mode: 'python' }, ownership: null, ownershipFailure: false })
const bad = (): ProofVerdict => ({ ok: false, failures: ['cleaned: no receipt for owned session s3'], facts: { mode: 'rust-shell' }, ownership: { ...emptyOwnership(), missingCleaned: ['s3'] }, ownershipFailure: true })

describe('finalizeEvidence fails closed', () => {
  it('is valid only when cleanup passed and the proof is a consistent success', () => {
    expect(finalizeEvidence({ mode: 'rust-shell', cleanupPassed: true, matched: true, proofStep: ok })).toMatchObject({ evidenceValid: true, error: null, proof: { ok: true } })
  })

  it.each([
    ['a receipt read/parse exception', () => { throw new SyntaxError('Unexpected end of JSON input') }],
    ['an unreadable executable (hash step)', () => { throw Object.assign(new Error('EACCES'), { code: 'EACCES' }) }],
    ['a thrown non-Error value', () => { throw 'boom' }],
  ])('invalidates the attempt on %s even though cleanup passed', (_name, proofStep) => {
    const result = finalizeEvidence({ mode: 'rust-shell', cleanupPassed: true, matched: true, proofStep })
    expect(result.evidenceValid).toBe(false)
    expect(result.proof).toBeNull()
    expect(result.error).toEqual(expect.any(String))
  })

  it('invalidates on a failed proof', () => {
    expect(finalizeEvidence({ mode: 'rust-shell', cleanupPassed: true, matched: true, proofStep: bad })).toMatchObject({ evidenceValid: false, proof: { ok: false } })
  })

  it('invalidates when the driver cleanup failed even if the proof passed', () => {
    expect(finalizeEvidence({ mode: 'rust-shell', cleanupPassed: false, matched: true, proofStep: ok }).evidenceValid).toBe(false)
  })

  it.each([
    ['ok=true with failures listed', () => ({ ...ok(), failures: ['x'] })],
    ['ok=false with no failures listed', () => ({ ...ok(), ok: false })],
    ['a missing verdict', () => undefined as unknown as ProofVerdict],
    ['a verdict without a facts mode', () => ({ ...ok(), facts: {} })],
  ])('invalidates an inconsistent verdict: %s', (_name, proofStep) => {
    expect(finalizeEvidence({ mode: 'rust-shell', cleanupPassed: true, matched: true, proofStep }).evidenceValid).toBe(false)
  })

  it('does not run the proof outside the matched study and keeps method-6 semantics', () => {
    let called = false
    const result = finalizeEvidence({ mode: 'rust-shell', cleanupPassed: true, matched: false, proofStep: () => { called = true; return ok() } })
    expect(called).toBe(false)
    expect(result).toEqual({ evidenceValid: true, proof: null, error: null })
    expect(finalizeEvidence({ mode: 'rust-shell', cleanupPassed: false, matched: false, proofStep: ok }).evidenceValid).toBe(false)
  })
})

describe('finalizeEvidence requires the complete ownership envelope for the right arm', () => {
  const run = (mode: 'python' | 'rust-shell', proofStep: () => ProofVerdict) => finalizeEvidence({ mode, cleanupPassed: true, matched: true, proofStep })

  it('accepts the complete Rust envelope and the Python null envelope', () => {
    expect(run('rust-shell', ok).evidenceValid).toBe(true)
    expect(run('python', pythonOk).evidenceValid).toBe(true)
  })

  it.each([
    ['null ownership on the Rust arm', () => ({ ...ok(), ownership: null })],
    ['a missing ownership key', () => { const ownership = emptyOwnership() as Record<string, unknown>; delete ownership.unclaimedRoots; return { ...ok(), ownership: ownership as never } }],
    ['an ownership list that is not an array', () => ({ ...ok(), ownership: { ...emptyOwnership(), missingCleaned: 'none' as never } })],
    ['a non-boolean ownershipFailure', () => ({ ...ok(), ownershipFailure: undefined as never })],
    ['ownershipFailure=true with ok=true', () => ({ ...ok(), ownershipFailure: true })],
    ['ownership lists non-empty with ownershipFailure=false', () => ({ ...ok(), ownership: { ...emptyOwnership(), missingCleaned: ['s3'] } })],
    ['facts naming another arm', () => ({ ...ok(), facts: { mode: 'python' } })],
  ])('invalidates %s', (_name, proofStep) => {
    expect(run('rust-shell', proofStep).evidenceValid).toBe(false)
  })

  it('invalidates non-null ownership on the Python arm and a Rust-named proof there', () => {
    expect(run('python', () => ({ ...pythonOk(), ownership: emptyOwnership() })).evidenceValid).toBe(false)
    expect(run('python', ok).evidenceValid).toBe(false)
  })
})
