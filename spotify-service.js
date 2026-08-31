const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { safeStorage } = require('electron');
const CALLBACK_PORT = 43821;
const REDIRECT_URI = `http://127.0.0.1:${CALLBACK_PORT}/callback`;

class SpotifyService {
  constructor({ app, shell }) {
    this.app = app;
    this.shell = shell;
    this.tokens = this.readTokens();
    this.currentDestination = null;
    this.rateLimitedUntil = 0;
    fs.mkdirSync(this.app.getPath('userData'), { recursive: true });
    if (!fs.existsSync(this.configPath())) fs.writeFileSync(this.configPath(), JSON.stringify({ clientId: '' }, null, 2));
  }
  configPath() { return path.join(this.app.getPath('userData'), 'spotify-config.json'); }
  config() {
    let file = {};
    try { file = JSON.parse(fs.readFileSync(this.configPath(), 'utf8')); } catch {
      try { file = JSON.parse(fs.readFileSync(path.join(__dirname, 'config', 'spotify.json'), 'utf8')); } catch {}
    }
    return { clientId: String(process.env.SPOTIFY_CLIENT_ID || file.clientId || '').trim() };
  }
  tokenPath() { return path.join(this.app.getPath('userData'), 'spotify-auth.bin'); }
  readTokens() {
    try {
      const encrypted = fs.readFileSync(path.join(this.app.getPath('userData'), 'spotify-auth.bin'));
      if (!safeStorage.isEncryptionAvailable()) return null;
      return JSON.parse(safeStorage.decryptString(encrypted));
    } catch { return null; }
  }
  writeTokens(tokens) {
    this.tokens = tokens;
    if (!safeStorage.isEncryptionAvailable()) throw new Error('Windows credential encryption is unavailable');
    fs.writeFileSync(this.tokenPath(), safeStorage.encryptString(JSON.stringify(tokens)));
  }
  async tokenRequest(params) {
    const response = await fetch('https://accounts.spotify.com/api/token', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(params)
    });
    if (!response.ok) throw new Error(`Spotify authorization failed (${response.status})`);
    return response.json();
  }
  async connect() {
    const { clientId } = this.config();
    if (!clientId) return { ok: false, status: 'configuration-required', message: 'Spotify Client ID required' };
    const verifier = crypto.randomBytes(64).toString('base64url');
    const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
    const state = crypto.randomBytes(24).toString('base64url');
    return new Promise(resolve => {
      let settled = false, timeout;
      const finish = result => { if (settled) return; settled = true; clearTimeout(timeout); if (server.listening) server.close(); resolve(result); };
      const server = http.createServer(async (request, response) => {
        try {
          const url = new URL(request.url, 'http://127.0.0.1');
          if (url.pathname !== '/callback') { response.writeHead(404).end(); return; }
          if (url.searchParams.get('state') !== state) throw new Error('Spotify authorization state mismatch');
          if (url.searchParams.get('error')) throw new Error(`Spotify authorization ${url.searchParams.get('error')}`);
          const code = url.searchParams.get('code'); if (!code) throw new Error('Spotify did not return an authorization code');
          const data = await this.tokenRequest({ grant_type: 'authorization_code', code, redirect_uri: REDIRECT_URI, client_id: clientId, code_verifier: verifier });
          this.writeTokens({ accessToken: data.access_token, refreshToken: data.refresh_token, expiresAt: Date.now() + data.expires_in * 1000 });
          response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }).end('<!doctype html><title>Spotify connected</title><style>body{background:#05090c;color:#8eeaff;font:18px Segoe UI;display:grid;place-items:center;height:100vh;margin:0}</style><p>Spotify connected. You can close this window.</p>');
          finish({ ok: true, status: 'connected' });
        } catch (error) { response.writeHead(400).end('Spotify connection failed.'); finish({ ok: false, status: 'error', message: error.message }); }
      });
      server.on('error', error => finish({ ok: false, status: 'error', message: error.code === 'EADDRINUSE' ? `Spotify callback port ${CALLBACK_PORT} is already in use` : error.message }));
      server.listen(CALLBACK_PORT, '127.0.0.1', async () => {
        const auth = new URL('https://accounts.spotify.com/authorize');
        auth.search = new URLSearchParams({ client_id: clientId, response_type: 'code', redirect_uri: REDIRECT_URI, scope: 'user-read-currently-playing', code_challenge_method: 'S256', code_challenge: challenge, state }).toString();
        try { await this.shell.openExternal(auth.toString()); } catch (error) { finish({ ok: false, status: 'error', message: error.message }); }
      });
      timeout = setTimeout(() => finish({ ok: false, status: 'error', message: 'Spotify connection timed out' }), 180000);
    });
  }
  async refresh() {
    const { clientId } = this.config();
    if (!this.tokens?.refreshToken || !clientId) throw new Error('Spotify reconnect required');
    const data = await this.tokenRequest({ grant_type: 'refresh_token', refresh_token: this.tokens.refreshToken, client_id: clientId });
    this.writeTokens({ accessToken: data.access_token, refreshToken: data.refresh_token || this.tokens.refreshToken, expiresAt: Date.now() + data.expires_in * 1000 });
  }
  async accessToken() {
    if (!this.tokens) return null;
    if (Date.now() > this.tokens.expiresAt - 60000) await this.refresh();
    return this.tokens.accessToken;
  }
  async getStatus(retry = true) {
    if (!this.config().clientId) return { status: 'configuration-required' };
    if (!this.tokens) return { status: 'disconnected' };
    if (Date.now() < this.rateLimitedUntil) return { status: 'rate-limited', retryAfterMs: this.rateLimitedUntil - Date.now() };
    try {
      const token = await this.accessToken();
      const response = await fetch('https://api.spotify.com/v1/me/player/currently-playing?additional_types=track,episode', { headers: { Authorization: `Bearer ${token}` } });
      if (response.status === 204) { this.currentDestination = null; return { status: 'nothing-playing' }; }
      if (response.status === 401 && retry) { await this.refresh(); return this.getStatus(false); }
      if (response.status === 429) { const seconds = Math.max(5, Number(response.headers.get('retry-after')) || 15); this.rateLimitedUntil = Date.now() + seconds * 1000; return { status: 'rate-limited', retryAfterMs: seconds * 1000 }; }
      if (!response.ok) throw new Error(`Spotify unavailable (${response.status})`);
      const data = await response.json(), item = data.item;
      if (!item) { this.currentDestination = null; return { status: 'nothing-playing' }; }
      const album = item.album || item.show || {};
      const artists = item.artists?.map(artist => artist.name).join(', ') || item.show?.publisher || '';
      const images = album.images || item.images || [];
      this.currentDestination = { uri: item.uri, web: item.external_urls?.spotify || album.external_urls?.spotify || null };
      return { status: 'playing', title: item.name || '', artist: artists, album: album.name || '', artwork: images[0]?.url || '', progressMs: data.progress_ms || 0, durationMs: item.duration_ms || 0, isPlaying: Boolean(data.is_playing), receivedAt: Date.now(), destination: this.currentDestination };
    } catch (error) {
      if (/reconnect|required|authorization/i.test(error.message)) { this.tokens = null; return { status: 'disconnected' }; }
      return { status: 'error', message: error.message };
    }
  }
  async openCurrent(destination) {
    const target = destination || this.currentDestination;
    if (!target) return { ok: false };
    if (target.uri) { try { await this.shell.openExternal(target.uri); return { ok: true }; } catch {} }
    if (target.web) { await this.shell.openExternal(target.web); return { ok: true }; }
    return { ok: false };
  }
  disconnect() { this.tokens = null; try { fs.unlinkSync(this.tokenPath()); } catch {} return { ok: true, status: 'disconnected' }; }
}

module.exports = { SpotifyService };
