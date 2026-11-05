#!/usr/bin/env node
// Fail packaging when the CLI runtime sidecar is not staged for the target platform and architecture.
//
// electron-builder copies whatever `extraResources` points at and silently skips a missing file, so a
// clean checkout that never ran `build:cli-runtime`, or a binary left over from a build for another
// architecture, would ship an app whose terminal runtime is missing or cannot start. The executable
// header says what the linker actually targeted, so this reads that.
//
// Two entry points:
//   - electron-builder `afterPack` hook (default export): checks the sidecar inside the packaged app
//     for the platform and architecture being packed.
//   - CLI, `node scripts/check-cli-runtime-staged.mjs [--platform darwin|linux|win32] [--arch arm64|x64]`:
//     checks `build/cli-runtime/` for the host (or the named target). `pnpm build` runs it.

import { closeSync, existsSync, openSync, readSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')

const BINARY = { darwin: 'navide-cli-runtime', linux: 'navide-cli-runtime', win32: 'navide-cli-runtime.exe' }
const PE_MACHINE = { arm64: 0xaa64, x64: 0x8664 }
const ELF_MACHINE = { arm64: 0xb7, x64: 0x3e }
const MACHO_CPU = { arm64: 0x0100000c, x64: 0x01000007 }
// electron-builder's `Arch` enum.
const BUILDER_ARCH = ['ia32', 'x64', 'armv7l', 'arm64', 'universal']

function readHead(file, length) {
  const fd = openSync(file, 'r')
  try {
    const buffer = Buffer.alloc(length)
    const read = readSync(fd, buffer, 0, length, 0)
    return buffer.subarray(0, read)
  } finally {
    closeSync(fd)
  }
}

/** The architectures an executable image is built for, by container format. Throws when the format is not the expected one. */
function architecturesOf(file, platform) {
  const head = readHead(file, 4096)
  if (platform === 'win32') {
    if (head.length < 0x40 || head.readUInt16LE(0) !== 0x5a4d) throw new Error('not a PE image (no MZ signature)')
    const peOffset = head.readUInt32LE(0x3c)
    const fd = openSync(file, 'r')
    try {
      const coff = Buffer.alloc(6)
      if (readSync(fd, coff, 0, coff.length, peOffset) !== coff.length || coff.readUInt32LE(0) !== 0x00004550) {
        throw new Error('not a PE image (no PE signature)')
      }
      return [coff.readUInt16LE(4)].map((machine) => Object.entries(PE_MACHINE).find(([, value]) => value === machine)?.[0] ?? `0x${machine.toString(16)}`)
    } finally {
      closeSync(fd)
    }
  }
  if (platform === 'linux') {
    if (head.length < 20 || head.readUInt32BE(0) !== 0x7f454c46) throw new Error('not an ELF image')
    const machine = head.readUInt16LE(18)
    return [Object.entries(ELF_MACHINE).find(([, value]) => value === machine)?.[0] ?? `0x${machine.toString(16)}`]
  }
  // darwin: thin 64-bit Mach-O (little-endian) or a fat container.
  if (head.length >= 8 && head.readUInt32LE(0) === 0xfeedfacf) {
    const cpu = head.readUInt32LE(4)
    return [Object.entries(MACHO_CPU).find(([, value]) => value === cpu)?.[0] ?? `0x${cpu.toString(16)}`]
  }
  if (head.length >= 8 && head.readUInt32BE(0) === 0xcafebabe) {
    const count = Math.min(head.readUInt32BE(4), 8)
    const found = []
    for (let i = 0; i < count; i++) {
      const cpu = head.readUInt32BE(8 + i * 20)
      found.push(Object.entries(MACHO_CPU).find(([, value]) => value === cpu)?.[0] ?? `0x${cpu.toString(16)}`)
    }
    return found
  }
  throw new Error('not a Mach-O image')
}

/**
 * Why the sidecar at `file` cannot ship for `platform`/`arch`, or `null` when it can.
 * `arch` is `arm64`, `x64` or `universal` (every thin slice present is accepted for a fat binary).
 */
export function stagedRuntimeProblem(file, platform, arch) {
  if (!existsSync(file)) return `${file} is missing; run \`pnpm run build:cli-runtime\``
  if (!statSync(file).isFile()) return `${file} is not a regular file`
  let found
  try {
    found = architecturesOf(file, platform)
  } catch (error) {
    return `${file}: ${error.message}`
  }
  const wanted = arch === 'universal' ? ['arm64', 'x64'] : [arch]
  const missing = wanted.filter((name) => !found.includes(name))
  if (missing.length === 0) return null
  // build-cli-runtime.mjs builds the host architecture unless told the package's (it needs the Rust target installed).
  const hint = arch === 'universal' ? '' : `; build that architecture with \`node scripts/build-cli-runtime.mjs --arch ${arch}\``
  return `${file}: built for ${found.join('+')}, expected ${wanted.join('+')}${hint}`
}

export function stagedRuntimePath(platform, root = ROOT) {
  return join(root, 'build', 'cli-runtime', BINARY[platform])
}

/** electron-builder `afterPack`: the packaged app must carry a sidecar built for the architecture being packed. */
export default function afterPack(context) {
  const platform = context.electronPlatformName
  const arch = BUILDER_ARCH[context.arch]
  if (!BINARY[platform]) throw new Error(`cli runtime: unsupported platform ${platform}`)
  if (!arch || arch === 'ia32' || arch === 'armv7l') throw new Error(`cli runtime: unsupported architecture ${arch ?? context.arch}`)
  const resources = platform === 'darwin'
    ? join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`, 'Contents', 'Resources')
    : join(context.appOutDir, 'resources')
  const problem = stagedRuntimeProblem(join(resources, 'bin', BINARY[platform]), platform, arch)
  if (problem) throw new Error(`cli runtime is not packaged correctly: ${problem}`)
}

export { afterPack }

function main(argv) {
  const options = new Map()
  for (let i = 0; i < argv.length; i += 2) options.set(argv[i], argv[i + 1])
  const platform = options.get('--platform') ?? process.platform
  const arch = options.get('--arch') ?? process.arch
  if (!BINARY[platform] || !['arm64', 'x64'].includes(arch)) {
    console.error('usage: node scripts/check-cli-runtime-staged.mjs [--platform darwin|linux|win32] [--arch arm64|x64]')
    return 2
  }
  const problem = stagedRuntimeProblem(stagedRuntimePath(platform), platform, arch)
  if (problem) {
    console.error(`[cli-runtime] ${problem}`)
    return 1
  }
  console.log(`[cli-runtime] ${stagedRuntimePath(platform)}: ${arch}`)
  return 0
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exit(main(process.argv.slice(2)))
