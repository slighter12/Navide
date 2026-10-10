import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { delimiter, join } from 'node:path'

// Candidate evaluation entrypoint; this does not select or download a runtime.
const required = '1.4.2'
if (process.versions.bun !== required) {
  console.error(`Bun ${required} is required for this evaluation; actual: ${process.versions.bun ?? 'not Bun'}. Install that exact version manually, then rerun bun scripts/bun-evaluation.mjs. No version manager is required.`)
  process.exit(1)
}

const [command, ...args] = process.argv.slice(2)
let invocation
switch (command) {
  case 'check':
    console.log(`Bun ${required}: ${process.execPath}`)
    process.exit(0)
  case 'install':
    if (args.length) {
      console.error('The ticket-02 install entrypoint accepts no flags; its lockfile is always frozen.')
      process.exit(1)
    }
    invocation = ['--bun', 'install', '--frozen-lockfile']
    break
  case 'run':
    if (![
      'test:run', 'typecheck', 'typecheck:node', 'typecheck:integration',
      'typecheck:web', 'typecheck:public', 'typecheck:plugin-git',
      'typecheck:plugin-plans', 'typecheck:plugin-mini-ide',
      'build', 'build:public-packages', 'build:official-plugins',
      'build:mini-ide', 'build:mini-ide:v2', 'build:plans',
      'build:plans:legacy', 'build:plans:v2', 'build:plans:backend',
      'build:git', 'build:git:legacy', 'build:git:v2',
      'build:pane-helper', 'build:fn-key',
      // 'dev' is the real development workflow: the same script a contributor
      // runs with Node, so Bun-only daily work can be checked end to end. It is
      // the one route whose children outlive the entrypoint (the Electron app
      // holds the pane until it is stopped), and `start` stays unavailable.
      'dev'
    ].includes(args[0])) {
      console.error('Only the bounded evaluation test/typecheck/build/dev scripts are permitted; start remains unavailable.')
      process.exit(1)
    }
    invocation = ['--bun', 'run', ...args]
    break
  case 'pack':
    invocation = ['pm', 'pack', ...args.map((arg) => arg === '--pack-destination' ? '--destination' : arg)]
    break
  case 'audit':
    invocation = ['audit', '--ignore=GHSA-ch52-4w7c-c8xp', ...args]
    break
  default:
    console.error('Usage: bun scripts/bun-evaluation.mjs <check|install|run|pack|audit> [arguments]')
    process.exit(1)
}

const env = { ...process.env }
let nodeAlias
let result
try {
  if (command === 'run') {
    // Dependency CLIs using /usr/bin/env node must select this same pinned Bun,
    // including nested package scripts. Never require an external Node binary.
    nodeAlias = mkdtempSync(join(tmpdir(), 'navide-bun-runtime-'))
    symlinkSync(process.execPath, join(nodeAlias, 'node'))
    env.PATH = `${nodeAlias}${delimiter}${env.PATH ?? ''}`
  }
  result = spawnSync(process.execPath, invocation, { stdio: 'inherit', env })
} finally {
  if (nodeAlias) rmSync(nodeAlias, { recursive: true })
}
if (result.error) throw result.error
if (result.signal) process.kill(process.pid, result.signal)
process.exit(result.status ?? 1)
