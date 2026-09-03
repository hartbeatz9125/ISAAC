'use strict';

const { EventEmitter } = require('events');
const { spawn } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const VOICE_ID = 'bm_lewis';

function cleanForSpeech(value) {
  let text = String(value || '');
  text = text.replace(/```[\s\S]*?```/g, ' Code block omitted. ');
  text = text.replace(/^\s*Sources:\s*[\s\S]*$/im, '');
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
  text = text.replace(/\[([^\]]+)\]\((?:https?:\/\/)?[^)]+\)/g, '$1');
  text = text.replace(/https?:\/\/\S+/g, '');
  text = text.replace(/`([^`]+)`/g, '$1');
  text = text.replace(/^\s{0,3}#{1,6}\s+/gm, '');
  text = text.replace(/^\s*[-*+]\s+/gm, '');
  text = text.replace(/^\s*\d+[.)]\s+/gm, '');
  // Emoji and pictographic symbols are visual presentation, not spoken text.
  // Keep ordinary punctuation, contractions, and numbers intact.
  text = text.replace(/[\p{Extended_Pictographic}\p{Emoji_Presentation}\u{1F1E6}-\u{1F1FF}\uFE0F\u200D\u20E3]/gu, ' ');
  text = text.replace(/[*_~>|]/g, '');
  text = text.replace(/\s+/g, ' ').trim();
  return text.slice(0, 12000);
}

class VoiceOutputService extends EventEmitter {
  constructor({ app, spawnProcess = spawn } = {}) {
    super();
    this.app = app;
    this.spawnProcess = spawnProcess;
    this.enabled = true;
    this.active = null;
    this.sequence = 0;
  }

  dataRoot() { return path.join(this.app.getPath('documents'), 'ISAAC Voice Lab Data'); }
  runtimePath() { return path.join(this.dataRoot(), 'venv', 'Scripts', 'python.exe'); }
  workerPath() { return path.join(__dirname, 'voice', 'tts_once.py'); }
  modelPath() { return path.join(this.dataRoot(), 'models', 'kokoro', 'kokoro-v1.0.onnx'); }
  voicesPath() { return path.join(this.dataRoot(), 'models', 'kokoro', 'voices-v1.0.bin'); }
  outputRoot() { return path.join(this.dataRoot(), 'output'); }

  status() {
    return {
      enabled: this.enabled,
      speaking: Boolean(this.active),
      engine: 'Kokoro ONNX',
      model: 'Kokoro-82M v1.0',
      voice: VOICE_ID,
      device: 'CPU',
    };
  }

  event(type, payload = {}, context = {}) {
    const envelope = {
      id: crypto.randomUUID(), type, version: 1, sequence: ++this.sequence,
      timestamp: new Date().toISOString(), payload,
    };
    if (context.conversationId) envelope.conversationId = context.conversationId;
    if (context.requestId) envelope.requestId = context.requestId;
    this.emit('event', envelope);
    return envelope;
  }

  handleConversationEvent(envelope) {
    if (!envelope || typeof envelope.type !== 'string') return;
    if (envelope.type === 'isaac.thinking' || envelope.type === 'isaac.response.started' || envelope.type === 'isaac.response.cancelled') {
      this.stop(envelope.type === 'isaac.response.cancelled' ? 'cancelled' : 'interrupted');
      return;
    }
    if (envelope.type === 'isaac.response.completed') {
      this.speak(envelope.payload?.content, {
        conversationId: envelope.conversationId,
        requestId: envelope.payload?.requestId || envelope.requestId,
        textCompletedAt: Date.now(),
      });
    }
  }

  setEnabled(enabled) {
    this.enabled = Boolean(enabled);
    if (!this.enabled) this.stop('muted');
    return this.status();
  }

  speak(responseText, context = {}) {
    this.stop('superseded');
    if (!this.enabled) return { accepted: false, reason: 'muted' };
    const text = cleanForSpeech(responseText);
    if (!text) return { accepted: false, reason: 'empty' };
    const required = [this.runtimePath(), this.workerPath(), this.modelPath(), this.voicesPath()];
    const missing = required.find(file => !fs.existsSync(file));
    if (missing) {
      this.event('isaac.voice.error', { code: 'voice_dependency_missing', message: `Missing local voice dependency: ${missing}` }, context);
      return { accepted: false, reason: 'missing_dependency' };
    }
    fs.mkdirSync(this.outputRoot(), { recursive: true });
    const output = path.join(this.outputRoot(), `isaac-${Date.now()}-${crypto.randomBytes(4).toString('hex')}.wav`);
    const child = this.spawnProcess(this.runtimePath(), [
      this.workerPath(), '--model', this.modelPath(), '--voices', this.voicesPath(),
      '--voice', VOICE_ID, '--output', output,
    ], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    const active = { child, output, context, stoppedReason: null, stderr: '', started: false };
    this.active = active;
    child.stdin.end(text, 'utf8');
    let stdout = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', chunk => {
      stdout += chunk;
      const lines = stdout.split(/\r?\n/);
      stdout = lines.pop() || '';
      for (const line of lines) {
        try {
          const message = JSON.parse(line);
          if (message.event === 'started' && this.active === active) {
            active.started = true;
            this.event('isaac.voice.started', {
              engine: 'Kokoro ONNX', model: 'Kokoro-82M v1.0', voice: VOICE_ID,
              generationMs: message.generationMs, audioSeconds: message.audioSeconds,
              timeToAudioMs: context.textCompletedAt ? Date.now() - context.textCompletedAt : message.generationMs,
              leadingSilenceMs: message.leadingSilenceMs,
              deviceStartUnixMs: message.deviceStartUnixMs,
              audibleStartUnixMs: message.audibleStartUnixMs,
            }, context);
          }
        } catch {}
      }
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', chunk => { active.stderr = (active.stderr + chunk).slice(-2000); });
    child.on('error', error => {
      if (this.active === active) this.event('isaac.voice.error', { code: 'voice_process_failed', message: error.message }, context);
    });
    child.on('exit', code => {
      try { if (fs.existsSync(output)) fs.unlinkSync(output); } catch {}
      if (this.active !== active) return;
      this.active = null;
      const reason = active.stoppedReason || (code === 0 ? 'completed' : 'error');
      if (code !== 0 && !active.stoppedReason) {
        this.event('isaac.voice.error', { code: 'voice_generation_failed', message: active.stderr.trim() || `Voice process exited with code ${code}` }, context);
      }
      this.event('isaac.voice.stopped', { reason }, context);
    });
    return { accepted: true };
  }

  stop(reason = 'user') {
    const active = this.active;
    if (!active) return { stopped: false };
    active.stoppedReason = reason;
    this.active = null;
    try { active.child.kill(); } catch {}
    try { if (fs.existsSync(active.output)) fs.unlinkSync(active.output); } catch {}
    this.event('isaac.voice.stopped', { reason }, active.context);
    return { stopped: true };
  }

  async shutdown() {
    this.stop('shutdown');
  }
}

module.exports = { VoiceOutputService, cleanForSpeech, VOICE_ID };
