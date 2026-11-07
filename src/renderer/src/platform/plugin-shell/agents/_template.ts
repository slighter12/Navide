/**
 * CONTRIBUTOR TEMPLATE — copy to `<your_key>.ts` and fill in, then register
 * the SPEC in `index.ts` (one line, display order). Files starting with `_`
 * are never registered. Full guide: docs/adding-a-cli-vendor.md.
 *
 * Start from the declaration: `shared/agent-cli/<key>.json` states the command, the flags and
 * the resume spelling once, and `scripts/generate-agent-cli-facts.py` writes them into
 * `src/shared/vendorFacts.ts`. This spec reads them (`FACT`, `...factOptions(FACT)`) and adds only what a
 * declaration cannot say.
 */

import type { AgentSpec } from './types'
import { VENDOR_FACTS } from '@shared/vendorFacts'
import { factOptions } from '@shared/launchWords'

// The declaration of this vendor: add `shared/agent-cli/<key>.json`, run the generator, then
// write `VENDOR_FACTS.<key>` here. `goose` stands in so this file compiles; it is not a vendor to copy from.
const FACT = VENDOR_FACTS.goose

// `as const satisfies` — NOT `: AgentSpec`. The annotation would widen
// agentKey to `string`, and index.ts derives the AgentKey union from these
// literals: one annotated spec collapses the union for the whole app.
export const SPEC = {
  agentKey: '_template', // must match the filename
  label: 'Human Name',
  defaultCommand: FACT.command,
  // `...factOptions(FACT)` gives this spec every option the declaration spells, and none other:
  // modelArgs, effortArgs, knownEfforts, skipPermissionFlag and resumeArgs. Omit a field in the JSON
  // (set it to null) and a spawn asking for it is REFUSED, which is the point: some CLIs accept an
  // unknown flag and ignore it, and a dropped --model looks exactly like a working one. Only what the
  // INTERACTIVE command accepts — Navide never spawns `exec`/`run`, and several CLIs expose these on
  // the subcommand only. Verify by running the flag with a deliberately invalid value; `--flag --version`
  // proves nothing.
  ...factOptions(FACT),
  // paneArg: (ctx) => '...',           // per-pane argument, if state must be split
  // ── Session resume (see types.ts for full field docs) ──
  // The declaration's `resume_args` is the resume spelling. A hook only for a CLI the facts cannot spell:
  // resumeCommandPattern: /^mycli\s+--restore\b/,  // an id-less resume (aider)
  // resumeWithoutId: (file) => '...',  // id-less vendors only (see aider)
  // supportsRebuild: true,
  // supportsRestorePin: true,
  // needsSessionMarker: true,          // true for every CLI that can't pin an id at launch
  // ── Turn signals ──
  // turnEndInferredFromSilence: true,  // ONLY if the log has no turn-end record
  // verifiedTurnText: true,            // ONLY after validating against real sessions
  // ── Terminal input protocol ──
  // bracketedPaste: true,
  // shiftEnterSequence: '\x1b[13;2u',  // only if bracketed-LF default won't do
  // ── Recovery ──
  // loginExpired: { pattern: /.../i, loginCommand: '/login' },
  hint: 'generalist'
} as const satisfies AgentSpec
