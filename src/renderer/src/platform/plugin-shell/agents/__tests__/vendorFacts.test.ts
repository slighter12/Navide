/**
 * The vendor specs read their launch facts from the generated declaration data
 * (`../_vendorFacts.ts`, generated from `shared/agent-cli/<key>.json`). These
 * tests pin that every real CLI spec has a declaration and produces exactly the
 * arguments that declaration states, so a hand-written second copy cannot drift.
 */

import { describe, expect, it } from 'vitest'

import { CLI_AGENT_SPECS } from '../index'
import { VENDOR_FACTS } from '../_vendorFacts'

describe('vendor declarations', () => {
  it('declares every real CLI spec and nothing else', () => {
    expect(Object.keys(VENDOR_FACTS).sort()).toEqual(CLI_AGENT_SPECS.map((s) => s.agentKey).sort())
  })

  for (const spec of CLI_AGENT_SPECS) {
    describe(spec.agentKey, () => {
      const facts = VENDOR_FACTS[spec.agentKey as keyof typeof VENDOR_FACTS]

      it('launches the declared command', () => {
        expect(spec.defaultCommand).toBe(facts.command)
      })

      it('builds model and effort arguments from the declared flags', () => {
        expect(spec.modelArgs?.('m-1')).toBe(facts.model_flag === null ? undefined : `${facts.model_flag} m-1`)
        expect(spec.effortArgs?.('e-1')).toBe(facts.effort_flag === null ? undefined : `${facts.effort_flag} e-1`)
        expect(spec.knownEfforts).toEqual(facts.known_efforts ?? undefined)
      })

      it('uses the declared permission flag and resume shape', () => {
        expect(spec.skipPermissionFlag).toBe(facts.permission_flag ?? undefined)
        expect(spec.resumeArgs?.('sess-1')).toBe(
          facts.resume_args === null ? undefined : facts.resume_args.replace('{id}', 'sess-1')
        )
      })

      it('pins the declared session id flag', () => {
        expect(spec.pinsSessionIdAtLaunch?.flag).toBe(facts.session_id_flag ?? undefined)
      })
    })
  }
})
