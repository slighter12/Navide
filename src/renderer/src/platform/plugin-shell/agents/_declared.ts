/**
 * How each CLI is launched and resumed, read from the runtime's declarations.
 *
 * `native/navide-cli-runtime/src/vendors/<key>.rs` declares a CLI once.
 * `cli_declarations.json` is the launch and resume part of every declaration,
 * printed by `navide-cli-runtime declarations launch`. It is generated, never
 * edited: the file here is the same output as the one the backend's vendor
 * registry reads (`backend/agent_team_backend/cli_declarations.json`), kept
 * inside this module because plugin-shell imports nothing from outside
 * itself. `backend/tests/test_cli_declarations.py` fails when either is not
 * what the runtime prints, and rewrites both. A spec file spreads
 * `declared('<key>')` instead of retyping the command and its flags:
 *
 *   label, defaultCommand, skipPermissionFlag, modelArgs, effortArgs,
 *   knownEfforts, resumeArgs, kind
 *
 * A field the declaration leaves empty is absent from the result, never
 * `undefined`-valued, so a spec looks the same as one that omitted the line.
 */
import declarations from './cli_declarations.json'
import type { AgentSpec } from './types'

interface Declaration {
  key: string
  label: string
  kind: string
  launch: {
    program: string
    base_args: string[]
    yolo_args: string[]
    /** Argument template; `{}` is the value. */
    model: string[] | null
    effort: string[] | null
    efforts: string[]
  }
  /** Argument template after the program; `{id}` is the session id. */
  resume: { args: string[] | null }
}

export type DeclaredFields = Pick<
  AgentSpec,
  | 'label'
  | 'defaultCommand'
  | 'skipPermissionFlag'
  | 'modelArgs'
  | 'effortArgs'
  | 'knownEfforts'
  | 'resumeArgs'
  | 'kind'
>

const BY_KEY = new Map((declarations as Declaration[]).map((entry) => [entry.key, entry]))

/** `template` joined into one command-line string, `hole` replaced by the value as written. */
function filled(template: string[], hole: string): (value: string) => string {
  const [before, ...after] = template.join(' ').split(hole)
  return (value) => [before, ...after].join(value)
}

export function declared(key: string): DeclaredFields {
  const entry = BY_KEY.get(key)
  if (!entry) throw new Error(`no CLI declaration for "${key}" (cli_declarations.json)`)
  const { launch, resume } = entry
  const fields: DeclaredFields = {
    label: entry.label,
    defaultCommand: [launch.program, ...launch.base_args].join(' '),
  }
  if (launch.yolo_args.length) fields.skipPermissionFlag = launch.yolo_args.join(' ')
  if (launch.model) fields.modelArgs = filled(launch.model, '{}')
  if (launch.effort) fields.effortArgs = filled(launch.effort, '{}')
  if (launch.efforts.length) fields.knownEfforts = launch.efforts
  if (resume.args) fields.resumeArgs = filled(resume.args, '{id}')
  if (entry.kind === 'agent') fields.kind = 'agent'
  return fields
}
