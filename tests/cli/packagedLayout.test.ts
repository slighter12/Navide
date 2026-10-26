import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
// @ts-ignore -- plain ESM helper shared with the hosted workflows, no declarations
import { assembleLayout, extraResourcesFor } from './packagedLayout.mjs'

const packageJson = JSON.parse(readFileSync('package.json', 'utf8'))

describe('package.json stages the CLI runtime beside the backend on every release target', () => {
  for (const [key, runtime, backend] of [
    ['mac', 'navide-cli-runtime', 'agent_team_backend'],
    ['linux', 'navide-cli-runtime', 'agent_team_backend'],
    ['win', 'navide-cli-runtime.exe', 'agent_team_backend.exe']
  ] as const) {
    it(`${key}: bin/${runtime} from build/cli-runtime, next to bin/${backend}`, () => {
      const entries = extraResourcesFor(key, packageJson) as Array<{ from: string; to: string; filter?: string[] }>
      expect(entries.find((entry) => entry.from === 'build/cli-runtime')).toEqual({
        from: 'build/cli-runtime', to: 'bin', filter: [runtime]
      })
      expect(entries.some((entry) => entry.to === `bin/${backend}`)).toBe(true)
    })
  }

  it('assembles exactly the sources that exist and lists the rest as absent', () => {
    const repo = mkdtempSync(join(tmpdir(), 'layout-'))
    mkdirSync(join(repo, 'backend', 'dist'), { recursive: true })
    mkdirSync(join(repo, 'build', 'cli-runtime'), { recursive: true })
    writeFileSync(join(repo, 'backend', 'dist', 'agent_team_backend'), 'b')
    writeFileSync(join(repo, 'build', 'cli-runtime', 'navide-cli-runtime'), 'r')
    writeFileSync(join(repo, 'package.json'), JSON.stringify(packageJson))
    const out = join(repo, 'out')
    const { staged, missing } = assembleLayout({ out, key: 'linux', repo })
    expect(staged.sort()).toEqual(['bin/agent_team_backend', 'bin/navide-cli-runtime'])
    expect(missing).toContain('build/stt')
  })
})
