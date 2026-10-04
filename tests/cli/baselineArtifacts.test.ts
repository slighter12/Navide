import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { expect, it } from 'vitest'
import { saveTrialArtifacts } from './baselineArtifacts'

it('persists a failed rescue and still saves the bounded remaining repetitions', () => {
  const root = mkdtempSync(join(tmpdir(), 'baseline-artifact-test-'))
  try {
    writeFileSync(join(root, 'first-backend.log'), 'http://127.0.0.1/mcp?t=SYNTHETIC_AUTH_SENTINEL')
    writeFileSync(join(root, 'restart-backend.log'), 'Authorization: Bearer SYNTHETIC_AUTH_SENTINEL')
    writeFileSync(join(root, 'first-identity.json'), '{"pid":11,"created":1}')
    writeFileSync(join(root, 'restart-identity.json'), '{"pid":12,"created":2}')
    for (let repeat = 0; repeat < 2; repeat++) {
      const prefix = join(root, `check-${repeat}`)
      const receipt = { passed: true, failure: null as string | null, events: [{ url: 'ws://127.0.0.1/ws?t=SYNTHETIC_AUTH_SENTINEL' }] }
      const error = saveTrialArtifacts(root, prefix, receipt, 'Cookie: session=SYNTHETIC_AUTH_SENTINEL', () => {
        execFileSync(process.execPath, ['-e', 'process.exit(7)'], { timeout: 5000 })
      })
      expect(error).toContain('rescue')
      expect(JSON.parse(readFileSync(`${prefix}.json`, 'utf8')).passed).toBe(false)
      expect(readFileSync(`${prefix}-first-identity.json`, 'utf8')).toContain('11')
      expect(readFileSync(`${prefix}-restart-identity.json`, 'utf8')).toContain('12')
      for (const suffix of ['.json', '.log', '-first-backend.log', '-restart-backend.log']) {
        expect(readFileSync(`${prefix}${suffix}`, 'utf8')).not.toContain('SYNTHETIC_AUTH_SENTINEL')
      }
    }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})

it.each(['identity', 'timeout'])('persists an actual %s rescue failure without preventing the next receipt', (mode) => {
  const root = mkdtempSync(join(tmpdir(), 'baseline-rescue-test-'))
  try {
    writeFileSync(join(root, 'identity.json'), JSON.stringify({ driver: { pid: 0 } }))
    const first = { passed: false, failure: 'untracked descendant' as string | null }
    const error = saveTrialArtifacts(root, join(root, 'first'), first, '', () => {
      if (mode === 'identity') {
        execFileSync(resolve(process.platform === 'win32' ? 'backend/.venv/Scripts/python.exe' : 'backend/.venv/bin/python'),
          ['-m', 'tests.cli_regression.support.runtime_baseline', '--rescue', root, '1'],
          { env: { HOME: root, USERPROFILE: root, APPDATA: root, LOCALAPPDATA: root, AGENT_TEAM_DATA_DIR: join(root, 'data'), PYTHONPATH: resolve('backend'), SYSTEMROOT: process.env.SYSTEMROOT }, timeout: 10000 })
      } else {
        execFileSync(process.execPath, ['-e', 'setTimeout(() => {}, 10000)'], { timeout: 100 })
      }
    })
    expect(error).toContain('rescue')
    expect(JSON.parse(readFileSync(join(root, 'first.json'), 'utf8')).passed).toBe(false)
    expect(JSON.parse(readFileSync(join(root, 'first.json'), 'utf8')).failure).toContain(mode === 'identity' ? 'AssertionError' : 'ETIMEDOUT')
    const next = { passed: false, failure: 'survivor' as string | null }
    saveTrialArtifacts(root, join(root, 'next'), next, '', () => { execFileSync(process.execPath, ['-e', 'process.exit(0)'], { timeout: 5000 }) })
    expect(JSON.parse(readFileSync(join(root, 'next.json'), 'utf8')).passed).toBe(false)
    expect(JSON.parse(readFileSync(join(root, 'next.json'), 'utf8')).failure).toContain('survivor')
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
