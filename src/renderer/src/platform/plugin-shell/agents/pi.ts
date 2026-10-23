/** Pi — per-vendor agent spec (see types.ts; assembled by index.ts). */

import type { AgentSpec } from './types'
import { VENDOR_FACTS, fillId } from './_vendorFacts'

const FACT = VENDOR_FACTS.pi

export const SPEC = {
  agentKey: 'pi',
  label: 'Pi',
  defaultCommand: FACT.command,
  // Like claude, pi warns and continues on an unknown level rather than
  // failing, so the vocabulary is checked before launch. pi also accepts
  // `provider/id:<thinking>` in --model; the flag is used instead so the two
  // arguments stay independent.
  modelArgs: (m) => `${FACT.model_flag} ${m}`,
  effortArgs: (e) => `${FACT.effort_flag} ${e}`,
  knownEfforts: FACT.known_efforts,
  // no skipPermissionFlag: pi has no permission system at all
  // (bash/edit tools execute directly, nothing to bypass)
  // Creates a NEW session when the id doesn't exist, resumes when it does.
  resumeArgs: (id) => fillId(FACT.resume_args, id),
  needsSessionMarker: true,
  turnEndInferredFromSilence: true,
  resumeCommandPattern: /^pi\s+--session-id\s+\S+/,
  supportsRebuild: true,
  // Restore-pin self-heals: `pi --session-id <id>` is documented as "Use exact
  // project session ID, creating it if missing", so a pin that never gets
  // replaced still resumes/claims a real session instead of dead-ending.
  supportsRestorePin: true,
  hint: 'generalist',
  // Quota failover: pi.py reports OpenRouter credits with usage/limit; a
  // null limit is an uncapped pool and says nothing about headroom.
  quotaSemantics: { hard: ['credits'], required: ['credits'] },
} as const satisfies AgentSpec
