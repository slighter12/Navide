import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import afterPack, { stagedRuntimeProblem } from '../../scripts/check-cli-runtime-staged.mjs'

const roots: string[] = []
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

function tmp(): string {
  const root = mkdtempSync(join(tmpdir(), 'cli-runtime-staged-'))
  roots.push(root)
  return root
}

// Just the executable headers the check reads.
function macho(cpu: number): Buffer {
  const header = Buffer.alloc(64)
  header.writeUInt32LE(0xfeedfacf, 0)
  header.writeUInt32LE(cpu, 4)
  return header
}
function fatMacho(...cpus: number[]): Buffer {
  const header = Buffer.alloc(8 + cpus.length * 20)
  header.writeUInt32BE(0xcafebabe, 0)
  header.writeUInt32BE(cpus.length, 4)
  cpus.forEach((cpu, i) => header.writeUInt32BE(cpu, 8 + i * 20))
  return header
}
function elf(machine: number): Buffer {
  const header = Buffer.alloc(64)
  header.writeUInt32BE(0x7f454c46, 0)
  header.writeUInt16LE(machine, 18)
  return header
}
function pe(machine: number): Buffer {
  const header = Buffer.alloc(0x80)
  header.writeUInt16LE(0x5a4d, 0)
  header.writeUInt32LE(0x40, 0x3c)
  header.writeUInt32LE(0x00004550, 0x40)
  header.writeUInt16LE(machine, 0x44)
  return header
}
const ARM64_MACHO = 0x0100000c
const X64_MACHO = 0x01000007

function stage(bytes: Buffer, name = 'navide-cli-runtime'): string {
  const file = join(tmp(), name)
  writeFileSync(file, bytes)
  return file
}

describe('stagedRuntimeProblem', () => {
  it('names a missing sidecar and how to build it', () => {
    expect(stagedRuntimeProblem(join(tmp(), 'navide-cli-runtime'), 'darwin', 'arm64')).toMatch(/is missing; run `pnpm run build:cli-runtime`/)
  })

  it.each([
    ['macOS arm64', macho(ARM64_MACHO), 'darwin', 'arm64'],
    ['macOS x64', macho(X64_MACHO), 'darwin', 'x64'],
    ['Linux arm64', elf(0xb7), 'linux', 'arm64'],
    ['Linux x64', elf(0x3e), 'linux', 'x64'],
    ['Windows arm64', pe(0xaa64), 'win32', 'arm64'],
    ['Windows x64', pe(0x8664), 'win32', 'x64'],
  ] as const)('accepts a %s binary built for its own architecture', (_label, bytes, platform, arch) => {
    expect(stagedRuntimeProblem(stage(bytes), platform, arch)).toBeNull()
  })

  it.each([
    ['macOS', macho(X64_MACHO), 'darwin', 'arm64'],
    ['Linux', elf(0x3e), 'linux', 'arm64'],
    ['Windows', pe(0x8664), 'win32', 'arm64'],
  ] as const)('rejects a %s binary left over from another architecture', (_label, bytes, platform, arch) => {
    expect(stagedRuntimeProblem(stage(bytes), platform, arch)).toMatch(/built for x64, expected arm64/)
  })

  it('rejects an executable of another platform and a file that is no executable', () => {
    expect(stagedRuntimeProblem(stage(elf(0x3e)), 'darwin', 'x64')).toMatch(/not a Mach-O image/)
    expect(stagedRuntimeProblem(stage(macho(ARM64_MACHO)), 'win32', 'arm64')).toMatch(/not a PE image/)
    expect(stagedRuntimeProblem(stage(Buffer.from('#!/bin/sh\n')), 'linux', 'x64')).toMatch(/not an ELF image/)
  })

  it('needs both slices of a fat macOS binary for a universal build', () => {
    expect(stagedRuntimeProblem(stage(fatMacho(ARM64_MACHO, X64_MACHO)), 'darwin', 'universal')).toBeNull()
    expect(stagedRuntimeProblem(stage(fatMacho(ARM64_MACHO)), 'darwin', 'universal')).toMatch(/built for arm64, expected arm64\+x64/)
  })
})

describe('afterPack', () => {
  const ARCH = { x64: 1, arm64: 3 }

  function macApp(bytes: Buffer | null): { appOutDir: string; context: Parameters<typeof afterPack>[0] } {
    const appOutDir = tmp()
    const bin = join(appOutDir, 'Navide.app', 'Contents', 'Resources', 'bin')
    mkdirSync(bin, { recursive: true })
    if (bytes) writeFileSync(join(bin, 'navide-cli-runtime'), bytes)
    return { appOutDir, context: { electronPlatformName: 'darwin', arch: ARCH.arm64, appOutDir, packager: { appInfo: { productFilename: 'Navide' } } } }
  }

  it('lets an app that carries a sidecar for its architecture through', () => {
    expect(() => afterPack(macApp(macho(ARM64_MACHO)).context)).not.toThrow()
  })

  it('fails packaging when the app has no sidecar', () => {
    expect(() => afterPack(macApp(null).context)).toThrow(/cli runtime is not packaged correctly.*is missing/)
  })

  it('fails packaging when the app carries a sidecar for the other architecture', () => {
    expect(() => afterPack(macApp(macho(X64_MACHO)).context)).toThrow(/built for x64, expected arm64/)
  })

  it('looks in resources/bin for the non-macOS layouts', () => {
    const appOutDir = tmp()
    mkdirSync(join(appOutDir, 'resources', 'bin'), { recursive: true })
    writeFileSync(join(appOutDir, 'resources', 'bin', 'navide-cli-runtime.exe'), pe(0x8664))
    const context = { electronPlatformName: 'win32', arch: ARCH.x64, appOutDir, packager: { appInfo: { productFilename: 'Navide' } } }
    expect(() => afterPack(context)).not.toThrow()
    expect(() => afterPack({ ...context, arch: ARCH.arm64 })).toThrow(/built for x64, expected arm64/)
  })
})

describe('the command line', () => {
  it('refuses an unknown target', () => {
    const result = spawnSync(process.execPath, [resolve('scripts/check-cli-runtime-staged.mjs'), '--arch', 'mips'], { encoding: 'utf8' })
    expect(result.status).toBe(2)
    expect(result.stderr).toMatch(/usage:/)
  })
})
