'use strict';

const assert = require('assert');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { CoreSupervisor } = require('../core-supervisor');
const { LanGateway } = require('../lan-gateway');

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
    const listener = event => {
      if (event.type === type && predicate(event)) { emitter.off('event', listener); resolve(event); }
    };
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
  const conversation = conversationId ? { conversationId } : await supervisor.client.command('conversation.create');
  const tools = [];
  const listener = event => { if (event.conversationId === conversation.conversationId && event.type.startsWith('isaac.tool.')) tools.push(event); };
  supervisor.on('event', listener);
  const started = performance.now();
  let requestId;
  const completed = waitForEvent(supervisor, 'isaac.response.completed', event => !requestId || event.payload.requestId === requestId);
  const sent = await supervisor.client.command('conversation.message', { conversationId: conversation.conversationId, content: prompt });
  requestId = sent.requestId;
  const event = await completed;
  supervisor.off('event', listener);
  return { conversationId: conversation.conversationId, prompt, response: event.payload.content, elapsedMs: Math.round(performance.now() - started), tools };
}

(async () => {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-web-real-'));
  process.env.ISAAC_MEMORY_DB = path.join(runtime, 'isaac.db');
  const supervisor = new CoreSupervisor({ app: { isPackaged: false, getPath: () => runtime }, port: await freePort() });
  let gateway;
  try {
    assert.strictEqual((await supervisor.start()).ok, true);
    const brain = await waitForBrain(supervisor);
    const casual = await ask(supervisor, 'Yo, give me a random offline idea.');
    assert.strictEqual(casual.tools.length, 0);

    const researched = await ask(supervisor, 'Search the internet for the current stable Python release and summarize what you can verify.');
    assert(researched.tools.some(event => event.type === 'isaac.tool.started'));
    assert.match(researched.response, /Sources:/);
    assert.match(researched.response, /https:\/\//);
    assert(researched.response.split('Sources:')[0].trim().length >= 40, researched.response);

    gateway = new LanGateway({ client: supervisor.client, eventSource: supervisor });
    const phone = await gateway.start();
    const auth = { Authorization: `Bearer ${gateway.token}`, 'Content-Type': 'application/json' };
    const created = await fetch(`${phone.url}/api/conversations`, { method: 'POST', headers: auth, body: '{}' }).then(response => response.json());
    const phoneTools = [];
    const phoneListener = event => { if (event.conversationId === created.conversationId && event.type.startsWith('isaac.tool.')) phoneTools.push(event); };
    supervisor.on('event', phoneListener);
    let phoneRequestId;
    const phoneCompleted = waitForEvent(supervisor, 'isaac.response.completed', event => event.conversationId === created.conversationId && (!phoneRequestId || event.payload.requestId === phoneRequestId));
    const phoneSend = await fetch(`${phone.url}/api/messages`, { method: 'POST', headers: auth, body: JSON.stringify({ conversationId: created.conversationId, content: 'Look up the latest Python release using the public web.' }) });
    assert.strictEqual(phoneSend.status, 202);
    phoneRequestId = (await phoneSend.json()).requestId;
    const phoneEvent = await phoneCompleted;
    supervisor.off('event', phoneListener);
    const phoneResult = { response: phoneEvent.payload.content, tools: phoneTools };
    assert.match(phoneResult.response, /Sources:/);
    assert(phoneResult.response.split('Sources:')[0].trim().length >= 40, phoneResult.response);
    assert(phoneResult.tools.some(event => event.type === 'isaac.tool.started'));
    const memories = await supervisor.client.command('memory.list');
    assert.deepStrictEqual(memories.memories, []);

    process.stdout.write(JSON.stringify({ result: 'PASS', brain: { state: brain.state, model: brain.model }, memoryCountAfterResearch: memories.memories.length, casual, researched, phone: { gateway: phone.url, response: phoneResult } }, null, 2) + '\n');
  } finally {
    if (gateway) await gateway.stop();
    await supervisor.stop();
    delete process.env.ISAAC_MEMORY_DB;
  }
})().then(() => process.exit(0)).catch(error => { process.stderr.write(`${error.stack || error}\n`); process.exit(1); });
