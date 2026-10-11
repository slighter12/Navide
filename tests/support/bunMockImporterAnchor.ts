// Bun compatibility setup: re-anchor relative Vitest mock ids on the caller file.
//
// Vitest resolves a relative `vi.mock('./x')` through the file it recovers from
// the calling frame (vitest's internal getImporter()). That lookup matches
// frames spelled `at Object.mock (...)` or `mock@...`. Bun's JSC frames are
// spelled `at mock (...)`, so the lookup misses, the importer comes back empty,
// and the mock is registered without a file anchor while the import of the same
// module resolves to its absolute id. The mock then never matches and the real
// module loads.
//
// Bun does print the calling file in the frame, so this setup recovers the
// importer from that frame and hands Vitest a pre-anchored id. Absolute and
// bare specifiers are passed through untouched. This file is inert outside Bun.
import { vi } from 'vitest'
import { statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// Extension probe order used by Vite's resolver (this project does not override
// resolve.extensions). Only consulted for an anchored id whose file name is not
// already spelled out, the same cases that need resolution for the import.

const isBun = Boolean((process.versions as Record<string, string | undefined>).bun)
// A query string can be suffixed to the setup module's own URL by the runner.
const ownFile = fileURLToPath(import.meta.url).split('?')[0].replace(/\\/g, '/')

function callerFile(): string | undefined {
  const stack = new Error().stack
  if (!stack) return undefined
  for (const line of stack.split('\n').slice(1)) {
    const match = /\(?((?:file:\/\/)?(?:[A-Za-z]:[\\/]|\/)[^\s()]+):\d+:\d+\)?$/.exec(line.trim())
    if (!match) continue
    const raw = match[1]
    const path = (raw.startsWith('file://') ? fileURLToPath(raw) : raw).replace(/\\/g, '/')
    if (path === ownFile || path.includes('/node_modules/')) continue
    return path
  }
  return undefined
}

const RESOLVE_EXTENSIONS = ['.mjs', '.js', '.mts', '.ts', '.jsx', '.tsx', '.json']

function isFile(path: string): boolean {
  try {
    return statSync(path).isFile()
  } catch {
    return false
  }
}

function resolveFrom(importer: string, specifier: string): string {
  const base = resolve(dirname(importer), specifier)
  if (isFile(base)) return base
  for (const extension of RESOLVE_EXTENSIONS) {
    if (isFile(base + extension)) return base + extension
  }
  for (const extension of RESOLVE_EXTENSIONS) {
    const index = join(base, `index${extension}`)
    if (isFile(index)) return index
  }
  return base
}

function anchorPath(path: string): string {
  if (!path.startsWith('./') && !path.startsWith('../')) return path
  const caller = callerFile()
  return caller ? resolveFrom(caller, path) : path
}

if (isBun) {
  const mockMethods = ['mock', 'unmock', 'doMock', 'doUnmock', 'importActual', 'importMock'] as const
  for (const name of mockMethods) {
    const original = vi[name] as (path: string, ...rest: unknown[]) => unknown
    ;(vi as unknown as Record<string, unknown>)[name] = function (path: string, ...rest: unknown[]) {
      // The dynamic-import mock form passes a promise instead of an id; those
      // ids are already absolute for Vite, so they are delegated untouched.
      if (typeof path !== 'string') return original.call(vi, path, ...rest)
      return original.call(vi, anchorPath(path), ...rest)
    }
  }
}
