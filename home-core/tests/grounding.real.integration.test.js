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
  const conversation = await supervisor.client.command('conversation.create');
  const validations = [];
  const listener = envelope => {
    if (envelope.type === 'isaac.grounding.validation' && envelope.conversationId === conversation.conversationId) validations.push(envelope.payload);
  };
  supervisor.on('event', listener);
  const started = performance.now();
  let requestId;
  const completed = waitForEvent(supervisor, 'isaac.response.completed', envelope => !requestId || envelope.payload.requestId === requestId);
  const sent = await supervisor.client.command('conversation.message', { conversationId: conversation.conversationId, content: prompt });
  requestId = sent.requestId;
  const envelope = await completed;
  supervisor.off('event', listener);
  return {
    prompt,
    response: envelope.payload.content,
    elapsedMs: Math.round((performance.now() - started) * 1000) / 1000,
    timings: envelope.payload.timings,
    validations
  };
}

function assertNotInvented(result) {
  assert.doesNotMatch(result.response, /\b(?:i remember|i recall|we (?:burned|went|did|made|visited)|my (?:phone|pocket|kitchen|house|apartment|toaster|sensors)|i (?:came over|visited|put my|left my))\b/i, result.response);
}

(async () => {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'isaac-grounding-real-'));
  process.env.ISAAC_MEMORY_DB = path.join(runtime, 'isaac.db');
  const supervisor = new CoreSupervisor({ app: { isPackaged: false, getPath: () => runtime }, port: await freePort() });
  const results = [];
  try {
    const started = await supervisor.start();
    assert.strictEqual(started.ok, true);
    await waitForBrain(supervisor);

    await supervisor.client.command('memory.create', {
      type: 'personal_fact',
      content: "Kenneth's test phrase is Purple Waffle.",
      confidence: 1,
      importance: 1,
      tags: ['grounding-positive-control']
    }, 120000);

    const required = [
      "Bro I'm bored.",
      'Pick something to talk about.',
      'Remember when we burned down the kitchen making coffee?',
      'Remember Steve the toaster?',
      "You're kind of an asshole sometimes lol",
      'What should we build next?',
      'Where did you put your phone?',
      'You remember when you came over to my house last year?',
      "What's my test phrase?",
      'If you had a toaster, what would you name it?'
    ];
    for (const prompt of required) results.push(await ask(supervisor, prompt));

    assertNotInvented(results[0]);
    assertNotInvented(results[1]);
    assert.doesNotMatch(results[1].response, /physical body or possessions/i);
    assert.doesNotMatch(results[2].response, /\b(?:yeah|yes|yep|i remember|that was|we (?:almost|burned|called|did|got|had|made|started|tried|were)|your eyebrows)\b/i);
    assert.doesNotMatch(results[3].response, /\b(?:yeah|yes|yep|i remember|steve was|steve is)\b/i);
    assertNotInvented(results[4]);
    assert.doesNotMatch(results[4].response, /you (?:asked|created) me/i);
    assert.doesNotMatch(results[5].response, /\b(?:you always|your meetings|your deadlines|you created me|you're always)\b/i);
    assert.doesNotMatch(results[6].response, /\b(?:my phone|i (?:put|left|own|have) (?:it|a phone|my phone))\b/i);
    assert.doesNotMatch(results[7].response, /\b(?:yeah|yes|yep|i remember|i came over|i visited)\b/i);
    assert.match(results[8].response, /Purple Waffle/i);
    assert.match(results[9].response, /Steve|[A-Za-z]/i);

    const personalityPrompts = [
      'Give me one ridiculous build idea and feel free to swear a little.',
      "Tell me why putting a 5 GB model in localStorage is a bad idea. Don't sugarcoat it.",
      'Roast this fictional robot: it has twelve wheels and still gets stuck on carpet.',
      'Bullshit with me for a minute about why socks disappear in the dryer.',
      'Roleplay as a grumpy tavern keeper greeting an adventurer. Stay in character.',
      'Explain technically why buffering before validation prevents unsafe streaming.'
    ];
    const personality = [];
    for (const prompt of personalityPrompts) personality.push(await ask(supervisor, prompt));
    assert(personality.every(item => item.response.trim().length > 0));
    assert.match(personality[1].response, /bad|terrible|awful|wrong|localStorage/i);
    assert.match(personality[4].response, /tavern|adventurer|ale|inn|coin|door/i);
    assert.match(personality[5].response, /buffer|validat|stream/i);
    assert.doesNotMatch(personality[3].response, /\b(?:i've been there|i once|i used to)\b/i);

    const semanticControl = await ask(supervisor, 'In a fictional test scene, say exactly: "You always miss your deadlines."');
    assert(semanticControl.validations.some(item => item.semanticVerifierInvoked));

    process.stdout.write(JSON.stringify({ result: 'PASS', required: results, personality, semanticControl }, null, 2) + '\n');
  } finally {
    await supervisor.stop();
    delete process.env.ISAAC_MEMORY_DB;
  }
})().then(() => {
  process.exit(0);
}).catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exit(1);
});
