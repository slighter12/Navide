/** Finite managed OpenCode contract. All inputs are OWN, JSON-decoded receipts. */
export const MANAGED_LINEAGE_GATES = [
  'managed-lineage-read', 'managed-record-shape', 'managed-runtime-owner', 'managed-unique-processes',
  'managed-intent-origin', 'managed-intent-sources', 'managed-chain-binding', 'managed-intent-argv',
  'managed-peer-self', 'managed-lineage-throw', 'managed-peer-birth', 'managed-peer-image', 'managed-peer-ready',
  'managed-root-birth', 'managed-root-start', 'managed-root-group',
  'managed-shell-resolution', 'managed-branch-source', 'managed-exec-identity',
  'managed-exec-parent', 'managed-fallback-distinct', 'managed-fallback-root-image',
  'managed-fallback-parent', 'managed-resume-argv',
] as const
export type ManagedLineageGate = typeof MANAGED_LINEAGE_GATES[number]

export function verifyManagedOpenCodeLineage(input: any, mark: (gate: ManagedLineageGate) => void): any {
  let rejection: ManagedLineageGate | undefined
  try {
  const { stage, root, child, producer, backend, runtime, ready, owned, identities, guard,
    shell, branch, intent, self, selfRow, sample, descriptor, guardSha256 } = input
  const check = (gate: ManagedLineageGate, condition: boolean): void => {
    mark(gate)
    if (!condition) { rejection = gate; throw new Error(gate) }
  }
  const integer = (value: unknown) => Number.isSafeInteger(value) && (value as number) > 0
  const birth = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value > 0
  const canonical = (value: any): any => Array.isArray(value) ? value.map(canonical)
    : value !== null && typeof value === 'object'
      ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])])) : value
  const same = (a: unknown, b: unknown) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b))
  const keys = (a: any, expected: string[]) => a !== null && typeof a === 'object' && !Array.isArray(a)
    && same(Object.keys(a).sort(), expected.slice().sort())
  check('managed-record-shape', (stage === 1 || stage === 2) && typeof root === 'string'
    && [child, producer, backend, runtime, ready, owned, identities, guard, shell, branch, intent, self, selfRow, descriptor].every(Boolean)
    && Array.isArray(sample) && keys(branch, ['version', 'root', 'shell_pid', 'parent', 'branch', 'exit_class', 'sources'])
    && branch.version === 1 && branch.root === root && integer(branch.shell_pid) && integer(branch.parent)
    && ((branch.branch === 'shell-check-exec' && branch.exit_class === 0)
      || (branch.branch === 'shell-check-unverified' && branch.exit_class === 76)))
  check('managed-runtime-owner', ready.owner_pid === backend.pid && owned.runtime_generation === ready.runtime_generation
    && integer(owned.root?.pid) && integer(owned.root?.pgid))
  const ptyRoot = sample.find((p: any) => p.pid === owned.root.pid)
  check('managed-unique-processes', [producer, backend, runtime, ptyRoot].every((p: any) => p && integer(p.pid)
    && birth(p.created) && sample.filter((r: any) => r.pid === p.pid).length === 1))
  check('managed-intent-origin', intent.version === 1 && intent.root === root
    && intent.backend_pid === backend.pid && intent.backend_created === backend.created
    && intent.guard_sha256 === guardSha256 && intent.descriptor_sha256 === identities['managed-wrapper'].sha256)
  check('managed-intent-sources', same(intent.sources, { builder: descriptor.sources.builder.sha256,
    helper: descriptor.sources.helper.sha256, startup: guard.files.os_startup.sha256 })
    && descriptor.root === root && descriptor.helper.sha256 === descriptor.sources.helper.sha256)
  check('managed-chain-binding', same(intent.validated_chain, { interpreter: identities.interpreter,
    profile: identities.profile, script: identities.script, shim: identities.shim })
    && identities.shim.path === `${root}/opencode` && identities.profile.path === `${root}/home/.bash_profile`
    && identities.script.path === guard.files.native_difference_peer.path)
  const peerArgv = [identities.interpreter.path, '-I', '-S', '-B', '-u', identities.script.path, root, 'opencode', ...child.argv]
  check('managed-intent-argv', Array.isArray(intent.peer_argv) && same(intent.peer_argv, peerArgv)
    && same(producer.argv, intent.peer_argv) && producer.argv_phase === 'self-producer')
  const selfKeys = ['pid', 'created', 'parent', 'exe', 'image', 'role', 'source', 'phase']
  const expectedSelf = { pid: producer.pid, created: producer.created, parent: producer.parent,
    exe: identities.interpreter.path, image: identities.interpreter, role: 'peer',
    source: identities.script, phase: 'self-producer' }
  check('managed-peer-self', keys(self, selfKeys) && keys(selfRow, [...selfKeys, 'argv'])
    && same(self, expectedSelf) && same(selfRow, { ...expectedSelf, argv: peerArgv })
    && same(selfRow.source, guard.files.native_difference_peer)
    && same(selfRow.image, guard.files.interpreter))
  check('managed-peer-birth', self.created === producer.created && self.parent === producer.parent
    && birth(producer.created) && producer.created >= ptyRoot.created && ptyRoot.created >= runtime.created
    && runtime.created >= backend.created)
  check('managed-peer-image', producer.exe === identities.interpreter.path
    && producer.image?.sha256 === identities.interpreter.sha256
    && producer.image.sha256 === guard.files.interpreter.sha256
    && identities.script.sha256 === guard.files.native_difference_peer.sha256)
  check('managed-peer-ready', child.pid === producer.pid && child.parent_claim === producer.parent
    && child.interpreter_claim === identities.interpreter.path && child.script_sha256 === identities.script.sha256
    && child.isatty === true && child.ctty === true && child.stdout_producer === 'synthetic-fixture-script')
  check('managed-root-birth', branch.shell_pid === owned.root.pid && branch.parent === runtime.pid
    && ptyRoot.created > 0 && ptyRoot.parent === runtime.pid)
  check('managed-root-start', owned.root.start_kind === 'posix-lstart' && typeof owned.root.start_value === 'string'
    && owned.root.start_value.length > 0 && ptyRoot.legacy_start === owned.root.start_value)
  check('managed-root-group', producer.pgid === owned.root.pgid && ptyRoot.pgid === owned.root.pgid)
  check('managed-shell-resolution', keys(shell, ['shell_pid', 'parent', 'path', 'resolved', 'PS1_empty', 'TERM_PROGRAM_empty'])
    && shell.shell_pid === owned.root.pid && shell.parent === runtime.pid && shell.path === guard.shell_path
    && shell.resolved === identities.shim.path && shell.PS1_empty === true && shell.TERM_PROGRAM_empty === true)
  check('managed-branch-source', keys(branch.sources, ['builder', 'helper', 'startup'])
    && same(branch.sources, intent.sources) && identities.profile?.sha256 !== undefined)
  if (branch.branch === 'shell-check-exec') {
    check('managed-exec-identity', producer.pid === owned.root.pid && producer.created === ptyRoot.created
      && producer.exe === ptyRoot.exe && producer.image.sha256 === ptyRoot.image?.sha256)
    check('managed-exec-parent', producer.parent === runtime.pid)
  } else {
    check('managed-fallback-distinct', producer.pid !== owned.root.pid && producer.created > ptyRoot.created)
    check('managed-fallback-root-image', ptyRoot.exe === identities.bash.path && ptyRoot.image?.sha256 === identities.bash.sha256)
    check('managed-fallback-parent', producer.parent === owned.root.pid)
  }
  check('managed-resume-argv', stage === 2
    ? child.argv[0] === '-s' && child.argv[1] === 'ses_native_difference'
    : !child.argv.some((a: string) => a === '-s' || a === '--session'))
  return { mode: branch.branch, root_process: ptyRoot, branch, intent, peer_self: self }
  } catch {
    const gate = rejection ?? 'managed-lineage-throw'
    mark(gate)
    throw new Error(gate) // Fixed provenance only, never the caught payload.
  }
}
