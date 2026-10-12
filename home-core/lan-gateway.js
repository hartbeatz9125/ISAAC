'use strict';

const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');

const MAX_BODY_BYTES = 12 * 1024;
const MAX_MESSAGE_CHARS = 8000;
const RATE_WINDOW_MS = 60_000;
const RATE_LIMIT = 30;
const ALLOWED_EVENT_PREFIXES = ['isaac.response.', 'isaac.thinking', 'isaac.error', 'isaac.brain.'];
const PHONE_ASSETS = new Map([
  ['/assets/reactor-bay.png', path.join(__dirname, '..', 'assets', 'masters', 'right-reactor-bay-v1.png')],
  ['/assets/reactor-master.png', path.join(__dirname, '..', 'assets', 'masters', 'right-approved-master.png')]
]);

function isPrivateIPv4(address) {
  const parts = String(address).split('.').map(Number);
  if (parts.length !== 4 || parts.some(part => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  return parts[0] === 10 || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) || (parts[0] === 192 && parts[1] === 168);
}

function findPrivateLanAddress() {
  const candidates = [];
  for (const [name, addresses] of Object.entries(os.networkInterfaces())) {
    for (const entry of addresses || []) {
      if (entry.family !== 'IPv4' || entry.internal || !isPrivateIPv4(entry.address)) continue;
      const virtual = /virtual|vethernet|vmware|virtualbox|hyper-v|docker|wsl|tailscale|vpn|loopback/i.test(name);
      const preferred = /wi-?fi|wireless|ethernet/i.test(name);
      candidates.push({ address: entry.address, score: (preferred ? 10 : 0) - (virtual ? 100 : 0) });
    }
  }
  candidates.sort((a, b) => b.score - a.score);
  return candidates[0]?.score >= 0 ? candidates[0].address : null;
}

function json(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer'
  });
  response.end(body);
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    request.on('data', chunk => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(Object.assign(new Error('Request too large'), { status: 413 }));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : {}); }
      catch { reject(Object.assign(new Error('Invalid JSON'), { status: 400 })); }
    });
    request.on('error', reject);
  });
}

class LanGateway {
  constructor({ client, eventSource, staticRoot = path.join(__dirname, 'phone') }) {
    this.client = client;
    this.eventSource = eventSource;
    this.staticRoot = staticRoot;
    this.token = crypto.randomBytes(32).toString('base64url');
    this.server = null;
    this.host = null;
    this.port = null;
    this.conversations = new Set();
    this.streams = new Set();
    this.rateBuckets = new Map();
    this.onCoreEvent = envelope => this.broadcast(envelope);
  }

  authorized(request, url) {
    const bearer = /^Bearer\s+(.+)$/i.exec(request.headers.authorization || '')?.[1];
    const supplied = bearer || url.searchParams.get('token') || '';
    const expected = Buffer.from(this.token);
    const actual = Buffer.from(supplied);
    return expected.length === actual.length && crypto.timingSafeEqual(expected, actual);
  }

  rateAllowed(request) {
    const key = request.socket.remoteAddress || 'unknown';
    const now = Date.now();
    const bucket = (this.rateBuckets.get(key) || []).filter(timestamp => now - timestamp < RATE_WINDOW_MS);
    if (bucket.length >= RATE_LIMIT) return false;
    bucket.push(now);
    this.rateBuckets.set(key, bucket);
    return true;
  }

  async route(request, response) {
    const url = new URL(request.url, `http://${request.headers.host || this.host}`);
    if (request.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
      const page = fs.readFileSync(path.join(this.staticRoot, 'index.html'));
      response.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Content-Length': page.length,
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY'
      });
      response.end(page);
      return;
    }
    if (!this.authorized(request, url)) return json(response, 401, { error: 'Authentication required' });
    if (!this.rateAllowed(request)) return json(response, 429, { error: 'Rate limit exceeded' });

    const phoneAsset = PHONE_ASSETS.get(url.pathname);
    if (request.method === 'GET' && phoneAsset) {
      const content = fs.readFileSync(phoneAsset);
      response.writeHead(200, {
        'Content-Type': 'image/png',
        'Content-Length': content.length,
        'Cache-Control': 'private, max-age=3600',
        'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer'
      });
      response.end(content);
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/events') {
      response.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-store',
        Connection: 'keep-alive',
        'X-Accel-Buffering': 'no'
      });
      response.write(': connected\n\n');
      this.streams.add(response);
      const heartbeat = setInterval(() => response.write(': heartbeat\n\n'), 15_000);
      request.on('close', () => { clearInterval(heartbeat); this.streams.delete(response); });
      return;
    }
    if (request.method === 'GET' && url.pathname === '/api/conversations') {
      return json(response, 200, await this.client.command('conversation.list'));
    }
    if (request.method !== 'POST' || request.headers['content-type']?.split(';')[0] !== 'application/json') {
      return json(response, 404, { error: 'Not found' });
    }

    const body = await readJson(request);
    if (url.pathname === '/api/conversations') {
      const result = await this.client.command('conversation.create');
      this.conversations.add(result.conversationId);
      return json(response, 201, { conversationId: result.conversationId });
    }
    if (url.pathname === '/api/conversations/bootstrap') {
      const result = await this.client.command('conversation.bootstrap');
      this.conversations.add(result.conversationId);
      return json(response, 200, result);
    }
    if (url.pathname === '/api/conversations/select') {
      const result = await this.client.command('conversation.select', { conversationId: body.conversationId });
      this.conversations.add(result.conversationId);
      return json(response, 200, result);
    }
    if (url.pathname === '/api/messages') {
      if (!this.conversations.has(body.conversationId)) return json(response, 403, { error: 'Unknown mobile conversation' });
      if (typeof body.content !== 'string' || !body.content.trim() || body.content.length > MAX_MESSAGE_CHARS) return json(response, 400, { error: 'Invalid message' });
      const result = await this.client.command('conversation.message', { conversationId: body.conversationId, content: body.content.trim() });
      return json(response, 202, { requestId: result.requestId });
    }
    if (url.pathname === '/api/cancel') {
      if (!this.conversations.has(body.conversationId)) return json(response, 403, { error: 'Unknown mobile conversation' });
      const result = await this.client.command('conversation.cancel', { conversationId: body.conversationId });
      return json(response, 200, result);
    }
    return json(response, 404, { error: 'Not found' });
  }

  broadcast(envelope) {
    if (!envelope || !ALLOWED_EVENT_PREFIXES.some(prefix => envelope.type?.startsWith(prefix))) return;
    if (envelope.conversationId && !this.conversations.has(envelope.conversationId)) return;
    const data = `data: ${JSON.stringify(envelope)}\n\n`;
    for (const stream of this.streams) stream.write(data);
  }

  async start() {
    if (this.server) return this.connectionInfo();
    this.host = findPrivateLanAddress();
    if (!this.host) throw new Error('No safe private IPv4 LAN interface found');
    this.eventSource.on('event', this.onCoreEvent);
    this.server = http.createServer((request, response) => {
      this.route(request, response).catch(error => {
        if (!response.headersSent) json(response, error.status || 500, { error: error.status ? error.message : 'Gateway request failed' });
        else response.end();
      });
    });
    this.server.requestTimeout = 20_000;
    this.server.headersTimeout = 10_000;
    await new Promise((resolve, reject) => this.server.listen(0, this.host, resolve).once('error', reject));
    this.port = this.server.address().port;
    return this.connectionInfo();
  }

  connectionInfo() {
    const baseUrl = `http://${this.host}:${this.port}`;
    return { host: this.host, port: this.port, url: baseUrl, pairingUrl: `${baseUrl}/#token=${this.token}` };
  }

  async stop() {
    if (!this.server) return;
    this.eventSource.off('event', this.onCoreEvent);
    for (const stream of this.streams) stream.end();
    this.streams.clear();
    await new Promise(resolve => this.server.close(resolve));
    this.server = null;
    this.conversations.clear();
    this.rateBuckets.clear();
    this.token = '';
  }
}

module.exports = { LanGateway, findPrivateLanAddress, isPrivateIPv4 };
