import { expect, it } from 'vitest'
import { hasExactLiveTotals, matchesSessionDetection, runtimeStudyEnabled } from './runtimeBaselineAssertions'

it('leaves the optional study inactive in ordinary collection', () => {
  expect(runtimeStudyEnabled({})).toBe(false)
  expect(runtimeStudyEnabled({ ACR_STUDY: '0', ACR_MEASURE: '0' })).toBe(false)
})

it('runs explicitly requested studies without requiring measurement mode', () => {
  expect(runtimeStudyEnabled({ ACR_STUDY: '1' })).toBe(true)
  expect(runtimeStudyEnabled({ ACR_MEASURE: '1' })).toBe(true)
})

const fixture = { pane: 'fixture-pane', vendor: 'opencode', workspace: '/fixture workspace', sessionId: 'ses_fixture-pane' }
const detected = { type: 'session.detected', payload: { pane_id: fixture.pane, vendor: fixture.vendor, workspace_path: fixture.workspace, session_id: fixture.sessionId } }

it('accepts the fixture session detection and that exact live session entry', () => {
  expect(matchesSessionDetection(detected, fixture)).toBe(true)
  expect(hasExactLiveTotals({ [fixture.sessionId]: { input: 12, output: 3 } }, fixture.sessionId)).toBe(true)
})

it('rejects a different session key with identical 12/3 totals', () => {
  expect(hasExactLiveTotals({ 'ses_someone-else': { input: 12, output: 3 } }, fixture.sessionId)).toBe(false)
  expect(hasExactLiveTotals({ [fixture.sessionId]: { input: 0, output: 0 }, 'ses_someone-else': { input: 12, output: 3 } }, fixture.sessionId)).toBe(false)
})

it('rejects a detected identity mismatch even for the right pane', () => {
  expect(matchesSessionDetection({ ...detected, payload: { ...detected.payload, session_id: 'ses_someone-else' } }, fixture)).toBe(false)
})

it('rejects missing native/detected identity and missing or mismatched totals', () => {
  expect(matchesSessionDetection({ ...detected, payload: { pane_id: fixture.pane } }, fixture)).toBe(false)
  expect(matchesSessionDetection(detected, { ...fixture, sessionId: '' })).toBe(false)
  expect(hasExactLiveTotals({ [fixture.sessionId]: { input: 12, output: 3 } }, undefined)).toBe(false)
  expect(hasExactLiveTotals({}, fixture.sessionId)).toBe(false)
  expect(hasExactLiveTotals({ [fixture.sessionId]: { input: 12, output: 4 } }, fixture.sessionId)).toBe(false)
})

it('rejects another pane, vendor, workspace or event type', () => {
  for (const payload of [
    { ...detected.payload, pane_id: 'other-pane' },
    { ...detected.payload, vendor: 'claude' },
    { ...detected.payload, workspace_path: '/another workspace' },
  ]) expect(matchesSessionDetection({ ...detected, payload }, fixture)).toBe(false)
  expect(matchesSessionDetection({ ...detected, type: 'agent.activity' }, fixture)).toBe(false)
})
