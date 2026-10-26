// G2 ticket 08: assemble the packaged `<resources>` layout exactly as package.json's per-OS extraResources say.
//
//   node tests/cli/packagedLayout.mjs --out DIR [--platform mac|win|linux] [--map from-prefix=dir ...]
//
// electron-builder is not run: this copies each `build.<os>.extraResources` entry whose source exists to `<DIR>/<to>`
// (a directory source with `filter` copies only the named files). Entries whose source is missing are listed, not
// invented, so a package.json that stages no CLI runtime yields a layout with none. `--map` points a `from` prefix
// at another directory (a hosted job builds the backend and the runtime in places the config does not name).

import { chmodSync, copyFileSync, cpSync, existsSync, mkdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const keys = { darwin: 'mac', win32: 'win', linux: 'linux' }

export function platformKey(platform = process.platform) {
  const key = keys[platform]
  if (!key) throw new Error(`no packaged layout for platform ${platform}`)
  return key
}

export function extraResourcesFor(key, packageJson) {
  const entries = packageJson.build?.[key]?.extraResources
  if (!Array.isArray(entries)) throw new Error(`package.json build.${key}.extraResources is missing`)
  return entries
}

export function assembleLayout({ out, key = platformKey(), maps = {}, repo = root }) {
  const packageJson = JSON.parse(readFileSync(join(repo, 'package.json'), 'utf8'))
  const staged = []
  const missing = []
  for (const entry of extraResourcesFor(key, packageJson)) {
    let from = entry.from
    for (const [prefix, dir] of Object.entries(maps)) {
      if (from === prefix || from.startsWith(`${prefix}/`)) from = join(dir, from.slice(prefix.length))
    }
    const source = resolve(repo, from)
    if (!existsSync(source)) {
      missing.push(entry.from)
      continue
    }
    const target = join(out, entry.to)
    if (statSync(source).isDirectory()) {
      mkdirSync(target, { recursive: true })
      if (entry.filter) {
        for (const name of entry.filter) {
          if (!existsSync(join(source, name))) {
            missing.push(`${entry.from}/${name}`)
            continue
          }
          copyFileSync(join(source, name), join(target, name))
          chmodSync(join(target, name), 0o755)
          staged.push(join(entry.to, name).replaceAll('\\', '/'))
        }
      } else {
        cpSync(source, target, { recursive: true, verbatimSymlinks: true })
        staged.push(entry.to)
      }
    } else {
      mkdirSync(dirname(target), { recursive: true })
      copyFileSync(source, target)
      chmodSync(target, 0o755)
      staged.push(entry.to)
    }
  }
  return { staged, missing }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2)
  let out = ''
  let key = platformKey()
  const maps = {}
  for (let index = 0; index < args.length; index++) {
    if (args[index] === '--out') out = resolve(args[++index])
    else if (args[index] === '--platform') key = args[++index]
    else if (args[index] === '--map') {
      const [prefix, dir] = args[++index].split('=')
      maps[prefix] = resolve(dir)
    } else throw new Error(`unknown argument ${args[index]}`)
  }
  if (!out) throw new Error('--out is required')
  const { staged, missing } = assembleLayout({ out, key, maps })
  console.log(`packaged layout for ${key} at ${out}`)
  for (const item of staged) console.log(`  staged  ${item}`)
  for (const item of missing) console.log(`  absent  ${item}`)
}
