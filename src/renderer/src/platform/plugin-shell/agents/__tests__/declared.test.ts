/**
 * The launch and resume fields of every agent spec come from the runtime's
 * declarations (`_declared.ts`, reading `cli_declarations.json`).
 *
 * `MAIN` is what each spec file itself held before it read them: the label,
 * the command, and the arguments its functions built. The declarations must
 * keep giving exactly these.
 */
import { describe, expect, it } from 'vitest'
import { AGENT_SPECS } from '../index'

interface Held {
  label: string
  command: string
  yolo?: string
  model?: string
  effort?: string
  efforts?: readonly string[]
  resume?: string
  kind?: 'agent'
}

const MAIN: Record<string, Held> = {
  aider: { label: 'Aider', command: 'aider', yolo: '--yes-always' },
  antigravity: {
    label: 'Antigravity CLI (Google)',
    command: 'agy',
    yolo: '--dangerously-skip-permissions',
    model: '--model M',
    effort: '--effort E',
    efforts: ['low', 'medium', 'high'],
    resume: '--conversation ID',
  },
  claude: {
    label: 'Claude Code (Anthropic)',
    command: 'claude',
    yolo: '--dangerously-skip-permissions',
    model: '--model M',
    effort: '--effort E',
    efforts: ['low', 'medium', 'high', 'xhigh', 'max'],
    resume: '--resume ID',
  },
  codex: {
    label: 'Codex CLI (OpenAI)',
    command: 'codex',
    yolo: '--dangerously-bypass-approvals-and-sandbox',
    model: '--model M',
    resume: 'resume ID',
  },
  copilot: {
    label: 'GitHub Copilot CLI',
    command: 'copilot',
    yolo: '--yolo',
    model: '--model M',
    effort: '--reasoning-effort E',
    efforts: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
    resume: '--resume=ID',
  },
  cursor: { label: 'Cursor CLI', command: 'agent', yolo: '--force', model: '--model M', resume: '--resume=ID' },
  devin: { label: 'Devin CLI (Cognition)', command: 'devin', yolo: '--permission-mode dangerous' },
  droid: { label: 'Droid CLI (Factory)', command: 'droid', yolo: '--auto high', resume: '--resume ID' },
  goose: { label: 'Goose (AAIF)', command: 'goose', resume: 'session -r --session-id ID' },
  grok: { label: 'Grok Build (SpaceXAI)', command: 'grok', model: '--model M', resume: '-r ID' },
  hermes: { label: 'Hermes Agent', command: 'hermes chat', yolo: '--yolo', resume: '--continue ID', kind: 'agent' },
  kilo: { label: 'Kilo Code CLI', command: 'kilo', yolo: '--auto', model: '--model M', resume: '--session ID' },
  kimi: {
    label: 'Kimi Code CLI (Moonshot AI)',
    command: 'kimi',
    yolo: '--yolo',
    model: '--model M',
    resume: '--session ID',
  },
  kiro: { label: 'Kiro CLI (AWS)', command: 'kiro-cli chat --v3', yolo: '--trust-all-tools' },
  mcode: { label: 'MiniMax Code', command: 'mcode' },
  muse: {
    label: 'Muse Code (Meta)',
    command: 'muse',
    yolo: '--disable-approval',
    model: '--model M',
    effort: '--reasoning-effort E',
    efforts: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
    resume: 'resume ID',
  },
  opencode: { label: 'OpenCode (Anomaly)', command: 'opencode', model: '--model M', resume: '--session ID' },
  openclaw: { label: 'OpenClaw', command: 'openclaw tui --local', resume: '--session ID', kind: 'agent' },
  pi: {
    label: 'Pi',
    command: 'pi',
    model: '--model M',
    effort: '--thinking E',
    efforts: ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
    resume: '--session-id ID',
  },
  qwen: { label: 'Qwen Code (Alibaba Cloud)', command: 'qwen', yolo: '--yolo', model: '--model M', resume: '--resume ID' },
}

describe('agent specs built from the declarations', () => {
  const vendors = AGENT_SPECS.filter((spec) => spec.agentKey !== 'terminal')

  it('covers every vendor', () => {
    expect(vendors.map((spec) => spec.agentKey).sort()).toEqual(Object.keys(MAIN).sort())
  })

  it.each(Object.entries(MAIN))('%s launches and resumes as on main', (key, main) => {
    const spec = vendors.find((candidate) => candidate.agentKey === key)!
    const held: Held = { label: spec.label, command: spec.defaultCommand }
    if (spec.skipPermissionFlag !== undefined) held.yolo = spec.skipPermissionFlag
    if (spec.modelArgs) held.model = spec.modelArgs('M')
    if (spec.effortArgs) held.effort = spec.effortArgs('E')
    if (spec.knownEfforts) held.efforts = [...spec.knownEfforts]
    if (spec.resumeArgs) held.resume = spec.resumeArgs('ID')
    if (spec.kind === 'agent') held.kind = 'agent'
    expect(held).toEqual(main)
  })

  it('puts a value with `$` in it into the arguments as written', () => {
    const claude = vendors.find((spec) => spec.agentKey === 'claude')!
    expect(claude.modelArgs?.('a$&b')).toBe('--model a$&b')
    expect(claude.resumeArgs?.("$'x")).toBe("--resume $'x")
  })
})
