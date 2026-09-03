'use strict';

const assert = require('assert');
const { EventEmitter } = require('events');
const path = require('path');
const { VoiceOutputService, cleanForSpeech, VOICE_ID } = require('../voice-output-service');

function waitFor(emitter, type, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { emitter.off('event', listener); reject(new Error(`${type} timed out`)); }, timeoutMs);
    const listener = envelope => {
      if (envelope.type !== type) return;
      clearTimeout(timer);
      emitter.off('event', listener);
      resolve(envelope);
    };
    emitter.on('event', listener);
  });
}

(async () => {
  const service = new VoiceOutputService({ app: { getPath: name => {
    if (name !== 'documents') throw new Error(`Unexpected app path: ${name}`);
    return path.join(process.env.OneDrive || process.env.USERPROFILE, 'Documents');
  } } });
  const events = [];
  service.on('event', event => events.push(event));

  assert.strictEqual(VOICE_ID, 'bm_lewis');
  assert.strictEqual(cleanForSpeech('**Hello** [Kenneth](https://example.com)\n\nSources:\n- source'), 'Hello Kenneth');

  const normalStarted = waitFor(service, 'isaac.voice.started');
  assert.strictEqual(service.speak("Yeah, I'm here. What's up?", { requestId: 'normal' }).accepted, true);
  const normal = await normalStarted;
  assert.strictEqual(normal.payload.voice, 'bm_lewis');
  assert.strictEqual(normal.payload.leadingSilenceMs, 250);
  assert.strictEqual(service.stop('user').stopped, true);

  service.setEnabled(false);
  assert.strictEqual(service.speak('This must remain silent.').reason, 'muted');
  service.setEnabled(true);

  const technicalStarted = waitFor(service, 'isaac.voice.started');
  service.speak("I found the problem. The service isn't responding.", { requestId: 'technical' });
  const technical = await technicalStarted;
  assert.strictEqual(technical.payload.leadingSilenceMs, 250);
  service.stop('user');

  service.speak("Bro... I don't know how, but that actually worked.", { requestId: 'superseded' });
  await new Promise(resolve => setTimeout(resolve, 100));
  const longerStarted = waitFor(service, 'isaac.voice.started');
  service.speak('I checked the service, verified the local connection, and found the process had stopped responding. The configuration is intact, so restarting the process should restore normal operation without changing anything else.', { requestId: 'longer' });
  const longer = await longerStarted;
  assert.strictEqual(longer.payload.leadingSilenceMs, 250);
  service.stop('user');
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.strictEqual(service.active, null);
  assert(events.some(event => event.type === 'isaac.voice.stopped' && event.payload.reason === 'superseded'));
  assert(events.some(event => event.type === 'isaac.voice.stopped' && event.payload.reason === 'muted') || service.status().enabled);
  await service.shutdown();

  process.stdout.write(JSON.stringify({
    result: 'PASS', voice: VOICE_ID,
    normalGenerationMs: normal.payload.generationMs,
    technicalGenerationMs: technical.payload.generationMs,
    longerGenerationMs: longer.payload.generationMs,
    eventTypes: events.map(event => event.type),
  }, null, 2) + '\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
