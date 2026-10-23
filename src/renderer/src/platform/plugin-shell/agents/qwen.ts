/** Qwen Code — per-vendor agent spec (see types.ts; assembled by index.ts). */

import type { AgentSpec } from './types'
import { VENDOR_FACTS, fillId } from './_vendorFacts'

const FACT = VENDOR_FACTS.qwen

export const SPEC = {
  agentKey: 'qwen',
  label: 'Qwen Code (Alibaba Cloud)',
  defaultCommand: FACT.command,
  // Effort is settings-only (model.reasoningEffort) plus the in-session
  // /effort command; no launch flag.
  modelArgs: (m) => `${FACT.model_flag} ${m}`,
  skipPermissionFlag: FACT.permission_flag,
  resumeArgs: (id) => fillId(FACT.resume_args, id),
  needsSessionMarker: true,
  turnEndInferredFromSilence: true,
  supportsRebuild: true,
  // Measured on a real PTY: the CLI itself emits `ESC[?1049h` during startup
  // (probe read 17768 bytes of startup output) and keeps the conversation there.
  fullScreenTui: true,
  // A line appended to the file the pane was launched watching. It reaches the
  // CLI's own message queue, never its composer, so someone typing in the pane
  // has nothing to lose by a message arriving — `holdsInputBox` is false.
  //
  // The mid-turn hold is still kept, and deliberately: the CLI aggregates
  // several plain queued messages into ONE submission, so pushing a second
  // message into a busy pane can merge two agents' messages into a single turn.
  pushChannel: { kind: 'input-file', holdsInputBox: false },
  hint: 'generalist',
  // Quota failover: qwen.py _QWEN_WINDOWS — per5Hour / perWeek / perBillMonth,
  // each only when the plan reports a total for it.
  quotaSemantics: { hard: ['session', 'weekly', 'monthly'], required: ['session'] },
} as const satisfies AgentSpec
