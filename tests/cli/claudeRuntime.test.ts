import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { expect, it } from 'vitest'
import { buildAgentPaneCommand, quoteCommandWord } from '../../src/main/plugins/agentPaneCommand'
import { SPEC } from '../../src/renderer/src/platform/plugin-shell/agents/claude'
import { modelArgsFor } from '../../src/renderer/src/platform/plugin-shell/lib/cliModel'
import { buildResumeCommand } from '../../src/renderer/src/platform/plugin-shell/lib/resume-command'
import { createWsClient, type TerminalOutputFrame } from '../../src/shared/wsClient'
import { redactDiagnostic } from './baselineArtifacts'

declare module 'vitest' {
  interface TaskMeta { cliRuntimeReference?: string }
}

async function until(predicate: () => boolean, message: string, timeout = 15_000): Promise<void> {
  const deadline = Date.now() + timeout
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(message)
    await delay(5)
  }
}

// The same controlled records and independent totals as the Python WS
// checkpoint supplement. This prepares external vendor data, not app state.
function stages(root: string): number {
  const prompt = { type: 'user', promptId: 'prompt-1', message: { content: 'Implement the fixture' } }
  const question = { type: 'assistant', requestId: 'request-1', message: {
    id: 'msg-1', model: 'fixture-model', stop_reason: 'tool_use',
    content: [{ type: 'tool_use', name: 'AskUserQuestion', input: {} }],
    usage: { input_tokens: 10, cache_read_input_tokens: 2, cache_creation_input_tokens: 3, output_tokens: 4 },
  } }
  const background = { type: 'user', message: { content: [{ type: 'tool_result' }] }, toolUseResult: { backgroundTaskId: 'bg-1' } }
  const completion = { type: 'assistant', requestId: 'request-2', version: '99.0.0-regression', message: {
    id: 'msg-2', stop_reason: 'end_turn', content: [{ type: 'text', text: 'Reference completed café 世界' }],
    usage: { input_tokens: 7, output_tokens: 5 },
  } }
  const full = Buffer.from(JSON.stringify(completion))
  const split = Math.floor(full.length / 2)
  const prefix = Buffer.from([prompt, question, question, background].map((r) => JSON.stringify(r) + '\n').join('') + 'corrupt complete record\n')
  writeFileSync(join(root, 'stage-1.bin'), Buffer.concat([prefix, full.subarray(0, split)]))
  const notification = { type: 'queue-operation', operation: 'enqueue', content: '<task-notification><task-id>bg-1</task-id><task-id>bg-2</task-id><status>completed</status></task-notification>' }
  const monitor = { ...notification, content: '<task-notification><task-id>bg-1</task-id>monitor</task-notification>' }
  const stop = { type: 'assistant', message: { stop_reason: 'tool_use', content: [{ type: 'tool_use', name: 'TaskStop', input: { task_id: 'bg-3' } }] } }
  const duplicate = { ...completion, message: { ...completion.message, stop_reason: 'tool_use' } }
  writeFileSync(join(root, 'stage-2.bin'), Buffer.concat([full.subarray(split), Buffer.from('\n' + [monitor, notification, stop, duplicate].map((r) => JSON.stringify(r) + '\n').join(''))]))
  return prefix.length
}

async function withBackend(fault: string, run: (root: string, driver: ReturnType<typeof spawn>, endpoint: (stage: number) => Promise<any>, firstOffset: number) => Promise<void>): Promise<void> {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'navide-rust-claude-')))
  const firstOffset = stages(root)
  const driver = spawn(resolve('backend/.venv/bin/python'), ['-m', 'tests.cli_regression.support.claude_runtime_driver', root, fault], {
    cwd: resolve('backend'), stdio: ['pipe', 'pipe', 'pipe'],
  })
  let log = ''
  let code: number | null | undefined
  driver.stdout!.on('data', (data) => { log += data })
  driver.stderr!.on('data', (data) => { log += data })
  driver.on('error', (error) => { log += String(error); code = -1 })
  driver.on('exit', (value) => { code = value })
  async function endpoint(stage: number): Promise<any> {
    await until(() => {
      if (code !== undefined) throw new Error(redactDiagnostic(log))
      return existsSync(join(root, `endpoint-${stage}.json`))
    }, 'Claude backend readiness', 35_000)
    return JSON.parse(readFileSync(join(root, `endpoint-${stage}.json`), 'utf8'))
  }
  try {
    await run(root, driver, endpoint, firstOffset)
  } finally {
    driver.stdin!.end('stop\n')
    await until(() => code !== undefined, 'Claude driver cleanup', 25_000)
    const reports = resolve('test-results/ci/claude-runtime', `${fault || 'native'}-${basename(root)}`)
    mkdirSync(reports, { recursive: true })
    writeFileSync(join(reports, 'driver.log'), redactDiagnostic(log))
    for (const name of readdirSync(root)) {
      // No authenticated endpoint URL or sandbox credential/config files cross
      // the artifact boundary. Keep child argv after normal diagnostic redaction.
      if (/^(stage-[12]-(backend\.log|scenario\.json|durable\.json|process-identities\.jsonl)|runtime-receipts\.jsonl|claude-(child|stopped)-\d+\.json|principal\.json)$/.test(name)) {
        writeFileSync(join(reports, name), redactDiagnostic(readFileSync(join(root, name), 'utf8')))
      }
    }
    expect(code, redactDiagnostic(log)).toBe(0)
    rmSync(root, { recursive: true, force: true })
  }
}

it.runIf(process.platform === 'darwin')('native Claude production-client checkpoint and persisted resume', async ({ task }) => {
  await withBackend('', async (root, driver, endpoint, firstOffset) => {
    const tids: string[] = []
    const backends: any[] = []
    const settings = { env: { REFERENCE_MARKER: "café 世界 quote' \" \\ ; | &" } }
    const modelRequest = { model: 'fixture-model', effort: 'high' }
    const model = modelArgsFor({ spec: SPEC, request: modelRequest })
    expect(model.ok).toBe(true)
    if (!model.ok) throw new Error('model request refused')
    for (const stage of [1, 2]) {
      const wire = await endpoint(stage)
      backends.push(wire.backend)
      const client = createWsClient()
      const output = new Map<string, string>()
      const decoder = new TextDecoder()
      const activity: any[] = []
      client.on('terminal.output', (payload) => {
        const frame = payload as TerminalOutputFrame
        output.set(frame.terminal_session_id, (output.get(frame.terminal_session_id) ?? '') + decoder.decode(frame.data, { stream: true }))
      })
      client.on('agent.activity', (payload) => activity.push(payload))
      async function request<T = any>(type: string, payload: Record<string, unknown>): Promise<T> {
        const response = await client.send<T>(type, payload, 15_000)
        expect(response.ok, JSON.stringify(response)).toBe(true)
        return response.payload!
      }
      try {
        client.connect(wire.url)
        let base: string
        if (stage === 1) {
          await request('manual_pane.spawn', { workspace_path: wire.workspace, pane_id: 'production-reference', agent: 'claude',
            command: SPEC.defaultCommand, session_id: wire.sessionId, ...modelRequest })
          base = `${SPEC.defaultCommand} ${model.args} ${SPEC.pinsSessionIdAtLaunch.flag} ${wire.sessionId}`
        } else {
          const project = await request('project.get', { workspace_path: wire.workspace })
          const pane = project.project.panes.find((p: any) => p.pane_id === 'production-reference')
          expect(pane).toMatchObject({ model: 'fixture-model', effort: 'high', session_id: wire.sessionId })
          base = buildResumeCommand('claude', pane.session_id, '', '', { model: pane.model, effort: pane.effort })
          expect(base).toBe(`claude --resume ${wire.sessionId} --model fixture-model --effort high`)
          const exists = await request('agent.session_exists', { agent: 'claude', workspace_path: wire.workspace, session_id: pane.session_id })
          expect(exists.exists).toBe(true)
          const before = await request('tokens.snapshot', { workspace_path: wire.workspace })
          expect([before.global.all_time.input, before.global.all_time.output]).toEqual([15, 4])
        }
        const command = buildAgentPaneCommand(wire.shell, [base, '--settings', quoteCommandWord(JSON.stringify(settings))])
        const created = await request<{ terminal_session_id: string }>('terminal.create', { pane_id: 'production-reference', agent_key: 'claude',
          command, cwd: wire.workspace, env: { REFERENCE_MARKER: settings.env.REFERENCE_MARKER },
          cols: 100, rows: 30, create_generation: `reference-${stage}`,
          metadata: { explicit_session_id: wire.sessionId } })
        const tid = created.terminal_session_id
        tids.push(tid)
        await until(() => (output.get(tid) ?? '').includes('CLAUDE_REFERENCE_READY '), 'actual Claude child READY')
        const child = JSON.parse(output.get(tid)!.split('CLAUDE_REFERENCE_READY ')[1].split('\r\n')[0])
        expect(child).toMatchObject({ cwd: wire.workspace, home: wire.home, shell_rc: 'login', marker: settings.env.REFERENCE_MARKER, isatty: true, ctty: true })
        const args: string[] = child.argv
        expect(args[args.indexOf('--model') + 1]).toBe('fixture-model')
        expect(args[args.indexOf('--effort') + 1]).toBe('high')
        expect(args[args.indexOf(stage === 1 ? '--session-id' : '--resume') + 1]).toBe(wire.sessionId)
        expect(JSON.parse(args[args.indexOf('--settings') + 1]).env.REFERENCE_MARKER).toBe(settings.env.REFERENCE_MARKER)
        if (stage === 2) expect(activity).toEqual([])
        await request('terminal.input', { terminal_session_id: tid, data: `stage ${stage}\r` })
        await until(() => (output.get(tid) ?? '').includes(`CLAUDE_STAGE ${stage}`), 'vendor bytes appended')
        await until(() => stage === 1
          ? activity.some((e) => e.detail === 'assistant:question') && activity.some((e) => e.detail === 'background:start')
          : activity.filter((e) => e.detail === 'background:end').length === 2, 'native activity sink')
        await expect.poll(async () => {
          const snapshot = await request('tokens.snapshot', { workspace_path: wire.workspace })
          const live = snapshot.workspace.live_by_session[wire.sessionId]
          return [live?.input, live?.output]
        }, { timeout: 15_000, interval: 50 }).toEqual(stage === 1 ? [15, 4] : [22, 9])
        expect(activity.every((e) => e.pane_id === 'production-reference' && e.workspace_path === wire.workspace)).toBe(true)
        const completions = activity.filter((e) => e.event_type === 'turn_complete')
        expect(completions.map((e) => e.text)).toEqual(stage === 1 ? [] : ['Reference completed café 世界'])
        if (stage === 2) {
          expect(activity.some((e) => e.detail === 'assistant:question')).toBe(false)
          expect(activity.filter((e) => e.detail === 'background:end').map((e) => e.text)).toEqual(['bg-1\nbg-2', 'bg-3'])
          const turns = await request('tokens.turns', { pane_id: 'production-reference', include_calls: true })
          expect(turns.totals).toEqual({ input: 17, cache_read: 2, cache_creation: 3, output: 9, total: 31, calls: 2 })
        }
        await request('terminal.kill', { terminal_session_id: tid, force: true })
        expect(existsSync(join(root, `claude-stopped-${child.pid}.json`))).toBe(true)
      } finally {
        client.dispose('Claude reference stage complete')
      }
      driver.stdin!.write(stage === 1 ? 'restart\n' : 'stop\n')
      await until(() => existsSync(join(root, `stage-${stage}-closed`)), 'durable backend shutdown', 25_000)
      const durable = JSON.parse(readFileSync(join(root, `stage-${stage}-durable.json`), 'utf8'))
      expect(durable.usage.kind).toBe('jsonl')
      expect(durable.usage.offset).toBe(stage === 1 ? firstOffset : durable.file_size)
      expect(durable.usage.recent_keys).toHaveLength(stage)
      expect(durable.activity.keys).toEqual([`act_hw::${stage === 1 ? 5 : 10}`])
      expect([durable.totals.input, durable.totals.output]).toEqual(stage === 1 ? [15, 4] : [22, 9])
    }
    expect(new Set(tids).size).toBe(2)
    const receipts = readFileSync(join(root, 'runtime-receipts.jsonl'), 'utf8').trim().split('\n').map((line) => JSON.parse(line))
    const ready = receipts.filter((r) => r.event === 'ready')
    expect(ready).toHaveLength(2)
    expect(new Set(ready.map((r) => r.runtime_generation)).size).toBe(2)
    const binarySha = createHash('sha256').update(readFileSync(resolve('native/navide-cli-runtime/target/release/navide-cli-runtime'))).digest('hex')
    expect(new Set(ready.map((r) => r.executableSha256))).toEqual(new Set([binarySha]))
    for (let index = 0; index < 2; index++) {
      expect(ready[index]).toMatchObject({ runtime: 'navide-cli-runtime', owner_pid: backends[index].pid })
      expect(ready[index].executableSha256).toMatch(/^[a-f0-9]{64}$/)
      const identities = readFileSync(join(root, `stage-${index + 1}-process-identities.jsonl`), 'utf8').trim().split('\n').flatMap((line) => JSON.parse(line))
      expect(identities.some((p: any) => p.pid === ready[index].pid && p.created === ready[index].created && p.parent === backends[index].pid)).toBe(true)
    }
    const owned = receipts.filter((r) => r.event === 'owned')
    const cleaned = receipts.filter((r) => r.event === 'cleaned')
    expect(owned.map((r) => r.session_id)).toEqual(tids)
    expect(cleaned.map((r) => r.session_id)).toEqual(tids)
    expect(cleaned.every((r) => r.cleanup_complete && r.root_reaped && !r.survivors.length && !r.unverifiable.length)).toBe(true)
    const observed = receipts.filter((r) => r.event === 'observed')
    expect(new Set(observed.map((r) => r.mode))).toEqual(new Set(['usage', 'activity', 'turns']))
    expect(observed.every((r) => r.parser === 'rust-claude-jsonl-v1' && r.native_session_id === '22222222-2222-4222-8222-222222222222'
      && r.identity && r.path.endsWith(`${r.native_session_id}.jsonl`) && ready.some((n) => n.runtime_generation === r.runtime_generation))).toBe(true)
    for (const owner of owned) expect(observed.some((r) => r.session_id === owner.session_id && r.session_generation === owner.session_generation
      && r.pane_id === 'production-reference' && r.workspace_path === owner.workspace_path)).toBe(true)
    writeFileSync(join(root, 'principal.json'), JSON.stringify({ seam: 'production TS WS client / authenticated Python / native Rust PTY / durable stores', tids, backends,
      ready, assertions: ['child argv/config', 'native usage/activity/turns', 'checkpoint restart', 'persisted model/effort resume', 'owned root cleanup'] }, null, 2))
  })
  task.meta.cliRuntimeReference = 'claude-checkpoint-resume'
}, 120_000)

it.runIf(process.platform === 'darwin')('native Claude production-client selected failure has no rescue', async ({ task }) => {
  await withBackend('missing', async (root, _driver, endpoint) => {
    const wire = await endpoint(1)
    const client = createWsClient()
    const output: unknown[] = []
    client.on('terminal.output', (payload) => output.push(payload))
    try {
      client.connect(wire.url)
      const response = await client.send('terminal.create', { pane_id: 'missing-reference', agent_key: 'claude', cwd: wire.workspace,
        command: buildAgentPaneCommand(wire.shell, ['claude --session-id', wire.sessionId]), create_generation: 'missing-reference' }, 15_000)
      expect(response.ok).toBe(false)
      expect(JSON.stringify(response)).toContain('Rust runtime binary missing at explicit override')
      expect(output).toEqual([])
      expect(readdirSync(root).some((name) => name.startsWith('claude-child-'))).toBe(false)
      writeFileSync(join(root, 'principal.json'), JSON.stringify({ seam: 'production TS WS client / authenticated Python / selected Rust startup refusal',
        actualRuntime: 'not started (missing executable)', response, noChildOrPythonPtyRescue: true }, null, 2))
    } finally {
      client.dispose('missing reference complete')
    }
  })
  task.meta.cliRuntimeReference = 'claude-selected-failure'
}, 60_000)
