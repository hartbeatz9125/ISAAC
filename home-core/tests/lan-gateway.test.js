'use strict';

const assert = require('assert');
const { EventEmitter } = require('events');
const test = require('node:test');
const { LanGateway } = require('../lan-gateway');

test('LAN gateway authenticates, streams only conversation events, and closes', async () => {
  const events = new EventEmitter();
  const conversationId = 'mobile-test-conversation';
  const client = {
    async command(type, payload) {
      if (type === 'conversation.create') return { conversationId };
      if (type === 'conversation.bootstrap' || type === 'conversation.select') return { conversationId, title: 'Phone Chat', messages: [{ role: 'user', content: 'Restored' }] };
      if (type === 'conversation.list') return { conversations: [{ conversationId, title: 'Phone Chat' }] };
      if (type === 'conversation.message') {
        setTimeout(() => {
          events.emit('event', { version: '1.0', type: 'isaac.response.started', conversationId, payload: { requestId: 'request-1' } });
          events.emit('event', { version: '1.0', type: 'isaac.response.delta', conversationId, payload: { requestId: 'request-1', delta: 'Purple Waffle' } });
          events.emit('event', { version: '1.0', type: 'isaac.response.completed', conversationId, payload: { requestId: 'request-1' } });
        }, 25);
        return { requestId: 'request-1' };
      }
      if (type === 'conversation.cancel') return { cancelled: true };
      throw new Error(`Unexpected command: ${type}`);
    }
  };
  const gateway = new LanGateway({ client, eventSource: events });
  const info = await gateway.start();
  const auth = { Authorization: `Bearer ${gateway.token}`, 'Content-Type': 'application/json' };
  try {
    const rejected = await fetch(`${info.url}/api/conversations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    assert.strictEqual(rejected.status, 401);

    const rejectedAsset = await fetch(`${info.url}/assets/reactor-bay.png`);
    assert.strictEqual(rejectedAsset.status, 401);
    const allowedAsset = await fetch(`${info.url}/assets/reactor-bay.png?token=${encodeURIComponent(gateway.token)}`);
    assert.strictEqual(allowedAsset.status, 200);
    assert.strictEqual(allowedAsset.headers.get('content-type'), 'image/png');

    const created = await fetch(`${info.url}/api/conversations`, { method: 'POST', headers: auth, body: '{}' });
    assert.strictEqual(created.status, 201);
    assert.strictEqual((await created.json()).conversationId, conversationId);

    const listed = await fetch(`${info.url}/api/conversations`, { headers: { Authorization: `Bearer ${gateway.token}` } });
    assert.strictEqual((await listed.json()).conversations[0].title, 'Phone Chat');
    const resumed = await fetch(`${info.url}/api/conversations/bootstrap`, { method: 'POST', headers: auth, body: '{}' });
    assert.strictEqual((await resumed.json()).messages[0].content, 'Restored');

    const stream = await fetch(`${info.url}/api/events?token=${encodeURIComponent(gateway.token)}`);
    assert.strictEqual(stream.status, 200);
    const reader = stream.body.getReader();
    await fetch(`${info.url}/api/messages`, { method: 'POST', headers: auth, body: JSON.stringify({ conversationId, content: 'What is my phone test phrase?' }) });
    let received = '';
    const deadline = Date.now() + 2000;
    while (!received.includes('Purple Waffle') && Date.now() < deadline) received += new TextDecoder().decode((await reader.read()).value || new Uint8Array());
    assert.match(received, /Purple Waffle/);
    await reader.cancel();
  } finally {
    await gateway.stop();
  }
  await assert.rejects(fetch(`${info.url}/`, { signal: AbortSignal.timeout(500) }));
});
