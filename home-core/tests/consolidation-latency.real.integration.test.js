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

function waitForEvent(supervisor, type, predicate = () => true, timeoutMs = 180000) {
  return timeout(new Promise(resolve => {
    const listener = event => {
      if (event.type === type && predicate(event)) {
        supervisor.off('event', listener);
        resolve(event);
      }
    };
    supervisor.on('event', listener);
  }), timeoutMs, type);
}

async function waitForBrain(supervisor) {
  for (let count = 0; count < 180; count += 1) {
    try {
      const status = await supervisor.client.command('brain.status');
      if (status.state === 'ready') return status;
    } catch {}
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('Brain did not become ready');
}

async function ask(supervisor, conversationId, content) {
  let requestId;
  const receivedAt = performance.now();
  const completed = waitForEvent(supervisor, 'isaac.response.completed', event => !requestId || event.payload.requestId === requestId);
  const result = await supervisor.client.command('conversation.message', { conversationId, content }, 180000);
  requestId = result.requestId;
  const acceptedAt = performance.now();
  const event = await completed;
  const completedAt = performance.now();
  return {
    content,
    acceptedMs: Math.round(acceptedAt - receivedAt),
    totalMs: Math.round(completedAt - receivedAt),
    response: event.payload.content,
    timings: event.payload.timings || {}
  };
}

async function waitForMemories(supervisor, minimum, timeoutMs = 180000) {
  const started = performance.now();
  while (performance.now() - started < timeoutMs) {
    const memories = (await supervisor.client.command('memory.list')).memories;
    if (memories.length >= minimum) return { memories, elapsedMs: Math.round(performance.now() - started) };
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out waiting for ${minimum} consolidated memories`);
}

(async () => {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-consolidation-latency-'));
  process.env.ISAAC_MEMORY_DB = path.join(runtime, 'isaac.db');
  const supervisor = new CoreSupervisor({ app: { isPackaged: false, getPath: () => runtime }, port: await freePort() });
  const results = {};
  try {
    assert.strictEqual((await supervisor.start()).ok, true);
    results.brain = await waitForBrain(supervisor);
    const conversation = await supervisor.client.command('conversation.create');

    results.meaningful = await ask(supervisor, conversation.conversationId, 'Kenneth decided the response-speed project will use an ordered background consolidation queue.');
    process.stdout.write(`MEANINGFUL ${JSON.stringify(results.meaningful)}\n`);

    results.immediateTrivialFollowUp = await ask(supervisor, conversation.conversationId, 'Okay, reply with one short sentence.');
    process.stdout.write(`IMMEDIATE_TRIVIAL ${JSON.stringify(results.immediateTrivialFollowUp)}\n`);

    results.meaningfulFollowUp = await ask(supervisor, conversation.conversationId, 'We also decided foreground conversation must always have priority over consolidation.');
    process.stdout.write(`MEANINGFUL_FOLLOWUP ${JSON.stringify(results.meaningfulFollowUp)}\n`);

    results.backgroundCommit = await waitForMemories(supervisor, 2);
    process.stdout.write(`BACKGROUND_COMMIT ${JSON.stringify({ elapsedMs: results.backgroundCommit.elapsedMs, count: results.backgroundCommit.memories.length })}\n`);

    const recallConversation = await supervisor.client.command('conversation.create');
    results.recall = await ask(supervisor, recallConversation.conversationId, 'What did we decide about the response-speed project?');
    process.stdout.write(`RECALL ${JSON.stringify(results.recall)}\n`);

    const preemptionConversation = await supervisor.client.command('conversation.create');
    results.preemptionSetup = await ask(supervisor, preemptionConversation.conversationId, 'We decided the response-speed project will keep shutdown flushing bounded.');
    await new Promise(resolve => setTimeout(resolve, 1000));
    results.duringActiveConsolidation = await ask(supervisor, preemptionConversation.conversationId, 'Reply with exactly: Foreground wins.');
    process.stdout.write(`ACTIVE_CONSOLIDATION_FOLLOWUP ${JSON.stringify(results.duringActiveConsolidation)}\n`);
    assert(results.duringActiveConsolidation.acceptedMs < 1000, `foreground acceptance was ${results.duringActiveConsolidation.acceptedMs}ms`);

    results.memories = (await supervisor.client.command('memory.list')).memories;
    process.stdout.write(`${JSON.stringify({ result: 'PASS', database: process.env.ISAAC_MEMORY_DB, results }, null, 2)}\n`);
  } finally {
    await supervisor.stop();
    delete process.env.ISAAC_MEMORY_DB;
  }
})().then(() => process.exit(0)).catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exit(1);
});
