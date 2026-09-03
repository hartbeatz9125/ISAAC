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
function waitForEvent(emitter, type, predicate = () => true, timeoutMs = 180000) {
  return timeout(new Promise(resolve => {
    const listener = event => {
      if (event.type === type && predicate(event)) {
        emitter.off('event', listener);
        resolve(event);
      }
    };
    emitter.on('event', listener);
  }), timeoutMs, type);
}
async function waitForBrain(supervisor) {
  for (let count = 0; count < 180; count += 1) {
    try { const status = await supervisor.client.command('brain.status'); if (status.state === 'ready') return status; } catch {}
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('Brain did not become ready');
}
async function measure(supervisor, conversationId, content) {
  let requestId;
  let firstVisibleAt;
  let streamed = '';
  const sentAt = performance.now();
  const listener = event => {
    if (event.conversationId !== conversationId || (requestId && event.payload?.requestId !== requestId)) return;
    if (event.type === 'isaac.response.delta') {
      if (!firstVisibleAt) firstVisibleAt = performance.now();
      streamed += event.payload.delta;
    }
  };
  supervisor.on('event', listener);
  const completedPromise = waitForEvent(supervisor, 'isaac.response.completed', event => event.conversationId === conversationId && (!requestId || event.payload.requestId === requestId));
  const sent = await supervisor.client.command('conversation.message', { conversationId, content }, 180000);
  requestId = sent.requestId;
  const completed = await completedPromise;
  supervisor.off('event', listener);
  const completedAt = performance.now();
  assert.strictEqual(streamed, completed.payload.content);
  return {
    prompt: content,
    response: completed.payload.content,
    firstVisibleMs: Math.round(firstVisibleAt - sentAt),
    totalMs: Math.round(completedAt - sentAt),
    pipeline: completed.payload.timings?.pipeline,
    grounding: completed.payload.timings?.grounding
  };
}

(async () => {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-live-streaming-'));
  process.env.ISAAC_MEMORY_DB = path.join(runtime, 'isaac.db');
  const supervisor = new CoreSupervisor({ app: { isPackaged: false, getPath: () => runtime }, port: await freePort() });
  const results = {};
  try {
    assert.strictEqual((await supervisor.start()).ok, true);
    results.brain = await waitForBrain(supervisor);

    const casual = await supervisor.client.command('conversation.create');
    results.multiSentence = await measure(supervisor, casual.conversationId, 'Give me three short, natural sentences about why responsive software feels better to use.');
    assert(results.multiSentence.firstVisibleMs < results.multiSentence.totalMs);

    const short = await supervisor.client.command('conversation.create');
    results.short = await measure(supervisor, short.conversationId, 'Reply exactly: Ready.');

    await supervisor.client.command('memory.create', { type: 'project', content: 'The isolated streaming test controller is an ESP32.', tags: ['streaming-test'] });
    const recall = await supervisor.client.command('conversation.create');
    results.memoryRecall = await measure(supervisor, recall.conversationId, 'What controller does the isolated streaming test use?');
    assert.match(results.memoryRecall.response, /ESP32/i);

    const physical = await supervisor.client.command('conversation.create');
    results.physicalRisk = await measure(supervisor, physical.conversationId, 'Did you physically visit my house last year?');
    assert.doesNotMatch(results.physicalRisk.response, /\b(?:yes|yeah|i visited|i came over)\b/i);

    const cancellation = await supervisor.client.command('conversation.create');
    let cancellationRequestId;
    let deltaCount = 0;
    const onDelta = event => {
      if (event.type === 'isaac.response.delta' && event.conversationId === cancellation.conversationId) deltaCount += 1;
    };
    supervisor.on('event', onDelta);
    const firstDelta = waitForEvent(supervisor, 'isaac.response.delta', event => event.conversationId === cancellation.conversationId);
    const cancelled = waitForEvent(supervisor, 'isaac.response.cancelled', event => event.conversationId === cancellation.conversationId && (!cancellationRequestId || event.payload.requestId === cancellationRequestId));
    cancellationRequestId = (await supervisor.client.command('conversation.message', {
      conversationId: cancellation.conversationId,
      content: 'Write a long multi-paragraph explanation of computer networking with at least twenty complete sentences.'
    })).requestId;
    await firstDelta;
    await supervisor.client.command('conversation.cancel', { conversationId: cancellation.conversationId });
    await cancelled;
    const countAtCancel = deltaCount;
    await new Promise(resolve => setTimeout(resolve, 750));
    supervisor.off('event', onDelta);
    assert.strictEqual(deltaCount, countAtCancel);
    results.cancellation = { partialApprovedDeltas: countAtCancel, staleDeltas: deltaCount - countAtCancel };

    process.stdout.write(JSON.stringify({ result: 'PASS', database: process.env.ISAAC_MEMORY_DB, results }, null, 2) + '\n');
  } finally {
    await supervisor.stop();
    delete process.env.ISAAC_MEMORY_DB;
  }
})().then(() => process.exit(0)).catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exit(1);
});
