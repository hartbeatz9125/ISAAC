'use strict';

const assert = require('assert');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { CoreSupervisor } = require('../core-supervisor');

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

(async () => {
  const previous = process.env.ISAAC_MODEL_PATH;
  process.env.ISAAC_MODEL_PATH = path.join(os.tmpdir(), 'isaac-model-does-not-exist.gguf');
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-brain-failure-'));
  process.env.ISAAC_MEMORY_DB = path.join(runtime, 'isaac.db');
  const supervisor = new CoreSupervisor({ app: { isPackaged: false, getPath: () => runtime }, port: await freePort() });
  try {
    const errorEvent = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('brain failure event timed out')), 15000);
      supervisor.on('event', envelope => {
        if (envelope.type === 'isaac.error' && envelope.payload?.code === 'brain_load_failed') {
          clearTimeout(timer);
          resolve(envelope);
        }
      });
    });
    const started = await supervisor.start();
    assert.strictEqual(started.ok, true);
    await errorEvent;
    const health = await supervisor.client.health();
    assert.strictEqual(health.status, 'healthy');
    assert.strictEqual(health.brain.state, 'error');
    process.stdout.write('Brain failure isolation: PASS\n');
  } finally {
    await supervisor.stop();
    if (previous === undefined) delete process.env.ISAAC_MODEL_PATH;
    else process.env.ISAAC_MODEL_PATH = previous;
    delete process.env.ISAAC_MEMORY_DB;
  }
})().catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
