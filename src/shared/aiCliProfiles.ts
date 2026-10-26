import { VENDOR_FACTS } from './vendorFacts'

/**
 * Host-owned executable profiles shared by renderer adapters and the main
 * process. The executable and unattended-mode flag come from the generated
 * vendor facts (`shared/agent-cli/<key>.json`), so the launch paths cannot
 * drift from the declarations; this file adds only the presentation label.
 */
export interface AiCliProfile {
  readonly label: string
  readonly command: string
  readonly yoloFlag?: string
}

function profile(key: keyof typeof VENDOR_FACTS, label: string): AiCliProfile {
  const facts = VENDOR_FACTS[key]
  return facts.permission_flag
    ? { label, command: facts.command, yoloFlag: facts.permission_flag }
    : { label, command: facts.command }
}

export const AI_CLI_PROFILES = {
  claude: profile('claude', 'Claude Code'),
  codex: profile('codex', 'Codex'),
  antigravity: profile('antigravity', 'Antigravity CLI'),
  grok: profile('grok', 'Grok CLI'),
  kimi: profile('kimi', 'Kimi Code'),
  opencode: profile('opencode', 'OpenCode'),
  qwen: profile('qwen', 'Qwen Code'),
  kilo: profile('kilo', 'Kilo Code'),
  pi: profile('pi', 'Pi'),
  copilot: profile('copilot', 'Copilot CLI'),
  cursor: profile('cursor', 'Cursor CLI'),
  aider: profile('aider', 'Aider'),
  muse: profile('muse', 'Muse Code'),
} as const satisfies Record<string, AiCliProfile>

export type AiCliProfileId = keyof typeof AI_CLI_PROFILES

/** Presentation metadata for the existing embedded editor terminal. Keeping
 * its richer profile set opt-in preserves current Git/Plans launch defaults. */
export const TERMINAL_AI_CLI_PROFILES = {
  ...AI_CLI_PROFILES,
  droid: profile('droid', 'Droid'),
} as const satisfies Record<string, AiCliProfile>

export const FULL_SCREEN_AI_CLI_PROFILES: readonly string[] = [
  'claude', 'antigravity', 'opencode', 'qwen', 'kilo', 'copilot',
]

export const BRACKETED_PASTE_AI_CLI_PROFILES: readonly string[] = [
  'claude', 'codex', 'antigravity', 'droid', 'grok', 'kimi',
]

/** Profiles whose terminal adapter uses a dedicated Shift+Enter sequence. */
export const AI_CLI_SHIFT_ENTER_SEQUENCES: Readonly<Record<string, string>> = {
  codex: '\x1b[13;2u',
}
