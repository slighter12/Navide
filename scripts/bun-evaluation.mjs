import { spawnSync } from 'node:child_process'

// Candidate-only ticket-02 entrypoint; this does not select or download a runtime.
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
    if (!['test:run', 'build:public-packages', 'build:mini-ide:v2', 'typecheck:web', 'typecheck:node'].includes(args[0])) {
      console.error('Ticket 02 permits test:run, build:public-packages, build:mini-ide:v2, typecheck:web, or typecheck:node only.')
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

const result = spawnSync(process.execPath, invocation, { stdio: 'inherit', env: process.env })
if (result.error) throw result.error
if (result.signal) process.kill(process.pid, result.signal)
process.exit(result.status ?? 1)
