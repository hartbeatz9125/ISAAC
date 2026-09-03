'use strict';

const { EventEmitter } = require('events');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { CoreClient } = require('./core-client');
const { DEFAULT_HOST, DEFAULT_PORT } = require('./protocol');

class CoreSupervisor extends EventEmitter {
  constructor({ app, host = DEFAULT_HOST, port = DEFAULT_PORT } = {}) {
    super();
    this.app = app;
    this.host = host;
    this.port = port;
    this.token = crypto.randomBytes(32).toString('base64url');
    this.client = new CoreClient({ host, port, token: this.token });
    this.child = null;
    this.ownsChild = false;
    this.stopping = false;
    this.restartTimer = null;
    this.restartDelay = 1000;
    this.logStream = null;
    this.wireClientEvents();
  }
  wireClientEvents() {
    for (const name of ['connected', 'disconnected', 'unavailable', 'event', 'isaac.ready', 'isaac.error']) this.client.on(name, value => this.emit(name, value));
    this.client.on('error', error => this.emit('client-error', error));
  }
  coreRoot() {
    return this.app.isPackaged
      ? path.join(process.resourcesPath, 'app.asar.unpacked', 'isaac-core')
      : path.join(__dirname, '..', 'isaac-core');
  }
  pythonCommand() { return process.env.ISAAC_CORE_PYTHON || 'python'; }
  environment(root) {
    const delimiter = process.platform === 'win32' ? ';' : ':';
    const pythonPath = path.join(root, 'src');
    return { ...process.env, ISAAC_CORE_TOKEN: this.token, PYTHONPATH: process.env.PYTHONPATH ? `${pythonPath}${delimiter}${process.env.PYTHONPATH}` : pythonPath };
  }
  openLog() {
    if (this.logStream) return this.logStream;
    const dir = path.join(this.app.getPath('userData'), 'logs');
    fs.mkdirSync(dir, { recursive: true });
    this.logStream = fs.createWriteStream(path.join(dir, 'isaac-core.log'), { flags: 'a' });
    return this.logStream;
  }
  async start() {
    this.stopping = false;
    try {
      await this.client.connect();
      this.emit('attached');
      return { ok: true, managed: false };
    } catch {
      this.startCoreProcess();
      const ready = await this.waitForHealth(12000);
      if (ready) { await this.client.connect(); return { ok: true, managed: true }; }
      this.emit('unavailable', new Error('ISAAC Core did not become healthy; Home will continue without Core'));
      return { ok: false, managed: true };
    }
  }
  startCoreProcess() {
    if (this.stopping || this.child) return;
    const root = this.coreRoot();
    const args = ['-m', 'isaac_core.main', '--host', this.host, '--port', String(this.port), '--parent-pid', String(process.pid)];
    const log = this.openLog();
    const child = spawn(this.pythonCommand(), args, { cwd: root, env: this.environment(root), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    this.child = child;
    this.ownsChild = true;
    child.stdout.pipe(log, { end: false });
    child.stderr.pipe(log, { end: false });
    child.on('error', error => { this.emit('unavailable', error); this.child = null; this.scheduleProcessRestart(); });
    child.on('exit', (code, signal) => {
      if (this.child === child) this.child = null;
      this.emit('core-exit', { code, signal });
      this.scheduleProcessRestart();
    });
  }
  scheduleProcessRestart() {
    if (this.stopping || this.restartTimer || this.child) return;
    const delay = this.restartDelay;
    this.restartDelay = Math.min(10000, Math.round(this.restartDelay * 1.8));
    this.restartTimer = setTimeout(async () => {
      this.restartTimer = null;
      if (this.stopping) return;
      this.startCoreProcess();
      if (await this.waitForHealth(12000)) {
        this.restartDelay = 1000;
        try { await this.client.connect(); } catch (error) { this.emit('unavailable', error); }
      }
    }, delay);
  }
  async waitForHealth(timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (!this.stopping && Date.now() < deadline) {
      try { await this.client.health(); return true; } catch {}
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    return false;
  }
  async stop() {
    this.stopping = true;
    clearTimeout(this.restartTimer);
    this.restartTimer = null;
    this.client.close();
    const child = this.child;
    if (child && this.ownsChild) {
      try { await this.client.shutdownCore(); } catch {}
      await Promise.race([
        new Promise(resolve => child.once('exit', resolve)),
        new Promise(resolve => setTimeout(resolve, 2500))
      ]);
      if (this.child && !child.killed) child.kill();
    }
    this.child = null;
    if (this.logStream) this.logStream.end();
    this.logStream = null;
  }
}

module.exports = { CoreSupervisor };
