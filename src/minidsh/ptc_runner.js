'use strict'

const readline = require('node:readline')
const repl = require('node:repl')
const util = require('node:util')
const { PassThrough } = require('node:stream')
const { stripTypeScriptTypes } = require('node:module')

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
const pending = new Map()
let nextCall = 1
let running = false
let persistentServer

process.env = {}

function send(message) {
  process.stdout.write(JSON.stringify(message) + '\n')
}

function materialize(value, seen = new Set(), top = false) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('non-finite result')
    return value
  }
  if (value === undefined && top) return null
  if (typeof value !== 'object') throw new Error(`unsupported result type: ${typeof value}`)
  if (seen.has(value)) throw new Error('cyclic result')
  seen.add(value)
  try {
    if (Array.isArray(value)) {
      const result = []
      for (let index = 0; index < value.length; index++) {
        if (!Object.hasOwn(value, index)) throw new Error('sparse result array')
        result.push(materialize(value[index], seen))
      }
      return result
    }
    if (Object.prototype.toString.call(value) !== '[object Object]') throw new Error('non-plain result object')
    const result = {}
    for (const key of Object.keys(value)) result[key] = materialize(value[key], seen)
    return result
  } finally {
    seen.delete(value)
  }
}

function makeBindings() {
  return new Proxy({}, {
    get(_target, name) {
      if (typeof name !== 'string') return undefined
      return args => {
        if (!running) return Promise.reject(new Error('execution already finished'))
        return new Promise((resolve, reject) => {
          const id = nextCall++
          pending.set(id, { resolve, reject })
          send({ type: 'call', id, name, args: args === undefined ? {} : args })
        })
      }
    },
  })
}

const bindings = makeBindings()
const console = {}
for (const method of ['log', 'info', 'warn', 'error', 'debug']) {
  console[method] = (...args) => {
    if (running) send({ type: 'log', method, text: args.map(arg => typeof arg === 'string' ? arg : util.inspect(arg, { depth: 8, maxArrayLength: 1000 })).join(' ') })
  }
}
Object.freeze(console)

function finish(status, value, diagnostic) {
  running = false
  for (const { reject } of pending.values()) reject(new Error('execution already finished'))
  pending.clear()
  send({ type: 'done', status, value, diagnostic })
}

function evaluate(request) {
  let code
  try {
    const source = request.mode === 'per-call' ? `(async function(tools, console) {\n${request.code}\n})` : request.code
    code = stripTypeScriptTypes(source, { mode: 'strip' })
  } catch (error) {
    finish('exception', null, error.message)
    return
  }
  running = true
  if (request.mode === 'per-call') {
    let result
    try {
      result = eval(code)(bindings, console)
    } catch (error) {
      finish('exception', null, error.message)
      return
    }
    Promise.resolve(result).then(
      value => {
        try { finish('completed', materialize(value, new Set(), true)) } catch (error) { finish('invalid-output', null, error.message) }
      },
      error => finish('exception', null, error && error.stack ? String(error.stack).slice(0, 4000) : String(error)),
    )
    return
  }
  if (!persistentServer) {
    persistentServer = repl.start({ prompt: '', terminal: false, input: new PassThrough(), output: new PassThrough() })
    persistentServer.context.tools = bindings
    persistentServer.context.console = console
  }
  persistentServer.eval(code, persistentServer.context, 'ptc.js', (error, value) => {
    if (error) {
      finish('exception', null, error && error.stack ? String(error.stack).slice(0, 4000) : String(error))
      return
    }
    try { finish('completed', materialize(value, new Set(), true)) } catch (failure) { finish('invalid-output', null, failure.message) }
  })
}

input.on('line', line => {
  let message
  try { message = JSON.parse(line) } catch { return }
  if (message.type === 'init') {
    if (running) {
      send({ type: 'done', status: 'protocol', diagnostic: 'an execution is already active' })
      return
    }
    if (!message.persistent) persistentServer = undefined
    evaluate(message)
    return
  }
  if (message.type === 'result') {
    const call = pending.get(message.id)
    if (!call) return
    pending.delete(message.id)
    if (message.error) {
      const error = new Error(message.error.message)
      error.toolName = message.error.toolName
      call.reject(error)
    } else {
      call.resolve(message.value)
    }
    return
  }
  if (message.type === 'revoke') {
    for (const { reject } of pending.values()) reject(new Error('execution revoked'))
    pending.clear()
  }
})

process.on('SIGTERM', () => process.exit(143))
process.on('SIGINT', () => process.exit(130))
