/**
 * What a pane's launch is composed to, and how it reaches the terminal: the spawn seam of `spawnPane` (App.vue).
 *
 * `composeLaunch` picks the command (a caller's override, the stored launch command, or the vendor's own fresh
 * launch) and the words it is composed from; a caller's override carries its words with it (`commandWords`), the
 * launch object being the only channel, so a rebuilt resume launch reaches the spawn as words and any other override
 * as one literal. `spawnLaunch` hands the result to the terminal port.
 */
import {
  AGENT_SPECS,
  chooseLaunchCommand,
  modelArgsFor,
  type CliModelRequest,
  type LaunchCommandSource,
} from '@navide/plugin-shell'
import { shellCommandArgv } from '../../../shared/osplat'
import {
  freshLaunchWords,
  literalWords,
  verifiedWords,
  withSessionIdWords,
  wordsText,
  type LaunchWord,
} from '../../../shared/launchWords'
import { pinFreshSessionAtLaunch } from './sessionHeal'

export const NO_MODEL_REQUEST: CliModelRequest = { model: '', effort: '' }

export interface LaunchInput {
  agentKey: string
  /** The caller's own command line (a rebuilt resume command, an MCP-supplied command), or ''. */
  commandOverride: string
  /** The words the caller composed `commandOverride` from; used only for a caller's command, and only when they spell it. */
  commandWords?: readonly LaunchWord[]
  /** The vendor's stored launch command setting, or ''. */
  storedCommand: string
  isLogin: boolean
  isResume: boolean
  /** A saved id a fresh spawn reuses instead of minting a new one. */
  freshSessionId?: string
  /** The permission flag a fresh launch carries, or ''. */
  skipFlag: string
  /** The vendor's own per-pane argument, or ''. */
  paneArg: string
  model: CliModelRequest
  userShell: string
  newSessionId: () => string
}

export interface LaunchPlan {
  agentKey: string
  command: string
  words: LaunchWord[]
  source: LaunchCommandSource
  /** The id the launch pins the session to ('' when it pins none). */
  explicitSessionId: string
  /** What reached argv: a stored command is the user's whole line, so no model or effort did. */
  launchedModel: CliModelRequest
}

export function composeLaunch(input: LaunchInput): LaunchPlan {
  const { agentKey } = input
  const spec = AGENT_SPECS.find((s) => s.agentKey === agentKey)
  const chosen = chooseLaunchCommand({
    callerCommand: input.commandOverride,
    storedCommand: input.storedCommand,
    isLogin: input.isLogin,
  })
  let command = chosen.command
  let words: LaunchWord[]
  if (chosen.source !== 'none') {
    // An override is trusted verbatim: the permission flag, the pane argument and the model are all things Navide
    // adds, and an override means the user is writing the line. A caller's launch brings the words it was composed
    // from; any other override (the stored one included) is one literal.
    const given = chosen.source === 'caller' ? input.commandWords : undefined
    words = given && wordsText(given) === command ? [...given] : literalWords(command)
  } else {
    const program = spec?.defaultCommand ?? agentKey
    const parts = [program]
    if (input.paneArg) parts.push(input.paneArg)
    if (input.skipFlag) parts.push(input.skipFlag)
    // Model/effort last, the same order buildResumeCommand uses. A request the vendor cannot honour is dropped
    // here and said so; the authoritative refusal happens before a spawn reaches this point.
    const model = modelArgsFor({ spec, request: input.model })
    if (model.ok) {
      if (model.args) parts.push(model.args)
    } else {
      console.warn(
        `[resolveCommand] ${agentKey} cannot take model="${input.model.model}" effort="${input.model.effort}" `
        + `(${model.refusal.kind}); launching on the vendor default`,
      )
    }
    command = parts.join(' ')
    words = freshLaunchWords({
      agentKey,
      program,
      paneArg: input.paneArg,
      skipPermissions: Boolean(input.skipFlag),
      model: model.ok ? input.model.model.trim() : '',
      effort: model.ok ? input.model.effort.trim() : '',
    })
  }
  const launchedModel = chosen.source === 'stored' ? NO_MODEL_REQUEST : { ...input.model }
  if (agentKey === 'terminal' && !command) {
    command = input.userShell
    words = literalWords(command)
  }
  // A fresh Claude launch pins a unique --session-id so the backend maps this pane's events to it. The pin appends
  // one `--session-id <id>` option; a handwritten one is already in the text.
  const pinned = pinFreshSessionAtLaunch(agentKey, input.isResume, command, input.freshSessionId, input.newSessionId)
  if (pinned.command !== command) words = withSessionIdWords(agentKey, words, pinned.explicitSessionId)
  return {
    agentKey,
    command: pinned.command,
    words,
    source: chosen.source,
    explicitSessionId: pinned.explicitSessionId,
    launchedModel,
  }
}

export interface LaunchFields {
  command: string | string[]
  words?: LaunchWord[]
}

/** The `command` and `words` a terminal spawn carries for a plan. The words go only when they spell exactly the
 *  command: the backend then takes the command as one literal. */
export function launchFields(plan: LaunchPlan, userShell: string): LaunchFields {
  return {
    command: shellCommandArgv(userShell, plan.command, { agentPane: plan.agentKey !== 'terminal' }),
    words: verifiedWords(plan.command, plan.words),
  }
}

/** Starts the plan on a terminal port; `rest` is everything else the spawn carries. */
export function spawnLaunch<Rest extends object, Result>(
  port: { spawn(request: LaunchFields & Rest): Promise<Result> },
  plan: LaunchPlan,
  userShell: string,
  rest: Rest,
): Promise<Result> {
  return port.spawn({ ...launchFields(plan, userShell), ...rest })
}
