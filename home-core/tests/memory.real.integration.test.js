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

async function startSupervisor(runtime) {
  const supervisor = new CoreSupervisor({ app: { isPackaged: false, getPath: () => runtime }, port: await freePort() });
  const brainReady = waitForEvent(supervisor, 'isaac.brain.ready', () => true, 30000);
  const started = await supervisor.start();
  assert.strictEqual(started.ok, true);
  await brainReady;
  return supervisor;
}

async function sendAndComplete(supervisor, conversationId, content) {
  let requestId;
  const completed = waitForEvent(supervisor, 'isaac.response.completed', envelope => !requestId || envelope.payload.requestId === requestId);
  const result = await supervisor.client.command('conversation.message', { conversationId, content });
  requestId = result.requestId;
  return completed;
}

async function newConversation(supervisor) {
  return (await supervisor.client.command('conversation.create')).conversationId;
}

(async () => {
  delete process.env.ISAAC_BRAIN_ENABLED;
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-memory-real-'));
  const databasePath = path.join(runtime, 'isaac.db');
  process.env.ISAAC_MEMORY_DB = databasePath;
  let supervisor = await startSupervisor(runtime);
  try {
    let conversation = await newConversation(supervisor);
    const saved = await sendAndComplete(supervisor, conversation, 'Remember that my favorite test color is ultraviolet.');
    assert.match(saved.payload.content, /remember/i);

    conversation = await newConversation(supervisor);
    const recalled = await sendAndComplete(supervisor, conversation, 'What test color did I tell you to remember?');
    assert.match(recalled.payload.content, /ultraviolet/i);

    conversation = await newConversation(supervisor);
    await sendAndComplete(supervisor, conversation, "Remember that the right monitor is ISAAC's main visual presence.");
    await sendAndComplete(supervisor, conversation, 'Remember that the unrelated kitchen test item is a copper spoon.');
    const recallMetadata = waitForEvent(supervisor, 'isaac.memory.recalled');
    conversation = await newConversation(supervisor);
    const semantic = await sendAndComplete(supervisor, conversation, 'Which screen are you mainly supposed to live on?');
    const recallEvent = await recallMetadata;
    assert.match(semantic.payload.content, /right monitor/i);
    assert(recallEvent.payload.count < 4, 'Recall should use a relevant subset, not every memory');

    const corePid = supervisor.child.pid;
    const restartedBrain = waitForEvent(supervisor, 'isaac.brain.ready', () => true, 30000);
    supervisor.child.kill();
    await restartedBrain;
    assert(supervisor.child && supervisor.child.pid !== corePid);
    conversation = await newConversation(supervisor);
    const afterCoreRestart = await sendAndComplete(supervisor, conversation, 'What test color did I tell you to remember?');
    assert.match(afterCoreRestart.payload.content, /ultraviolet/i);

    await supervisor.stop();
    supervisor = await startSupervisor(runtime);
    conversation = await newConversation(supervisor);
    const afterHomeRestart = await sendAndComplete(supervisor, conversation, 'What test color did I tell you to remember?');
    assert.match(afterHomeRestart.payload.content, /ultraviolet/i);

    conversation = await newConversation(supervisor);
    await sendAndComplete(supervisor, conversation, 'Remember that the unique forget test word is Marigold.');
    const forgotten = await sendAndComplete(supervisor, conversation, 'Forget what I told you about the unique forget test word.');
    assert.match(forgotten.payload.content, /forgotten/i);
    const marigoldSearch = await supervisor.client.command('memory.search', { query: 'unique forget test word Marigold' });
    assert.strictEqual(marigoldSearch.memories.some(memory => /Marigold/i.test(memory.content)), false);

    conversation = await newConversation(supervisor);
    await sendAndComplete(supervisor, conversation, 'Remember that my test editor is AlphaEdit.');
    await sendAndComplete(supervisor, conversation, 'My test editor is now BetaEdit.');
    const editor = await sendAndComplete(supervisor, conversation, 'What test editor do I use?');
    assert.match(editor.payload.content, /BetaEdit/i);
    assert.doesNotMatch(editor.payload.content, /AlphaEdit/i);

    conversation = await newConversation(supervisor);
    await sendAndComplete(supervisor, conversation, 'My temporary session animal is an otter.');
    const temporary = await sendAndComplete(supervisor, conversation, 'What is my temporary session animal?');
    assert.match(temporary.payload.content, /otter/i);

    conversation = await newConversation(supervisor);
    const secret = await sendAndComplete(supervisor, conversation, 'Remember that my password is fake-password-123.');
    assert.match(secret.payload.content, /won't store|secret/i);
    const secretSearch = await supervisor.client.command('memory.search', { query: 'fake-password-123' });
    assert.strictEqual(secretSearch.memories.some(memory => /fake-password-123|my password/i.test(memory.content)), false);

    const active = await supervisor.client.command('memory.list', { status: 'active', limit: 100 });
    assert(active.memories.some(memory => /ultraviolet/i.test(memory.content)));
    assert(active.memories.some(memory => /BetaEdit/i.test(memory.content)));
    assert(!active.memories.some(memory => /AlphaEdit/i.test(memory.content)));

    await supervisor.stop();
    process.env.ISAAC_MEMORY_DB = runtime;
    supervisor = await startSupervisor(runtime);
    const memoryStatus = await supervisor.client.command('memory.status');
    assert.strictEqual(memoryStatus.state, 'unavailable');
    conversation = await newConversation(supervisor);
    const failureFallback = await sendAndComplete(supervisor, conversation, 'Say hello in one short sentence.');
    assert(failureFallback.payload.content.length > 0);

    process.stdout.write(JSON.stringify({
      result: 'PASS',
      databasePath,
      explicitRecall: recalled.payload.content,
      semanticRecall: semantic.payload.content,
      coreRestartRecall: afterCoreRestart.payload.content,
      homeRestartRecall: afterHomeRestart.payload.content,
      updateRecall: editor.payload.content,
      sessionContext: temporary.payload.content,
      activeMemoryCount: active.memories.length,
      databaseFailureConversation: failureFallback.payload.content
    }, null, 2) + '\n');
  } finally {
    await supervisor.stop();
    delete process.env.ISAAC_MEMORY_DB;
  }
})().catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
