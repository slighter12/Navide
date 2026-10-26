// Build the CLI runtime sidecar: build/cli-runtime/navide-cli-runtime[.exe].
//
// electron-builder packages build/cli-runtime/ into <resources>/bin/ beside the frozen backend and the STT sidecar
// (see package.json extraResources); backend/agent_team_backend/cli_runtime.py runtime_path() looks only there when
// packaged. Unlike build-stt.mjs this never degrades: without cargo, or when the build breaks, it exits 1, so a
// release can never ship an app whose terminal runtime is missing. The binary is the release-shaped one: no
// `fault-hooks` feature (tests/cli/g2Native.mjs proves the literal is absent).
//
// NAVIDE_CLI_RUNTIME_CARGO_TARGET_DIR, when set, is the cargo target directory (hosted jobs keep it out of the tree).

import { spawnSync } from 'node:child_process'
import { chmodSync, copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const crate = join(root, 'native', 'navide-cli-runtime')
const outDir = join(root, 'build', 'cli-runtime')
const binName = process.platform === 'win32' ? 'navide-cli-runtime.exe' : 'navide-cli-runtime'
const targetDir = process.env.NAVIDE_CLI_RUNTIME_CARGO_TARGET_DIR || join(crate, 'target')

function fail(message) {
  console.error(`[cli-runtime] ${message}`)
  process.exit(1)
}

const probe = spawnSync('cargo', ['--version'], { stdio: 'ignore' })
if (probe.error || probe.status !== 0) fail('cargo not found')

const build = spawnSync(
  'cargo',
  ['build', '--locked', '--release', '--manifest-path', join(crate, 'Cargo.toml'), '--bin', 'navide-cli-runtime'],
  { stdio: 'inherit', env: { ...process.env, CARGO_TARGET_DIR: targetDir } }
)
if (build.error || build.status !== 0) fail('cargo build failed')

const built = join(targetDir, 'release', binName)
if (!existsSync(built)) fail(`cargo did not produce ${built}`)
mkdirSync(outDir, { recursive: true })
const staged = join(outDir, binName)
copyFileSync(built, staged)
chmodSync(staged, 0o755)
console.log(`[cli-runtime] built ${staged}`)
