// Run with Node 24+: node tools/test_opencode_transport.mjs
// Load the actual plugin and execute its factory/tools. Only the external
// OpenCode schema builder and Bun subprocess boundary are fixtures; this does
// not test SDK validation, a live provider, or Python CLI behavior.
import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const sdkFixture = `
const schema = new Proxy(function () { return schema }, {
  get() { return schema },
})
export function tool(specification) { return specification }
tool.schema = schema
`
const sdkFixtureUrl = `data:text/javascript,${encodeURIComponent(sdkFixture)}`
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    return specifier === '@opencode-ai/plugin'
      ? { url: sdkFixtureUrl, shortCircuit: true }
      : nextResolve(specifier, context)
  },
})
let PangeaPlugin
try {
  ;({ default: PangeaPlugin } = await import('../.opencode/plugins/pangea.ts'))
} finally {
  hooks.deregister()
}

test('PANGEA transport isolates Desktop workers and preserves native OpenCode', async t => {
  const savedTransport = process.env.PANGEA_WORKER_TRANSPORT
  const savedPython = process.env.PANGEA_PYTHON
  const savedBun = Object.getOwnPropertyDescriptor(globalThis, 'Bun')
  const worktree = fileURLToPath(new URL('../', import.meta.url))
  const calls = []
  const client = new Proxy({}, {
    get(_target, key) { assert.fail(`Unexpected OpenCode client access: ${String(key)}`) },
  })
  process.env.PANGEA_PYTHON = '/fixture/python'
  globalThis.Bun = {
    spawn(argv, options) {
      calls.push({ argv, options })
      return {
        stdout: new Response(JSON.stringify({ ok: true, result: { resumed: true } })).body,
        stderr: new Response('').body,
        exited: Promise.resolve(0),
      }
    },
  }

  try {
    await t.test('desktop-cli registers neither tools nor hooks and makes no host call', async () => {
      process.env.PANGEA_WORKER_TRANSPORT = 'desktop-cli'
      assert.deepEqual(await PangeaPlugin({ client, worktree }), {})
      assert.equal(calls.length, 0)
    })

    await t.test('unmarked native plugin exposes hooks and executes its CLI tool', async () => {
      delete process.env.PANGEA_WORKER_TRANSPORT
      const plugin = await PangeaPlugin({ client, worktree })
      assert.equal(typeof plugin.event, 'function')
      assert.equal(typeof plugin['chat.message'], 'function')
      for (const name of ['pangea_action_dispatch', 'pangea_source_index', 'pangea_source_read', 'pangea_source_search']) {
        assert.equal(typeof plugin.tool[name].execute, 'function', name)
      }
      await plugin.event({ event: { type: 'session.idle', properties: { sessionID: 'unbound' } } })
      await plugin['chat.message']({ sessionID: 'native-parent', model: { providerID: 'fixture', modelID: 'model' } })
      assert.deepEqual(JSON.parse(await plugin.tool.pangea_run_resume.execute({
        data_root: '/fixture/data', run_id: 'run-exact',
      })), { resumed: true })
      assert.deepEqual(calls, [{
        argv: ['/fixture/python', '-m', 'pangea_agent.cli.main', 'resume-run', '--data-root', '/fixture/data', '--run-id', 'run-exact'],
        options: { cwd: worktree, stdout: 'pipe', stderr: 'pipe' },
      }])

      await assert.rejects(
        plugin.tool.pangea_source_read.execute({ repo_id: 'repo-exact', path: 'file.c' }, { sessionID: 'unbound' }),
        error => {
          assert.match(error.message, /当前 OpenCode session 未绑定 PANGEA action/)
          assert.match(error.message, /不要重复重试或猜测绑定/)
          assert.match(error.message, /Desktop 外部 worker.*Python CLI/)
          return true
        },
      )
      assert.equal(calls.length, 1, 'An unbound native tool must not invoke CLI or guess a binding')
    })

    await t.test('only the exact desktop-cli marker suppresses the plugin', async () => {
      process.env.PANGEA_WORKER_TRANSPORT = 'desktop-cli-other'
      const plugin = await PangeaPlugin({ client, worktree })
      assert.equal(typeof plugin.tool.pangea_source_read.execute, 'function')
      assert.equal(typeof plugin.event, 'function')
      assert.equal(typeof plugin['chat.message'], 'function')
    })
  } finally {
    if (savedTransport === undefined) delete process.env.PANGEA_WORKER_TRANSPORT
    else process.env.PANGEA_WORKER_TRANSPORT = savedTransport
    if (savedPython === undefined) delete process.env.PANGEA_PYTHON
    else process.env.PANGEA_PYTHON = savedPython
    if (savedBun) Object.defineProperty(globalThis, 'Bun', savedBun)
    else delete globalThis.Bun
  }
})
