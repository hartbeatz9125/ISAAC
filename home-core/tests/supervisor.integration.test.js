'use strict';

const assert = require('assert');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { once } = require('events');
const { CoreSupervisor } = require('../core-supervisor');

process.env.ISAAC_BRAIN_ENABLED = '0';

function timeout(promise, milliseconds, label) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`${label} timed out`)), milliseconds); })
  ]).finally(() => clearTimeout(timer));
}

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

(async () => {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-home-test-'));
  process.env.ISAAC_MEMORY_DB = path.join(runtime, 'isaac.db');
  const fakeApp = { isPackaged: false, getPath: () => runtime };
  const supervisor = new CoreSupervisor({ app: fakeApp, port: await freePort() });
  try {
    const firstReady = timeout(once(supervisor, 'isaac.ready'), 15000, 'initial ready event');
    const started = await supervisor.start();
    assert.deepStrictEqual(started, { ok: true, managed: true });
    const [readyEnvelope] = await firstReady;
    assert.strictEqual(readyEnvelope.version, 1);
    const firstPid = supervisor.child.pid;

    const restartedReady = timeout(once(supervisor, 'isaac.ready'), 20000, 'restarted ready event');
    supervisor.child.kill();
    await restartedReady;
    assert(supervisor.child && supervisor.child.pid !== firstPid, 'Core should restart with a new PID');
    await supervisor.stop();
    assert.strictEqual(supervisor.child, null);
    process.stdout.write('Home supervisor lifecycle/restart integration: PASS\n');
  } finally {
    await supervisor.stop();
    delete process.env.ISAAC_MEMORY_DB;
  }
})().catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
