'use strict';

const assert = require('assert');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { CoreSupervisor } = require('../core-supervisor');

const timeout = (promise, milliseconds, label) => Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error(`${label} timed out`)), milliseconds))]);
async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
function waitForEvent(emitter, type, predicate = () => true, timeoutMs = 300000) {
  return timeout(new Promise(resolve => {
    const listener = event => { if (event.type === type && predicate(event)) { emitter.off('event', listener); resolve(event); } };
    emitter.on('event', listener);
  }), timeoutMs, type);
}
async function waitForBrain(supervisor) {
  for (let count = 0; count < 120; count += 1) {
    try { const status = await supervisor.client.command('brain.status'); if (status.state === 'ready') return status; } catch {}
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('Brain did not become ready');
}
async function ask(supervisor, prompt, conversationId = null) {
  process.stderr.write(`TEST_PROMPT ${prompt}\n`);
  const conversation = conversationId ? { conversationId } : await supervisor.client.command('conversation.create');
  let requestId;
  const started = performance.now();
  const completed = waitForEvent(supervisor, 'isaac.response.completed', event => !requestId || event.payload.requestId === requestId);
  const sent = await supervisor.client.command('conversation.message', { conversationId: conversation.conversationId, content: prompt });
  requestId = sent.requestId;
  const event = await completed;
  return { conversationId: conversation.conversationId, prompt, response: event.payload.content, elapsedMs: Math.round(performance.now() - started) };
}

(async () => {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-continuity-real-'));
  process.env.ISAAC_MEMORY_DB = path.join(runtime, 'isaac.db');
  const supervisor = new CoreSupervisor({ app: { isPackaged: false, getPath: () => runtime }, port: await freePort() });
  const results = {};
  try {
    assert.strictEqual((await supervisor.start()).ok, true);
    await waitForBrain(supervisor);

    results.decision = await ask(supervisor, 'Kenneth is building a weather station project and decided to use an ESP32.');
    results.recallEsp32 = await ask(supervisor, 'What did we decide to use for the weather station?');
    assert.match(results.recallEsp32.response, /ESP32/i);

    results.correction = await ask(supervisor, "Actually we're using a Raspberry Pi instead of the ESP32 for the weather station project.");
    results.recallPi = await ask(supervisor, 'Which controller did we choose for the weather station?');
    assert.match(results.recallPi.response, /Raspberry Pi/i);
    assert.doesNotMatch(results.recallPi.response, /(?:controller|choice)\s+(?:is|remains)\s+(?:an?\s+)?ESP32/i);
    const correctedController = (await supervisor.client.command('memory.list')).memories.filter(memory => memory.tags.includes('subject:weather-station:controller'));
    assert.strictEqual(correctedController.length, 1);
    assert.match(correctedController[0].content, /Raspberry Pi/i);

    const beforeTrivial = (await supervisor.client.command('memory.list')).memories.length;
    const trivialConversation = (await supervisor.client.command('conversation.create')).conversationId;
    results.trivial = [];
    for (const prompt of ['lol', 'yo', "that's crazy"]) results.trivial.push(await ask(supervisor, prompt, trivialConversation));
    const afterTrivial = (await supervisor.client.command('memory.list')).memories.length;
    assert.strictEqual(afterTrivial, beforeTrivial);

    const decisionConversation = (await supervisor.client.command('conversation.create')).conversationId;
    results.projectDecisionOne = await ask(supervisor, 'We decided the weather station project enclosure must be waterproof.', decisionConversation);
    results.projectDecisionTwo = await ask(supervisor, 'We also decided the weather station project will upload readings every ten minutes.', decisionConversation);
    results.lastTime = await ask(supervisor, 'What did we decide last time about the weather station project?');
    assert.match(results.lastTime.response, /waterproof|ten minutes/i);

    results.forget = await ask(supervisor, 'Forget the weather station controller.');
    results.afterForget = await ask(supervisor, 'What controller did we choose for the weather station?');
    assert.doesNotMatch(results.afterForget.response, /Raspberry Pi|ESP32/i);

    const finalMemories = (await supervisor.client.command('memory.list')).memories;
    assert(!finalMemories.some(memory => /Raspberry Pi|ESP32/i.test(memory.content)));
    process.stdout.write(JSON.stringify({ result: 'PASS', database: process.env.ISAAC_MEMORY_DB, counts: { beforeTrivial, afterTrivial, final: finalMemories.length }, finalMemories, results }, null, 2) + '\n');
  } finally {
    await supervisor.stop();
    delete process.env.ISAAC_MEMORY_DB;
  }
})().then(() => process.exit(0)).catch(error => { process.stderr.write(`${error.stack || error}\n`); process.exit(1); });
