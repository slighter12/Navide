import { describe, expect, it } from 'vitest'
// @ts-ignore -- plain ESM helper shared with the hosted workflows, no declarations
import { checkReceipts, dirStem, requiredIds } from './g2Native.mjs'

describe('requiredIds', () => {
  it('reads ids with spaces and skips blanks and comments', () => {
    expect(requiredIds('# c\n\ntests/a.py::t[one two]\ntests/a.py::u\n')).toEqual(['tests/a.py::t[one two]', 'tests/a.py::u'])
  })

  it('fails on a malformed nonempty line', () => {
    expect(() => requiredIds('tests/a.py::t\ntests/a.py:t\n')).toThrow(/line 2 is not a node id/)
  })

  it('fails on a repeated id', () => {
    expect(() => requiredIds('tests/a.py::t\ntests/a.py::t\n')).toThrow(/line 2 repeats an id/)
  })
})

describe('checkReceipts', () => {
  const release = 'rel'
  const harness = 'har'
  const expectations = {
    cases: [
      { dir: 'test_h_case', tests: ['test_h_case'], kinds: ['harness'] },
      { dir: 'test_b3_release', tests: ['test_b3_release'], kinds: ['release'] },
    ],
  }
  const run = (dirs: Record<string, string[]>) =>
    checkReceipts({
      dirs: new Map(Object.entries(dirs).map(([dir, hashes]) => [dir, new Set(hashes)])),
      release,
      harness,
      expectations,
      stubHashes: { peer: 'stub' },
      requiredNames: new Set(['test_h_case', 'test_b3_release']),
    }).problems

  it('strips only the trailing pytest counter from a directory name', () => {
    expect(dirStem('test_b3_release0')).toBe('test_b3_release')
    expect(dirStem('test_x_violations12')).toBe('test_x_violations')
  })

  it('accepts a harness case, a release case and an unlisted release directory', () => {
    expect(run({ test_h_case0: [harness], test_b3_release0: [release], test_other0: [release] })).toEqual([])
  })

  it('fails a release case that ran a different binary, even the harness one', () => {
    const problems = run({ test_h_case0: [harness], test_b3_release0: [harness] })
    expect(problems).toEqual([
      'foreign harness in test_b3_release0 (allowed: release)',
      'missing release evidence in test_b3_release',
    ])
  })

  it('fails the harness hash in an unlisted directory', () => {
    expect(run({ test_h_case0: [harness], test_b3_release0: [release], test_other0: [harness] })).toEqual([
      'foreign harness in test_other0 (allowed: release)',
    ])
  })

  it('fails a listed case that no longer spawns its sidecar', () => {
    expect(run({ test_b3_release0: [release] })).toEqual(['missing harness evidence in test_h_case'])
  })
})
