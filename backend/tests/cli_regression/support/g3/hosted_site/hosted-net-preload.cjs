'use strict'
// Preload of the hosted Windows run (`NODE_OPTIONS=--require <this file>`, set by hosted_seams.hosted_environment on win32 only).
// The frozen TS attempt runner connects to `endpoint.countersSocket` (<root>/gc.sock) with `net.connect(path)`. CPython has no AF_UNIX on Windows, so the backend serves the same protocol on a named pipe
// (hosted_counters.serve_pipe). This hook maps a `gc.sock` path to that pipe name, with the same function as hosted_counters.pipe_path (sha256 of the path string, first 24 hex characters).
// Nothing else is touched: every other path and every non-win32 process passes through unchanged.
const net = require('node:net')
const { createHash } = require('node:crypto')

const PIPE_PREFIX = '\\\\.\\pipe\\acr-g1e-counters-'
const SOCKET_NAME = 'gc.sock'

const pipePath = (socketPath) => PIPE_PREFIX + createHash('sha256').update(socketPath, 'utf8').digest('hex').slice(0, 24)
const isCountersSocket = (value) => typeof value === 'string' && /(^|[\\/])gc\.sock$/.test(value) && value.endsWith(SOCKET_NAME)
const mapArgs = (args) => {
  if (isCountersSocket(args[0])) return [pipePath(args[0]), ...args.slice(1)]
  if (args[0] && typeof args[0] === 'object' && isCountersSocket(args[0].path)) return [{ ...args[0], path: pipePath(args[0].path) }, ...args.slice(1)]
  return args
}

// Installed marker (hosted_markers.py): `<ACR_G3_HOSTED_MARKERS>/node-preload-<pid>.marker`, written once the hook is in place, so a preload that never loaded is named in the rehearsal summary.
const writeMarker = (env, platform, pid, fs, path) => {
  const dir = env.ACR_G3_HOSTED_MARKERS
  if (platform !== 'win32' || !dir) return false
  try {
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, `node-preload-${pid}.marker`), JSON.stringify({ kind: 'node-preload', pid }), { flag: 'wx' })
    return true
  } catch {
    return false
  }
}

if (process.platform === 'win32' && !net.connect.__hostedCounters) {
  const original = net.connect
  const wrapped = function (...args) { return original.apply(this, mapArgs(args)) }
  wrapped.__hostedCounters = true
  net.connect = wrapped
  net.createConnection = wrapped
  writeMarker(process.env, process.platform, process.pid, require('node:fs'), require('node:path'))
}

module.exports = { pipePath, mapArgs, isCountersSocket, writeMarker }
