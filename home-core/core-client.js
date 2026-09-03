'use strict';

const { EventEmitter } = require('events');
const crypto = require('crypto');
const { PROTOCOL_VERSION, DEFAULT_HOST, DEFAULT_PORT, HEALTH_PATH, CAPABILITIES_PATH, EVENTS_PATH, SHUTDOWN_PATH } = require('./protocol');

class CoreClient extends EventEmitter {
  constructor({ host = DEFAULT_HOST, port = DEFAULT_PORT, token, reconnect = true } = {}) {
    super();
    this.host = host;
    this.port = port;
    this.token = token;
    this.reconnect = reconnect;
    this.socket = null;
    this.closed = false;
    this.retryTimer = null;
    this.retryDelay = 500;
    this.pendingCommands = new Map();
  }
  get baseUrl() { return `http://${this.host}:${this.port}`; }
  headers() { return { Authorization: `Bearer ${this.token}`, Accept: 'application/json' }; }
  async request(path, options = {}) {
    const response = await fetch(this.baseUrl + path, { ...options, headers: { ...this.headers(), ...(options.headers || {}) }, signal: AbortSignal.timeout(options.timeoutMs || 2000) });
    if (!response.ok) throw new Error(`ISAAC Core request failed (${response.status})`);
    const text = await response.text();
    return text ? JSON.parse(text) : null;
  }
  async health() {
    const health = await this.request(HEALTH_PATH);
    if (health.protocolVersion !== PROTOCOL_VERSION) throw new Error(`ISAAC protocol mismatch: Home=${PROTOCOL_VERSION} Core=${health.protocolVersion}`);
    return health;
  }
  capabilities() { return this.request(CAPABILITIES_PATH); }
  async connect() {
    this.closed = false;
    await this.health();
    this.openSocket();
  }
  openSocket() {
    if (this.closed || this.socket) return;
    const url = `ws://${this.host}:${this.port}${EVENTS_PATH}?access_token=${encodeURIComponent(this.token)}`;
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.addEventListener('open', () => { this.retryDelay = 500; this.emit('connected'); });
    socket.addEventListener('message', event => {
      try {
        const envelope = JSON.parse(String(event.data));
        if (envelope.version !== PROTOCOL_VERSION || typeof envelope.type !== 'string') throw new Error('Invalid ISAAC event envelope');
        if (envelope.type === 'isaac.command.result') {
          const pending = this.pendingCommands.get(envelope.payload?.requestId);
          if (pending) {
            this.pendingCommands.delete(envelope.payload.requestId);
            clearTimeout(pending.timer);
            if (envelope.payload.ok) pending.resolve(envelope.payload.result);
            else pending.reject(new Error(envelope.payload.error || 'ISAAC command failed'));
          }
        }
        this.emit('event', envelope);
        this.emit(envelope.type, envelope);
      } catch (error) { this.emit('error', error); }
    });
    socket.addEventListener('error', () => this.emit('unavailable', new Error('ISAAC Core event connection failed')));
    socket.addEventListener('close', () => {
      this.socket = null;
      this.rejectPending(new Error('ISAAC Core disconnected'));
      this.emit('disconnected');
      this.scheduleReconnect();
    });
  }
  command(type, payload = {}, timeoutMs = 15000) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return Promise.reject(new Error('ISAAC Core is not connected'));
    const requestId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingCommands.delete(requestId);
        reject(new Error(`ISAAC command timed out: ${type}`));
      }, timeoutMs);
      this.pendingCommands.set(requestId, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ type, requestId, version: PROTOCOL_VERSION, payload }));
    });
  }
  rejectPending(error) {
    for (const pending of this.pendingCommands.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pendingCommands.clear();
  }
  scheduleReconnect() {
    if (this.closed || !this.reconnect || this.retryTimer) return;
    const delay = this.retryDelay;
    this.retryDelay = Math.min(10000, Math.round(this.retryDelay * 1.8));
    this.retryTimer = setTimeout(async () => {
      this.retryTimer = null;
      try { await this.health(); this.openSocket(); } catch (error) { this.emit('unavailable', error); this.scheduleReconnect(); }
    }, delay);
  }
  async shutdownCore() {
    return this.request(SHUTDOWN_PATH, { method: 'POST', timeoutMs: 3000 });
  }
  close() {
    this.closed = true;
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.rejectPending(new Error('ISAAC Core client closed'));
    if (this.socket) this.socket.close(1000, 'ISAAC Home stopping');
    this.socket = null;
  }
}

module.exports = { CoreClient };
