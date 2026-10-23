/** Kimi Code — per-vendor agent spec (see types.ts; assembled by index.ts). */

import type { AgentSpec } from './types'
import { VENDOR_FACTS, fillId } from './_vendorFacts'

const FACT = VENDOR_FACTS.kimi

export const SPEC = {
  agentKey: 'kimi',
  label: 'Kimi Code CLI (Moonshot AI)',
  defaultCommand: FACT.command,
  // Effort lives in ~/.kimi-code/config.toml ([thinking] effort) and an
  // invalid value is silently clamped there, so it is not exposed here.
  modelArgs: (m) => `${FACT.model_flag} ${m}`,
  skipPermissionFlag: FACT.permission_flag,
  // id is the `session_<uuid>` dir name.
  resumeArgs: (id) => fillId(FACT.resume_args, id),
  // Short flag is uppercase `-S` (verified with `kimi --help`); kimi does not
  // accept a lowercase `-s`, so it is not matched here.
  resumeCommandPattern: /^kimi\s+(?:--session|-S)\s+\S+/,
  // wire.jsonl carries no turn-end record: a turn is closed by the NEXT
  // turn.prompt, and the latest one only after an 8s quiet window
  // (_TURN_IDLE_MS in cli_vendors/kimi.py). The newest turn — the one the
  // messaging gate asks about — is therefore always silence-inferred.
  turnEndInferredFromSilence: true,
  needsSessionMarker: true,
  bracketedPaste: true,
  supportsRebuild: true,
  hint: 'generalist',
  // Quota failover: kimi.py reports weekly always and a "Rate limit (5h)"
  // session window only when the plan has one — enforced when present.
  quotaSemantics: { hard: ['weekly', 'session'], required: ['weekly'] },
} as const satisfies AgentSpec
