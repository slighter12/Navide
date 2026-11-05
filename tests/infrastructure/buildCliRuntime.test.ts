import { spawnSync } from 'node:child_process'
import { chmodSync, copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

// scripts/build-cli-runtime.mjs run for real, copied into a scratch tree (its root is its own
// location, so nothing under the repository is written) with fake `cargo` and `rustc` programs on
// PATH that log what they were asked and write the binary a build would.
const SCRIPT = resolve(__dirname, '../../scripts/build-cli-runtime.mjs')
const posix = process.platform !== 'win32'
const hostArch = process.arch === 'arm64' ? 'arm64' : 'x64'
const otherArch = hostArch === 'arm64' ? 'x64' : 'arm64'
const TRIPLE_ARCH = { arm64: 'aarch64', x64: 'x86_64' } as const
const TRIPLE_OS = { darwin: 'apple-darwin', linux: 'unknown-linux-gnu', win32: 'pc-windows-msvc' } as const
const tripleFor = (arch: 'arm64' | 'x64'): string =>
  `${TRIPLE_ARCH[arch]}-${TRIPLE_OS[process.platform as keyof typeof TRIPLE_OS]}`

const FAKE_CARGO = `#!/bin/sh
echo "$@" >> "$FAKE_LOG"
[ "$1" = "--version" ] && { echo "cargo 1.0.0"; exit 0; }
target=""
previous=""
for argument in "$@"; do
  [ "$previous" = "--target" ] && target="$argument"
  previous="$argument"
done
dir="$CARGO_TARGET_DIR"
[ -n "$target" ] && dir="$dir/$target"
mkdir -p "$dir/release"
printf 'binary' > "$dir/release/navide-cli-runtime"
`

// rustc --print target-libdir --target <triple>: the directory exists only for an installed target.
const FAKE_RUSTC = `#!/bin/sh
previous=""
for argument in "$@"; do
  [ "$previous" = "--target" ] && { echo "$FAKE_SYSROOT/$argument"; exit 0; }
  previous="$argument"
done
echo "$FAKE_SYSROOT/host"
`

const scratch: string[] = []
afterEach(() => {
  for (const dir of scratch.splice(0)) rmSync(dir, { recursive: true, force: true })
})

function setup(opts: { withCargo?: boolean, installedTargets?: string[] } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'build-cli-runtime-'))
  scratch.push(root)
  mkdirSync(join(root, 'scripts'))
  copyFileSync(SCRIPT, join(root, 'scripts', 'build-cli-runtime.mjs'))
  mkdirSync(join(root, 'native', 'navide-cli-runtime'), { recursive: true })
  writeFileSync(join(root, 'native', 'navide-cli-runtime', 'Cargo.toml'), '')
  const bin = join(root, 'bin')
  mkdirSync(bin)
  const sysroot = join(root, 'sysroot')
  mkdirSync(sysroot)
  for (const triple of opts.installedTargets ?? []) mkdirSync(join(sysroot, triple))
  if (opts.withCargo !== false) {
    writeFileSync(join(bin, 'cargo'), FAKE_CARGO)
    writeFileSync(join(bin, 'rustc'), FAKE_RUSTC)
    chmodSync(join(bin, 'cargo'), 0o755)
    chmodSync(join(bin, 'rustc'), 0o755)
  }
  const log = join(root, 'cargo.log')
  writeFileSync(log, '')
  const run = (args: string[] = [], env: Record<string, string> = {}) => spawnSync(process.execPath, [join(root, 'scripts', 'build-cli-runtime.mjs'), ...args], {
    encoding: 'utf8',
    env: {
      PATH: `${bin}:/usr/bin:/bin`,
      FAKE_LOG: log,
      FAKE_SYSROOT: sysroot,
      NAVIDE_CLI_RUNTIME_CARGO_TARGET_DIR: join(root, 'target'),
      ...env,
    },
  })
  const staged = join(root, 'build', 'cli-runtime', 'navide-cli-runtime')
  const cargoCalls = (): string[] => readFileSync(log, 'utf8').split('\n').filter(Boolean)
  return { run, staged, cargoCalls }
}

describe.skipIf(!posix)('build-cli-runtime.mjs', () => {
  it('fails with how to install Rust when cargo is not on PATH', () => {
    const { run, staged } = setup({ withCargo: false })
    const result = run()
    expect(result.status).toBe(1)
    expect(result.stderr).toMatch(/cargo not found: install Rust with rustup/)
    expect(existsSync(staged)).toBe(false)
  })

  it('builds the host target when no other architecture is asked for', () => {
    const { run, staged, cargoCalls } = setup()
    const result = run()
    expect(result.status, result.stderr).toBe(0)
    const build = cargoCalls().find((call) => call.startsWith('build'))
    expect(build).toBeDefined()
    expect(build).not.toContain('--target')
    expect(existsSync(staged)).toBe(true)
  })

  it('builds the host target when the requested architecture is the host one', () => {
    const { run, staged, cargoCalls } = setup()
    expect(run(['--arch', hostArch]).status).toBe(0)
    expect(cargoCalls().find((call) => call.startsWith('build'))).not.toContain('--target')
    expect(existsSync(staged)).toBe(true)
  })

  it.each([
    ['--arch', (arch: string) => ['--arch', arch], {}],
    ['NAVIDE_CLI_RUNTIME_ARCH', () => [], undefined],
  ])('builds the other architecture with cargo --target when that target is installed (%s)', (_how, argsFor, envFor) => {
    const triple = tripleFor(otherArch)
    const { run, staged, cargoCalls } = setup({ installedTargets: [triple] })
    const result = envFor === undefined ? run([], { NAVIDE_CLI_RUNTIME_ARCH: otherArch }) : run(argsFor(otherArch))
    expect(result.status, result.stderr).toBe(0)
    const build = cargoCalls().find((call) => call.startsWith('build'))
    expect(build).toContain(`--target ${triple}`)
    expect(existsSync(staged)).toBe(true)
  })

  it('fails before building when the other architecture\'s target is not installed, and says how to add it', () => {
    const triple = tripleFor(otherArch)
    const { run, staged, cargoCalls } = setup({ installedTargets: [] })
    const result = run(['--arch', otherArch])
    expect(result.status).toBe(1)
    expect(result.stderr).toContain(`rustup target add ${triple}`)
    expect(cargoCalls().some((call) => call.startsWith('build'))).toBe(false)
    expect(existsSync(staged)).toBe(false)
  })

  it('refuses another platform and an unknown architecture before building', () => {
    const { run, cargoCalls } = setup({ installedTargets: [tripleFor(otherArch)] })
    const otherPlatform = process.platform === 'linux' ? 'win32' : 'linux'
    const crossOs = run(['--platform', otherPlatform])
    expect(crossOs.status).toBe(1)
    expect(crossOs.stderr).toMatch(/cannot be built on/)
    const unknown = run(['--arch', 'ia32'])
    expect(unknown.status).toBe(1)
    expect(unknown.stderr).toMatch(/unsupported architecture/)
    expect(cargoCalls().some((call) => call.startsWith('build'))).toBe(false)
  })
})
