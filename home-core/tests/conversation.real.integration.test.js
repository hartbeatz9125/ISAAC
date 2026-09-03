'use strict';

const assert = require('assert');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { CoreSupervisor } = require('../core-supervisor');

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

function waitForEvent(emitter, type, predicate = () => true, timeoutMs = 120000) {
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

async function sendAndComplete(supervisor, conversationId, content) {
  let requestId;
  const completed = waitForEvent(supervisor, 'isaac.response.completed', envelope => !requestId || envelope.payload.requestId === requestId);
  const result = await supervisor.client.command('conversation.message', { conversationId, content });
  requestId = result.requestId;
  return completed;
}

(async () => {
  delete process.env.ISAAC_BRAIN_ENABLED;
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-real-chat-'));
  process.env.ISAAC_MEMORY_DB = path.join(runtime, 'isaac.db');
  const fakeApp = { isPackaged: false, getPath: () => runtime };
  const supervisor = new CoreSupervisor({ app: fakeApp, port: await freePort() });
  const startedAt = Date.now();
  let streamedDeltaCount = 0;
  supervisor.on('event', envelope => {
    if (envelope.type === 'isaac.response.delta') streamedDeltaCount += 1;
  });
  try {
    const brainReady = waitForEvent(supervisor, 'isaac.brain.ready');
    const started = await supervisor.start();
    assert.strictEqual(started.ok, true);
    const ready = await brainReady;
    const loadSeconds = (Date.now() - startedAt) / 1000;
    assert.strictEqual(ready.payload.state, 'ready');

    const first = await supervisor.client.command('conversation.create');
    await sendAndComplete(supervisor, first.conversationId, 'My name is Kenneth. Remember that for this temporary conversation.');
    const contextReply = await sendAndComplete(supervisor, first.conversationId, 'What is my name? Answer in one short sentence.');
    assert.match(contextReply.payload.content, /Kenneth/i);

    const identity = await supervisor.client.command('conversation.create');
    const identityReply = await sendAndComplete(supervisor, identity.conversationId, 'Who are you? Explain your name naturally in two short sentences.');
    assert.match(identityReply.payload.content, /ISAAC/i);
    assert.match(identityReply.payload.content, /Intelligent System for Analysis, Assistance (?:&|and) Control/i);

    const fresh = await supervisor.client.command('conversation.create');
    const freshReply = await sendAndComplete(supervisor, fresh.conversationId, 'What personal name have I told you in this conversation? Answer briefly.');
    assert.doesNotMatch(freshReply.payload.content, /Kenneth/i);

    const cancellation = await supervisor.client.command('conversation.create');
    let cancellationRequestId;
    const cancelledEvent = waitForEvent(supervisor, 'isaac.response.cancelled', envelope => !cancellationRequestId || envelope.payload.requestId === cancellationRequestId);
    const cancelStarted = waitForEvent(supervisor, 'isaac.response.started');
    const sendResult = await supervisor.client.command('conversation.message', {
      conversationId: cancellation.conversationId,
      content: 'Write a very long, detailed essay of at least 2,000 words about the history of computing.'
    });
    cancellationRequestId = sendResult.requestId;
    await cancelStarted;
    await supervisor.client.command('conversation.cancel', { conversationId: cancellation.conversationId });
    await cancelledEvent;
    assert(streamedDeltaCount > 1, 'Expected multiple streamed response deltas');

    const firstPid = supervisor.child.pid;
    const restartedBrain = waitForEvent(supervisor, 'isaac.brain.ready', () => true, 30000);
    supervisor.child.kill();
    await restartedBrain;
    assert(supervisor.child && supervisor.child.pid !== firstPid, 'Core and Brain should reconnect after restart');

    process.stdout.write(JSON.stringify({
      result: 'PASS',
      modelLoadSeconds: ready.payload.loadSeconds || loadSeconds,
      contextReply: contextReply.payload.content,
      identityReply: identityReply.payload.content,
      freshConversationReply: freshReply.payload.content,
      streamedDeltaCount,
      timings: contextReply.payload.timings
    }, null, 2) + '\n');
  } finally {
    await supervisor.stop();
    delete process.env.ISAAC_MEMORY_DB;
  }
})().catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
