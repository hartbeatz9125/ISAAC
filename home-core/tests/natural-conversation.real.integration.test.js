'use strict';

const assert = require('assert');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { CoreSupervisor } = require('../core-supervisor');

const timeout = (promise, milliseconds, label) => Promise.race([
  promise,
  new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} timed out`)), milliseconds))
]);

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

function waitForEvent(emitter, type, predicate = () => true, timeoutMs = 300000) {
  return timeout(new Promise(resolve => {
    const listener = envelope => {
      if (envelope.type === type && predicate(envelope)) {
        emitter.off('event', listener);
        resolve(envelope);
      }
    };
    emitter.on('event', listener);
  }), timeoutMs, type);
}

async function waitForBrain(supervisor) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const status = await supervisor.client.command('brain.status');
      if (status.state === 'ready') return status;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('Brain did not become ready');
}

async function ask(supervisor, prompt) {
  process.stderr.write(`TEST_PROMPT ${prompt}\n`);
  const conversation = await supervisor.client.command('conversation.create');
  const started = performance.now();
  let requestId;
  const completed = waitForEvent(supervisor, 'isaac.response.completed', envelope => !requestId || envelope.payload.requestId === requestId);
  const sent = await supervisor.client.command('conversation.message', { conversationId: conversation.conversationId, content: prompt });
  requestId = sent.requestId;
  const envelope = await completed;
  return { prompt, response: envelope.payload.content, elapsedMs: Math.round(performance.now() - started), timings: envelope.payload.timings };
}

(async () => {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-natural-real-'));
  process.env.ISAAC_MEMORY_DB = path.join(runtime, 'isaac.db');
  const supervisor = new CoreSupervisor({ app: { isPackaged: false, getPath: () => runtime }, port: await freePort() });
  const results = {};
  try {
    assert.strictEqual((await supervisor.start()).ok, true);
    await waitForBrain(supervisor);

    const empty = await supervisor.client.command('memory.list');
    assert.deepStrictEqual(empty.memories, []);
    const created = await supervisor.client.command('memory.create', { type: 'explicit', content: "Kenneth's isolated reset test word is cobalt.", confidence: 1, importance: 1, tags: ['isolation-control'] }, 120000);
    const read = await supervisor.client.command('memory.get', { id: created.id });
    assert.match(read.content, /cobalt/i);
    await supervisor.client.command('memory.delete', { id: created.id });
    const deleted = await supervisor.client.command('memory.get', { id: created.id });
    assert.strictEqual(deleted.status, 'deleted');

    results.name = await ask(supervisor, "What's your name?");
    results.userIdentity = await ask(supervisor, 'Who am I?');
    results.sharedMemory = await ask(supervisor, 'Remember when you came over to my house?');
    results.visual = await ask(supervisor, 'What color shirt am I wearing?');
    results.casual = await ask(supervisor, "Hey, how's it going?");

    assert.match(results.name.response, /ISAAC/i);
    assert.match(results.userIdentity.response, /Kenneth/i);
    assert.doesNotMatch(results.sharedMemory.response, /\b(?:yeah|yes|yep|i remember|that was|when i came)\b/i);
    assert.match(results.visual.response, /(?:can't|cannot|don't know|no clue|no way).*(?:see|tell|know)|(?:can't|cannot) see/i);
    assert(results.casual.response.trim().length > 0);

    process.stdout.write(JSON.stringify({ result: 'PASS', database: process.env.ISAAC_MEMORY_DB, memoryCrud: { emptyInitially: true, created: created.id, read: read.content, deletedStatus: deleted.status }, results }, null, 2) + '\n');
  } finally {
    await supervisor.stop();
    delete process.env.ISAAC_MEMORY_DB;
  }
})().then(() => process.exit(0)).catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exit(1);
});
