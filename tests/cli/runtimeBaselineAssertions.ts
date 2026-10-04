// Test-only study entry/observation predicates; no runtime inference or fallback.
export function runtimeStudyEnabled(environment: Record<string, string | undefined>): boolean {
  return environment.ACR_STUDY === '1' || environment.ACR_MEASURE === '1'
}

export type FixtureSession = { pane: string; vendor: string; workspace: string; sessionId: string }
export type ObservationEvent = { type: string; payload: Record<string, unknown> }

export function matchesSessionDetection(event: ObservationEvent, fixture: FixtureSession): boolean {
  return Boolean(fixture.sessionId) && event.type === 'session.detected'
    && event.payload.pane_id === fixture.pane && event.payload.session_id === fixture.sessionId
    && event.payload.vendor === fixture.vendor && event.payload.workspace_path === fixture.workspace
}

export function hasExactLiveTotals(live: Record<string, { input: number; output: number }>, sessionId: string | undefined): boolean {
  if (!sessionId) return false
  const total = live[sessionId]
  return total?.input === 12 && total.output === 3
}
