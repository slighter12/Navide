// Build the CLI runtime sidecar: build/cli-runtime/navide-cli-runtime[.exe].
//
// electron-builder packages build/cli-runtime/ into <resources>/bin/ beside the frozen backend and the STT sidecar
// (see package.json extraResources); backend/agent_team_backend/cli_runtime.py runtime_path() looks only there when
// packaged. Unlike build-stt.mjs this never degrades: without cargo, or when the build breaks, it exits 1, so a
// release can never ship an app whose terminal runtime is missing. The binary is the release-shaped one: no
// `fault-hooks` feature (tests/cli/g2Native.mjs proves the literal is absent).
//
// Target: the host's platform and architecture. A package for the other architecture of the same platform
// (`--arch arm64|x64`, or NAVIDE_CLI_RUNTIME_ARCH) is built with `cargo build --target <triple>`; when that Rust
// target is not installed the script fails here, before packaging, with the `rustup target add` to run. Another
// platform cannot be built from this host.
//
// NAVIDE_CLI_RUNTIME_CARGO_TARGET_DIR, when set, is the cargo target directory (hosted jobs keep it out of the tree).

import { spawnSync } from 'node:child_process'
import { chmodSync, copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const crate = join(root, 'native', 'navide-cli-runtime')
const outDir = join(root, 'build', 'cli-runtime')
const targetDir = process.env.NAVIDE_CLI_RUNTIME_CARGO_TARGET_DIR || join(crate, 'target')

const TRIPLE_ARCH = { arm64: 'aarch64', x64: 'x86_64' }
const TRIPLE_OS = { darwin: 'apple-darwin', linux: 'unknown-linux-gnu', win32: 'pc-windows-msvc' }

function fail(message) {
  console.error(`[cli-runtime] ${message}`)
  process.exit(1)
}

function option(name) {
  const at = process.argv.indexOf(name)
  return at >= 0 ? process.argv[at + 1] : undefined
}

const platform = option('--platform') ?? process.platform
const arch = option('--arch') ?? process.env.NAVIDE_CLI_RUNTIME_ARCH ?? process.arch
if (!TRIPLE_OS[platform]) fail(`unsupported platform ${platform}`)
if (!TRIPLE_ARCH[arch]) fail(`unsupported architecture ${arch} (expected arm64 or x64)`)
if (platform !== process.platform) fail(`a ${platform} runtime cannot be built on ${process.platform}; build it on a ${platform} host`)
const binName = platform === 'win32' ? 'navide-cli-runtime.exe' : 'navide-cli-runtime'

const probe = spawnSync('cargo', ['--version'], { stdio: 'ignore' })
if (probe.error || probe.status !== 0) {
  fail('cargo not found: install Rust with rustup (https://rustup.rs) and re-run; the CLI runtime sidecar is part of every package')
}

// The host architecture builds with cargo's default target; another one needs its own Rust target installed.
const triple = arch === process.arch ? null : `${TRIPLE_ARCH[arch]}-${TRIPLE_OS[platform]}`
if (triple) {
  // `rustc --print target-libdir` names the standard-library directory; it exists only for an installed target.
  const libdir = spawnSync('rustc', ['--print', 'target-libdir', '--target', triple], { encoding: 'utf8' })
  const installed = !libdir.error && libdir.status === 0 && existsSync(libdir.stdout.trim())
  if (!installed) fail(`the Rust target ${triple} is not installed, so a ${arch} runtime cannot be built; run: rustup target add ${triple}`)
}

const build = spawnSync(
  'cargo',
  [
    'build', '--locked', '--release',
    ...(triple ? ['--target', triple] : []),
    '--manifest-path', join(crate, 'Cargo.toml'), '--bin', 'navide-cli-runtime',
  ],
  { stdio: 'inherit', env: { ...process.env, CARGO_TARGET_DIR: targetDir } }
)
if (build.error || build.status !== 0) fail('cargo build failed')

const built = join(targetDir, ...(triple ? [triple] : []), 'release', binName)
if (!existsSync(built)) fail(`cargo did not produce ${built}`)
mkdirSync(outDir, { recursive: true })
const staged = join(outDir, binName)
copyFileSync(built, staged)
chmodSync(staged, 0o755)
console.log(`[cli-runtime] built ${staged}`)
