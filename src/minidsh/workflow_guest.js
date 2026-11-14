'use strict'

const readline = require('node:readline')
const vm = require('node:vm')

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
const pending = new Map()
const keepAlive = setInterval(() => {}, 1000)
let nextCall = 1

input.on('close', () => clearInterval(keepAlive))

function send(message) {
  process.stdout.write(JSON.stringify(message) + '\n')
}

function materialize(value, seen = new Set(), topLevel = false) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new Error('non-finite numbers are not JSON results')
    return value
  }
  if (value === undefined && topLevel) return null
  if (typeof value !== 'object') throw new Error(`unsupported JSON result type: ${typeof value}`)
  if (seen.has(value)) throw new Error('cyclic values are not JSON results')
  seen.add(value)
  try {
    if (Array.isArray(value)) {
      const result = []
      for (let index = 0; index < value.length; index++) {
        if (!Object.hasOwn(value, index)) throw new Error('sparse arrays are not JSON results')
        result.push(materialize(value[index], seen))
      }
      return result
    }
    const prototype = Object.getPrototypeOf(value)
    const plainObject = prototype === null || (Object.prototype.toString.call(value) === '[object Object]'
      && prototype.constructor && prototype.constructor.name === 'Object')
    if (!plainObject) throw new Error('special object prototypes are not JSON results')
    const result = {}
    for (const key of Object.keys(value)) result[key] = materialize(value[key], seen)
    return result
  } finally {
    seen.delete(value)
  }
}

function fatal(message, code) {
  const error = new Error(message)
  error.code = code
  error.fatal = true
  return error
}

function ordinaryError(error) {
  if (error && error.fatal === true) throw error
  return null
}

function makeHooks() {
  const hostCall = (operation, payload) => new Promise((resolve, reject) => {
    const id = nextCall++
    pending.set(id, { resolve, reject })
    send({ type: operation, id, ...payload })
  })
  const agent = (prompt, opts = {}) => {
    if (typeof prompt !== 'string' || !prompt) return Promise.reject(fatal('agent prompt must be a non-empty string', 'INVALID_ARGUMENT'))
    if (opts === null || typeof opts !== 'object' || Array.isArray(opts)) return Promise.reject(fatal('agent opts must be an object', 'INVALID_ARGUMENT'))
    const allowed = new Set(['label', 'phase', 'schema', 'provider', 'model'])
    for (const key of Object.keys(opts)) if (!allowed.has(key)) return Promise.reject(fatal(`unknown agent option: ${key}`, 'UNSUPPORTED_OPTION'))
    for (const key of ['label', 'phase', 'provider', 'model']) {
      if (opts[key] !== undefined && typeof opts[key] !== 'string') return Promise.reject(fatal(`agent ${key} must be a string`, 'INVALID_ARGUMENT'))
    }
    let options
    try { options = materialize(opts) } catch (error) { return Promise.reject(fatal(error.message, 'INVALID_ARGUMENT')) }
    return hostCall('agent', { prompt, opts: options })
  }
  const parallel = thunks => {
    if (!Array.isArray(thunks) || thunks.length > 4096 || thunks.some(thunk => typeof thunk !== 'function')) {
      return Promise.reject(fatal('parallel expects at most 4096 zero-argument functions', 'INVALID_ARGUMENT'))
    }
    return Promise.all(thunks.map(thunk => Promise.resolve().then(() => thunk()).catch(ordinaryError)))
  }
  const pipeline = (items, ...stages) => {
    if (!Array.isArray(items) || items.length > 4096 || stages.length === 0 || stages.some(stage => typeof stage !== 'function')) {
      return Promise.reject(fatal('pipeline expects at most 4096 items and one or more stages', 'INVALID_ARGUMENT'))
    }
    return Promise.all(items.map(async (item, index) => {
      try {
        let previous = item
        for (const stage of stages) previous = await stage(previous, item, index)
        return previous
      } catch (error) {
        return ordinaryError(error)
      }
    }))
  }
  return {
    agent,
    parallel,
    pipeline,
    phase(title) {
      if (typeof title !== 'string') throw fatal('phase title must be a string', 'INVALID_ARGUMENT')
      send({ type: 'phase', title })
    },
    log(message) {
      if (typeof message !== 'string') throw fatal('log message must be a string', 'INVALID_ARGUMENT')
      send({ type: 'log', message })
    },
  }
}

async function execute(request) {
  const hooks = makeHooks()
  const context = vm.createContext({ args: request.args === undefined ? null : request.args, ...hooks }, {
    codeGeneration: { strings: false, wasm: false },
  })
  let script
  try {
    script = new vm.Script(`(async () => {\n${request.script}\n})()`, { filename: 'workflow.js' })
  } catch (error) {
    send({ type: 'done', stopReason: 'error', error: { code: 'SCRIPT_PARSE', message: error.message } })
    return
  }
  try {
    const promise = script.runInContext(context, { timeout: 5000 })
    const value = await promise
    send({ type: 'done', stopReason: 'completed', value: materialize(value, new Set(), true) })
  } catch (error) {
    send({ type: 'done', stopReason: 'error', error: {
      code: error && error.code ? String(error.code) : 'SCRIPT_ERROR',
      message: error && error.message ? String(error.message) : String(error),
    } })
  }
}

input.on('line', line => {
  let message
  try { message = JSON.parse(line) } catch { return }
  if (message.type === 'init') {
    execute(message).catch(error => send({ type: 'done', stopReason: 'error', error: { code: 'HOST_ERROR', message: error.message } }))
    return
  }
  if (message.type === 'result') {
    const call = pending.get(message.id)
    if (!call) return
    pending.delete(message.id)
    if (message.error) {
      const error = new Error(message.error.message)
      error.code = message.error.code
      error.fatal = message.error.fatal === true
      call.reject(error)
    } else {
      call.resolve(message.value)
    }
  }
})

process.on('SIGTERM', () => process.exit(143))
process.on('SIGINT', () => process.exit(130))
