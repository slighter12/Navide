import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { boundedErrorCode, verifyFreeze } from './runtimeFreeze'

const sha = (data: string): string => createHash('sha256').update(data).digest('hex')
let repo = ''

function git(...args: string[]): string {
  return execFileSync('git', args, { cwd: repo, encoding: 'utf8' })
}

function makeFreeze(overrides: Record<string, unknown> = {}): { freeze: string; digest: string } {
  const binary = 'native/navide-cli-runtime/target/release/navide-cli-runtime'
  const freeze = {
    head: git('rev-parse', 'HEAD').trim(),
    trackedDiffSha256: createHash('sha256').update(execFileSync('git', ['diff', 'HEAD'], { cwd: repo })).digest('hex'),
    statusPorcelain: git('status', '--porcelain').split('\n').filter(Boolean),
    sidecarSha256: sha('binary-bytes'),
    inputs: { 'tests/a.ts': sha('A'), 'tests/b.py': sha('B'), [binary]: sha('binary-bytes') },
    ...overrides,
  }
  const freezePath = join(repo, 'freeze.json')
  writeFileSync(freezePath, JSON.stringify(freeze))
  writeFileSync(join(repo, 'freeze.sha256'), `${sha(JSON.stringify(freeze))}  freeze.json\n`)
  return { freeze: freezePath, digest: join(repo, 'freeze.sha256') }
}

beforeEach(() => {
  repo = mkdtempSync(join(tmpdir(), 'runtime-freeze-test-'))
  git('init', '-q')
  git('config', 'user.email', 'a@b.c')
  git('config', 'user.name', 'n')
  mkdirSync(join(repo, 'tests'), { recursive: true })
  mkdirSync(join(repo, 'native/navide-cli-runtime/target/release'), { recursive: true })
  writeFileSync(join(repo, 'tests/a.ts'), 'A')
  writeFileSync(join(repo, 'tests/b.py'), 'B')
  writeFileSync(join(repo, 'native/navide-cli-runtime/target/release/navide-cli-runtime'), 'binary-bytes')
  writeFileSync(join(repo, '.gitignore'), 'freeze.json\nfreeze.sha256\nnative/navide-cli-runtime/target\n')
  git('add', 'tests', '.gitignore')
  git('commit', '-q', '-m', 'init')
})
afterEach(() => rmSync(repo, { recursive: true, force: true }))

describe('verifyFreeze', () => {
  it('accepts an untouched frozen state and counts the inputs it re-hashed', () => {
    const { freeze, digest } = makeFreeze()
    const verdict = verifyFreeze(repo, freeze, digest)
    expect(verdict.failures).toEqual([])
    expect(verdict.checkedInputs).toBe(3)
    expect(verdict.freezeSha256).toMatch(/^[0-9a-f]{64}$/)
  })

  it.each([
    ['a changed runner/checker input', () => writeFileSync(join(repo, 'tests/a.ts'), 'A2'), 'frozen input changed: tests/a.ts'],
    ['a changed binary', () => writeFileSync(join(repo, 'native/navide-cli-runtime/target/release/navide-cli-runtime'), 'other'), 'frozen input changed: native/navide-cli-runtime/target/release/navide-cli-runtime'],
    ['a deleted input', () => rmSync(join(repo, 'tests/b.py')), 'frozen input is missing: tests/b.py'],
  ])('rejects %s', (_name, mutate, fragment) => {
    const { freeze, digest } = makeFreeze()
    mutate()
    expect(verifyFreeze(repo, freeze, digest).failures.join('\n')).toContain(fragment)
  })

  it('rejects a new commit (HEAD moved)', () => {
    const { freeze, digest } = makeFreeze()
    writeFileSync(join(repo, 'tests/c.ts'), 'C')
    git('add', 'tests/c.ts')
    git('commit', '-q', '-m', 'more')
    expect(verifyFreeze(repo, freeze, digest).failures.join('\n')).toContain('HEAD')
  })

  it('rejects a tracked-diff change that is not in any frozen input, and a new untracked file', () => {
    writeFileSync(join(repo, 'tests/a.ts'), 'A')
    const first = makeFreeze()
    writeFileSync(join(repo, 'README'), 'x')
    git('add', 'README')
    expect(verifyFreeze(repo, first.freeze, first.digest).failures.join('\n')).toContain('tracked diff')
    git('reset', '-q', 'README')
    writeFileSync(join(repo, 'stray.txt'), 's')
    expect(verifyFreeze(repo, first.freeze, first.digest).failures.join('\n')).toContain('porcelain')
  })

  it('rejects a freeze file that does not match its recorded digest', () => {
    const { freeze, digest } = makeFreeze()
    writeFileSync(freeze, JSON.stringify({ ...JSON.parse(execFileSync('cat', [freeze], { encoding: 'utf8' })), note: 'edited' }))
    expect(verifyFreeze(repo, freeze, digest).failures.join('\n')).toContain('freeze file digest')
  })

  it('rejects a freeze whose sidecar hash and binary input disagree', () => {
    const { freeze, digest } = makeFreeze({ sidecarSha256: sha('another') })
    expect(verifyFreeze(repo, freeze, digest).failures.join('\n')).toContain('disagree')
  })

  it('reports a missing freeze file instead of throwing', () => {
    expect(verifyFreeze(repo, join(repo, 'nope.json'), join(repo, 'nope.sha256')).ok).toBe(false)
  })
})

describe('boundedErrorCode', () => {
  it('keeps only a bounded code, never message text, argv or output', () => {
    const error = Object.assign(new Error('Command failed: pmset -g batt /Users/someone/secret'), { code: 'ENOENT', stdout: 'proc names', stderr: 'argv' })
    expect(boundedErrorCode(error)).toBe('ENOENT')
    expect(boundedErrorCode(Object.assign(new Error('x'), { status: 7 }))).toBe('EXIT_7')
    expect(boundedErrorCode(Object.assign(new Error('x'), { code: 'weird text with spaces /path' }))).toBe('ERROR')
    expect(boundedErrorCode('a string /with/path')).toBe('ERROR')
    expect(boundedErrorCode(null)).toBe('ERROR')
  })
})

describe('verifyFreeze fails closed on an empty or malformed freeze', () => {
  const failsWith = (overrides: Record<string, unknown>, fragment: string): void => {
    const { freeze, digest } = makeFreeze(overrides)
    expect(verifyFreeze(repo, freeze, digest).failures.join('\n')).toContain(fragment)
  }

  it('rejects a freeze with no inputs at all', () => failsWith({ inputs: {} }, 'no frozen inputs'))
  it('rejects a freeze with no sidecar binary entry even when the sidecar hash field is also absent', () => {
    const binary = 'native/navide-cli-runtime/target/release/navide-cli-runtime'
    failsWith({ inputs: { 'tests/a.ts': sha('A') }, sidecarSha256: undefined }, 'sidecar')
    failsWith({ inputs: { 'tests/a.ts': sha('A') }, sidecarSha256: sha('binary-bytes') }, binary)
  })
  it('rejects a malformed sidecar hash', () => failsWith({ sidecarSha256: 'not-a-hash', inputs: { 'tests/a.ts': sha('A'), 'native/navide-cli-runtime/target/release/navide-cli-runtime': 'not-a-hash' } }, 'sidecar'))
  it('rejects non-hex or non-string input hashes', () => failsWith({ inputs: { 'tests/a.ts': 7, 'native/navide-cli-runtime/target/release/navide-cli-runtime': sha('binary-bytes') } }, 'malformed'))
  it('rejects a missing head, tracked diff or porcelain record', () => {
    failsWith({ head: undefined }, 'HEAD')
    failsWith({ trackedDiffSha256: undefined }, 'tracked diff')
    failsWith({ statusPorcelain: undefined }, 'porcelain')
  })
})
