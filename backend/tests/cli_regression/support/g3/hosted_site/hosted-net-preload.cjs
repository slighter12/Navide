'use strict'
// Preload of the hosted runs (`NODE_OPTIONS=--require <this file>`): the counters pipe mapping on win32 (hosted_seams.hosted_environment), and on Linux the WebSocket deferral below (hosted_seams.node_client_environment), each inert unless its platform or flag says so.
// The Windows part:
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

// ---- Linux: defer the real WebSocket until its port accepts (rehearsal fix round 5), only with ACR_G3_HOSTED_WS_DEFER=1.
// The backend writes `backend-port` and the token BEFORE uvicorn listens and the frozen runners connect the moment both files exist. Node 22's undici (6.x) fires `error` and never `close` for a handshake that fails
// before the TCP connection exists (websocket/util.js failWebsocketConnection; `onSocketClose` is registered only after a 101), so `wsClient`'s close-driven reconnect never runs and the socket stays CONNECTING.
// This facade probes host:port with plain TCP connects (every `pollMs`, at most `boundMs`) and only then creates the real WebSocket, with the original arguments; after the bound it creates it as-is. Once the real socket
// exists every call goes straight to it. Listeners, `binaryType` and `on*` handlers set meanwhile are handed over on creation.
const DEFER_FLAG = 'ACR_G3_HOSTED_WS_DEFER'
const STATS_KEY = Symbol.for('acr.g3h.wsDefer')
const DEFAULT_PORTS = { 'ws:': 80, 'wss:': 443, 'http:': 80, 'https:': 443 }
const HANDLERS = ['onopen', 'onmessage', 'onerror', 'onclose']

const deferTarget = (url) => {
  try {
    const parsed = new URL(String(url))
    const host = parsed.hostname.replace(/^\[|\]$/g, '')
    const port = Number(parsed.port || DEFAULT_PORTS[parsed.protocol])
    return host && Number.isInteger(port) && port > 0 ? { host, port } : null
  } catch {
    return null
  }
}

const probe = (net, target) => new Promise((resolve) => {
  let socket
  const finish = (accepted) => {
    resolve(accepted)
    try { socket.destroy() } catch { /* already gone */ }
  }
  try {
    socket = net.connect({ host: target.host, port: target.port })
  } catch {
    resolve(false)
    return
  }
  socket.setTimeout(2000)
  socket.once('connect', () => finish(true))
  socket.once('error', () => finish(false))
  socket.once('timeout', () => finish(false))
})

// `<ACR_G3_HOSTED_MARKERS>/ws-defer-<pid>.marker`, once per process, the first time a probe was refused: the preload was loaded AND had to wait. `step` is the ACR_G3 selector of the step.
const writeDeferMarker = (env, pid, fs, path) => {
  const dir = env.ACR_G3_HOSTED_MARKERS
  if (!dir) return false
  try {
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, `ws-defer-${pid}.marker`), JSON.stringify({ kind: 'ws-defer', pid, role: 'node-ws-defer', step: env.ACR_G3 || 'unknown' }), { flag: 'wx' })
    return true
  } catch {
    return false
  }
}

const makeDeferred = (Real, context) => {
  const { net, stats, pollMs, boundMs, onRefused } = context
  class DeferredWebSocket {
    #args
    #target
    #real = null
    #listeners = []
    #binaryType = 'blob'
    #handlers = {}
    #cancelled = false
    #timer = null
    #started = Date.now()

    constructor(...args) {
      const target = deferTarget(args[0])
      if (!target) return new Real(...args)      // the runtime reports what is wrong with the URL itself
      this.#args = args
      this.#target = target
      stats.deferred++
      this.#attempt()
    }

    #attempt() {
      probe(net, this.#target).then((accepted) => {
        if (this.#cancelled) return
        if (accepted) return this.#create()
        stats.refused++
        onRefused()
        if (Date.now() - this.#started >= boundMs) {
          stats.bounded++
          return this.#create()
        }
        this.#timer = setTimeout(() => this.#attempt(), pollMs)
      })
    }

    #fire(type, extra) {
      const event = Object.assign(new Event(type), extra)
      for (const [name, listener] of this.#listeners) {
        if (name !== type) continue
        try { typeof listener === 'function' ? listener.call(this, event) : listener.handleEvent(event) } catch { /* a listener's own failure */ }
      }
    }

    #create() {
      try {
        this.#real = new Real(...this.#args)
      } catch {
        this.#cancelled = true
        this.#fire('error', {})
        this.#fire('close', { code: 1006, reason: '', wasClean: false })
        return
      }
      this.#real.binaryType = this.#binaryType
      for (const [type, listener, options] of this.#listeners) this.#real.addEventListener(type, listener, options)
      for (const name of HANDLERS) if (this.#handlers[name]) this.#real[name] = this.#handlers[name]
    }

    get readyState() { return this.#real ? this.#real.readyState : (this.#cancelled ? 3 : 0) }
    get url() { return this.#real ? this.#real.url : String(this.#args[0]) }
    get protocol() { return this.#real ? this.#real.protocol : '' }
    get extensions() { return this.#real ? this.#real.extensions : '' }
    get bufferedAmount() { return this.#real ? this.#real.bufferedAmount : 0 }
    get binaryType() { return this.#real ? this.#real.binaryType : this.#binaryType }
    set binaryType(value) { this.#binaryType = value; if (this.#real) this.#real.binaryType = value }

    addEventListener(type, listener, options) {
      if (this.#real) return this.#real.addEventListener(type, listener, options)
      this.#listeners.push([type, listener, options])
    }

    removeEventListener(type, listener, options) {
      if (this.#real) return this.#real.removeEventListener(type, listener, options)
      this.#listeners = this.#listeners.filter(([t, l]) => !(t === type && l === listener))
    }

    dispatchEvent(event) { return this.#real ? this.#real.dispatchEvent(event) : false }

    send(...args) {
      if (this.#real) return this.#real.send(...args)
      throw new DOMException('WebSocket is not open: readyState 0 (CONNECTING)', 'InvalidStateError')
    }

    close(...args) {
      if (this.#real) return this.#real.close(...args)
      if (this.#cancelled) return
      this.#cancelled = true
      clearTimeout(this.#timer)
      this.#fire('close', { code: 1006, reason: '', wasClean: false })
    }

    #handler(name) { return this.#real ? this.#real[name] : this.#handlers[name] ?? null }
    #setHandler(name, value) { if (this.#real) this.#real[name] = value; else this.#handlers[name] = value }
    get onopen() { return this.#handler('onopen') }
    set onopen(value) { this.#setHandler('onopen', value) }
    get onmessage() { return this.#handler('onmessage') }
    set onmessage(value) { this.#setHandler('onmessage', value) }
    get onerror() { return this.#handler('onerror') }
    set onerror(value) { this.#setHandler('onerror', value) }
    get onclose() { return this.#handler('onclose') }
    set onclose(value) { this.#setHandler('onclose', value) }
  }
  for (const [name, value] of [['CONNECTING', 0], ['OPEN', 1], ['CLOSING', 2], ['CLOSED', 3]]) {
    Object.defineProperty(DeferredWebSocket, name, { value, enumerable: true })
    Object.defineProperty(DeferredWebSocket.prototype, name, { value, enumerable: true })
  }
  return DeferredWebSocket
}

const install = ({ globalObject, env, net, fs, path, pid, pollMs = 20, boundMs = 30000 }) => {
  if (env[DEFER_FLAG] !== '1' || globalObject[STATS_KEY]) return false
  const descriptor = Object.getOwnPropertyDescriptor(globalObject, 'WebSocket')
  if (!descriptor) return false
  const stats = { loaded: true, deferred: 0, refused: 0, bounded: 0 }
  Object.defineProperty(globalObject, STATS_KEY, { value: stats, configurable: true })
  let marked = false
  const onRefused = () => { if (!marked) { marked = true; writeDeferMarker(env, pid, fs, path) } }
  let facade
  Object.defineProperty(globalObject, 'WebSocket', {
    configurable: true,
    enumerable: descriptor.enumerable,
    get() {
      if (!facade) {
        const Real = descriptor.get ? descriptor.get.call(globalObject) : descriptor.value
        facade = typeof Real === 'function' ? makeDeferred(Real, { net, stats, pollMs, boundMs, onRefused }) : Real
      }
      return facade
    },
    set(value) { Object.defineProperty(globalObject, 'WebSocket', { value, writable: true, configurable: true, enumerable: descriptor.enumerable }) },
  })
  return true
}

install({ globalObject: globalThis, env: process.env, net, fs: require('node:fs'), path: require('node:path'), pid: process.pid })

module.exports = { pipePath, mapArgs, isCountersSocket, writeMarker, install, deferTarget, writeDeferMarker, DEFER_FLAG, STATS_KEY }
