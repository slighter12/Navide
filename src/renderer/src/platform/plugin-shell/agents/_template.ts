/**
 * CONTRIBUTOR TEMPLATE — copy to `<your_key>.ts` and fill in, then register
 * the SPEC in `index.ts` (one line, display order). Files starting with `_`
 * are never registered. Full guide: docs/adding-a-cli-vendor.md.
 */

import type { AgentSpec } from './types'

// In a real spec file: `import { declared } from './_declared'`.

// `as const satisfies` — NOT `: AgentSpec`. The annotation would widen
// agentKey to `string`, and index.ts derives the AgentKey union from these
// literals: one annotated spec collapses the union for the whole app.
export const SPEC = {
  agentKey: '_template', // must match the filename
  // ── Launch and resume: declared once, never retyped here ──
  // The label, the command, the unattended flag, the model and effort flags
  // (with the effort vocabulary), the resume arguments and the vendor kind
  // come from the CLI's declaration in
  // native/navide-cli-runtime/src/vendors/<key>.rs, through
  // cli_declarations.json:
  //   ...declared('mycli'),
  // Declare only what the INTERACTIVE command accepts — Navide never spawns
  // `exec`/`run`, and several CLIs expose a flag on the subcommand only. A
  // flag left undeclared makes a spawn asking for it REFUSED, which is the
  // point: some CLIs accept an unknown flag and ignore it, and a dropped
  // --model looks exactly like a working one. Verify by running the flag with
  // a deliberately invalid value; `--flag --version` proves nothing.
  // (This template alone spells the two required fields out, since it has no
  // declaration.)
  label: 'Human Name',
  defaultCommand: 'mycli',
  // paneArg: (ctx) => '...',           // per-pane argument, if state must be split
  // ── Session resume (see types.ts for full field docs) ──
  // resumeCommandPattern: /^mycli\s+--resume\s+\S+/, // only if non-generic shape
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
