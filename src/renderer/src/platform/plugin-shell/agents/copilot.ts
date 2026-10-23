/** Copilot CLI — per-vendor agent spec (see types.ts; assembled by index.ts). */

import type { AgentSpec } from './types'
import { VENDOR_FACTS, fillId } from './_vendorFacts'

const FACT = VENDOR_FACTS.copilot

export const SPEC = {
  agentKey: 'copilot',
  label: 'GitHub Copilot CLI',
  defaultCommand: FACT.command,
  // `--effort` is an accepted alias; the long form is used here. GitHub's
  // published CLI reference documents only the settings.json `effortLevel`
  // and omits this flag entirely — the values come from the binary's own
  // --help, which exits 1 listing them. Individual models may still refuse
  // one of them.
  modelArgs: (m) => `${FACT.model_flag} ${m}`,
  effortArgs: (e) => `${FACT.effort_flag} ${e}`,
  knownEfforts: FACT.known_efforts,
  // --yolo ≡ --allow-all-tools --allow-all-paths --allow-all-urls
  skipPermissionFlag: FACT.permission_flag,
  // NOTE the `=` form. Verified against 1.0.78 `--help`: this flag is scoped
  // to an id that already exists ("optionally specify existing session ID,
  // task ID, ID prefix, or name"), while the create-if-missing behaviour lives
  // on a DIFFERENT flag, --session-id ("Resume an existing session or task by
  // ID, or set the UUID for a new session"). An older note here credited
  // --resume= with creating on a miss; whether it still does cannot be settled
  // without a live session, so supportsRestorePin stays off — a stale pin has
  // no documented guarantee of self-healing on this flag.
  resumeArgs: (id) => fillId(FACT.resume_args, id),
  needsSessionMarker: true,
  // The `=` form, which the generic --resume shape would not match.
  resumeCommandPattern: /^copilot\s+--resume(?:=|\s+)\S+/,
  supportsRebuild: true,
  // Measured on a real PTY: the CLI itself emits `ESC[?1049h` during startup
  // (probe read 333 bytes of startup output) and keeps the conversation there.
  fullScreenTui: true,
  hint: 'generalist',
  // Quota failover: copilot.py reports one monthly premium-requests window.
  quotaSemantics: { hard: ['monthly'], required: ['monthly'] },
} as const satisfies AgentSpec
