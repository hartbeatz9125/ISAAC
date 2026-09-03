const { app, BrowserWindow, Menu, screen, globalShortcut, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const { SpotifyService } = require('./spotify-service');
const { CoreSupervisor } = require('./home-core/core-supervisor');
const { LanGateway } = require('./home-core/lan-gateway');
const { VoiceOutputService } = require('./home-core/voice-output-service');

app.commandLine.appendSwitch('high-dpi-support', '1');
app.commandLine.appendSwitch('force-device-scale-factor', '1');

let windows = [];
let spotifyService;
let coreSupervisor;
let coreQuitInProgress = false;
let isaacDevWindow;
let lanGateway;
let voiceOutput;

function safeLogValue(value) { return String(value ?? '').replace(/[\r\n]+/g, ' ').slice(0, 1000); }
function appendLaunchLog(entry) {
  const logDir = path.join(app.getPath('userData'), 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  const logPath = path.join(logDir, 'game-launch.log');
  fs.appendFileSync(logPath, JSON.stringify(entry) + '\n', 'utf8');
  return logPath;
}
function appendCoreHomeLog(event, detail = {}) {
  try {
    const logDir = path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(logDir, { recursive: true });
    fs.appendFileSync(path.join(logDir, 'isaac-home.log'), JSON.stringify({ timestamp: new Date().toISOString(), event, ...detail }) + '\n', 'utf8');
  } catch {}
}
function spawnDetached(command, args, cwd) {
  const child = spawn(command, args, { cwd: cwd || undefined, detached: true, stdio: 'ignore', windowsHide: true });
  child.unref();
}
async function launchGame(game) {
  const startedAt = new Date().toISOString();
  const launch = game?.launch;
  const base = { timestamp: startedAt, title: safeLogValue(game?.title), source: safeLogValue(game?.source), launchType: safeLogValue(launch?.type), attempted: '' };
  try {
    if (!game || !launch || !launch.type) throw new Error('No usable launch mapping');
    if (launch.type === 'xbox-aumid' || launch.type === 'aumid') {
      const identity = launch.arguments?.[0];
      if (!identity || !identity.startsWith('shell:AppsFolder\\')) throw new Error('Invalid AppsFolder identity');
      base.attempted = identity; spawnDetached('explorer.exe', [identity]);
    } else if (launch.type === 'steam') {
      if (!/^\d+$/.test(String(launch.appId || ''))) throw new Error('Invalid Steam App ID');
      base.attempted = `steam://rungameid/${launch.appId}`; await shell.openExternal(base.attempted);
    } else if (launch.type === 'epic' || launch.type === 'uri') {
      if (!/^([a-z][a-z0-9+.-]*):/i.test(launch.uri || '')) throw new Error('Invalid launch URI');
      base.attempted = launch.uri; await shell.openExternal(launch.uri);
    } else if (launch.type === 'executable') {
      if (!path.isAbsolute(launch.target || '') || !fs.existsSync(launch.target)) throw new Error('Mapped executable does not exist');
      base.attempted = launch.target; spawnDetached(launch.target, Array.isArray(launch.arguments) ? launch.arguments : [], launch.workingDirectory);
    } else throw new Error(`Unsupported launch type: ${launch.type}`);
    const logPath = appendLaunchLog({ ...base, success: true });
    return { ok: true, logPath };
  } catch (error) {
    const message = safeLogValue(error?.message || error);
    const logPath = appendLaunchLog({ ...base, success: false, error: message });
    return { ok: false, error: message, logPath };
  }
}

function orderedDisplays() {
  return screen.getAllDisplays().slice().sort((a, b) =>
    a.bounds.x === b.bounds.x ? a.bounds.y - b.bounds.y : a.bounds.x - b.bounds.x
  );
}

function createDisplayWindow(display, side) {
  const { x, y, width, height } = display.bounds;
  const win = new BrowserWindow({
    x, y, width, height,
    frame: false,
    titleBarStyle: 'hidden',
    fullscreen: true,
    autoHideMenuBar: true,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    backgroundColor: '#000000',
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'preload.js'),
      backgroundThrottling: false
    }
  });

  win.removeMenu();
  win.isaacDisplaySide = side;
  if (side === 'right') {
    win.webContents.on('before-input-event', (event, input) => {
      if (!input.control || input.alt || input.meta || input.type !== 'keyDown') return;
      const action = String(input.key || '').toLowerCase();
      if (action === 'v') win.webContents.paste();
      else if (action === 'c') win.webContents.copy();
      else if (action === 'x') win.webContents.cut();
      else if (action === 'a') win.webContents.selectAll();
      else return;
      event.preventDefault();
    });
    win.webContents.on('context-menu', (_event, params) => {
      const flags = params.editFlags || {};
      Menu.buildFromTemplate([
        { role: 'cut', enabled: Boolean(params.isEditable && flags.canCut) },
        { role: 'copy', enabled: Boolean(flags.canCopy) },
        { role: 'paste', enabled: Boolean(params.isEditable && flags.canPaste) },
        { type: 'separator' },
        { role: 'selectAll', enabled: Boolean(params.isEditable && flags.canSelectAll) }
      ]).popup({ window: win });
    });
  }
  win.loadFile(path.join(__dirname, side + '-display', 'index.html'));
  win.once('ready-to-show', () => {
    win.setBounds(display.bounds, false);
    win.setFullScreen(true);
    win.show();
  });
  win.on('closed', () => { windows = windows.filter(w => w !== win); });
  windows.push(win);
}

function createReviewWindow() {
  const primary = screen.getPrimaryDisplay();
  const win = new BrowserWindow({
    ...primary.workArea,
    backgroundColor: '#000000',
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  win.removeMenu();
  win.loadFile(path.join(__dirname, 'review', 'index.html'));
  windows.push(win);
}

function createIsaacDevWindow() {
  if (isaacDevWindow && !isaacDevWindow.isDestroyed()) {
    if (isaacDevWindow.isMinimized()) isaacDevWindow.restore();
    isaacDevWindow.setAlwaysOnTop(true, 'screen-saver');
    isaacDevWindow.show();
    isaacDevWindow.moveTop();
    isaacDevWindow.focus();
    appendCoreHomeLog('shortcut.dev-chat.window-focused');
    return;
  }
  isaacDevWindow = new BrowserWindow({
    width: 620,
    height: 720,
    title: 'ISAAC Phase 2 Test',
    backgroundColor: '#05090d',
    autoHideMenuBar: true,
    show: false,
    alwaysOnTop: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, 'home-core', 'dev-preload.js')
    }
  });
  const editMenu = Menu.buildFromTemplate([{
    label: 'Edit',
    submenu: [
      { role: 'undo' },
      { role: 'redo' },
      { type: 'separator' },
      { role: 'cut' },
      { role: 'copy' },
      { role: 'paste' },
      { role: 'selectAll' }
    ]
  }]);
  isaacDevWindow.setMenu(editMenu);
  isaacDevWindow.webContents.on('context-menu', (_event, params) => {
    const flags = params.editFlags || {};
    Menu.buildFromTemplate([
      { role: 'cut', enabled: Boolean(params.isEditable && flags.canCut) },
      { role: 'copy', enabled: Boolean(flags.canCopy) },
      { role: 'paste', enabled: Boolean(params.isEditable && flags.canPaste) },
      { type: 'separator' },
      { role: 'selectAll', enabled: Boolean(flags.canSelectAll) }
    ]).popup({ window: isaacDevWindow });
  });
  isaacDevWindow.loadFile(path.join(__dirname, 'home-core', 'dev-chat', 'index.html'));
  isaacDevWindow.once('ready-to-show', () => {
    isaacDevWindow.show();
    isaacDevWindow.moveTop();
    isaacDevWindow.focus();
    appendCoreHomeLog('shortcut.dev-chat.window-opened');
  });
  isaacDevWindow.on('closed', () => { isaacDevWindow = null; });
}

function registerIsaacDevShortcut(attempt = 1) {
  const accelerator = 'CommandOrControl+Shift+K';
  if (globalShortcut.isRegistered(accelerator)) {
    appendCoreHomeLog('shortcut.registration', { accelerator, registered: true, attempt, alreadyRegistered: true });
    return true;
  }
  const registered = globalShortcut.register(accelerator, () => {
    appendCoreHomeLog('shortcut.dev-chat.invoked', { accelerator });
    createIsaacDevWindow();
  });
  const verified = registered && globalShortcut.isRegistered(accelerator);
  appendCoreHomeLog('shortcut.registration', { accelerator, registered, verified, attempt });
  if (!verified && attempt < 5) setTimeout(() => registerIsaacDevShortcut(attempt + 1), 1000);
  return verified;
}

function sendIsaacEventToDevWindow(envelope) {
  if (isaacDevWindow && !isaacDevWindow.isDestroyed()) isaacDevWindow.webContents.send('isaac:event', envelope);
}

function sendIsaacEventToRightDisplay(envelope) {
  const rightDisplay = windows.find(win => !win.isDestroyed() && win.isaacDisplaySide === 'right');
  if (rightDisplay) rightDisplay.webContents.send('isaac:event', envelope);
}

function broadcastIsaacEvent(envelope) {
  sendIsaacEventToDevWindow(envelope);
  sendIsaacEventToRightDisplay(envelope);
}

app.whenReady().then(() => {
  voiceOutput = new VoiceOutputService({ app });
  voiceOutput.on('event', envelope => {
    appendCoreHomeLog(envelope.type, { reason: envelope.payload?.reason, generationMs: envelope.payload?.generationMs, timeToAudioMs: envelope.payload?.timeToAudioMs });
    broadcastIsaacEvent(envelope);
  });
  ipcMain.handle('game:launch', (_event, game) => launchGame(game));
  spotifyService = new SpotifyService({ app, shell });
  ipcMain.handle('spotify:status', () => spotifyService.getStatus());
  ipcMain.handle('spotify:connect', () => spotifyService.connect());
  ipcMain.handle('spotify:disconnect', () => spotifyService.disconnect());
  ipcMain.handle('spotify:open-current', (_event, destination) => spotifyService.openCurrent(destination));
  ipcMain.handle('isaac:conversation:create', () => coreSupervisor?.client.command('conversation.create'));
  ipcMain.handle('isaac:conversation:message', (_event, payload) => {
    voiceOutput?.stop('interrupted');
    return coreSupervisor?.client.command('conversation.message', payload);
  });
  ipcMain.handle('isaac:conversation:cancel', (_event, payload) => {
    voiceOutput?.stop('cancelled');
    return coreSupervisor?.client.command('conversation.cancel', payload);
  });
  ipcMain.handle('isaac:voice:status', () => voiceOutput?.status());
  ipcMain.handle('isaac:voice:enabled', (_event, enabled) => voiceOutput?.setEnabled(enabled));
  ipcMain.handle('isaac:voice:stop', () => voiceOutput?.stop('user'));
  ipcMain.handle('isaac:brain:status', () => coreSupervisor?.client.command('brain.status'));
  ipcMain.handle('isaac:memory:status', () => coreSupervisor?.client.command('memory.status'));
  ipcMain.handle('isaac:memory:list', (_event, payload) => coreSupervisor?.client.command('memory.list', payload));
  ipcMain.handle('isaac:memory:search', (_event, payload) => coreSupervisor?.client.command('memory.search', payload));
  ipcMain.handle('isaac:memory:get', (_event, payload) => coreSupervisor?.client.command('memory.get', payload));
  ipcMain.handle('isaac:memory:create', (_event, payload) => coreSupervisor?.client.command('memory.create', payload));
  ipcMain.handle('isaac:memory:update', (_event, payload) => coreSupervisor?.client.command('memory.update', payload));
  ipcMain.handle('isaac:memory:delete', (_event, payload) => coreSupervisor?.client.command('memory.delete', payload));
  if (process.argv.includes('--review')) {
    createReviewWindow();
  } else {
    const displays = orderedDisplays();
    createDisplayWindow(displays[0], 'left');
    createDisplayWindow(displays[1] || displays[0], 'right');
  }

  globalShortcut.register('CommandOrControl+Shift+Q', () => app.quit());
  globalShortcut.register('CommandOrControl+Shift+R', () => windows.forEach(w => w.reload()));
  registerIsaacDevShortcut();

  coreSupervisor = new CoreSupervisor({ app });
  coreSupervisor.on('connected', () => appendCoreHomeLog('core.connected'));
  coreSupervisor.on('isaac.ready', envelope => appendCoreHomeLog('isaac.ready', { sequence: envelope.sequence, protocolVersion: envelope.version }));
  coreSupervisor.on('isaac.error', envelope => appendCoreHomeLog('isaac.error', { sequence: envelope.sequence, code: safeLogValue(envelope.payload?.code) }));
  coreSupervisor.on('event', envelope => {
    voiceOutput?.handleConversationEvent(envelope);
    broadcastIsaacEvent(envelope);
  });
  coreSupervisor.on('core-exit', result => appendCoreHomeLog('core.exit', result));
  coreSupervisor.on('unavailable', error => appendCoreHomeLog('core.unavailable', { message: safeLogValue(error?.message || error) }));
  coreSupervisor.start().then(async result => {
    appendCoreHomeLog('core.start-result', result);
    if (!result.ok) return;
    lanGateway = new LanGateway({ client: coreSupervisor.client, eventSource: coreSupervisor });
    const info = await lanGateway.start();
    appendCoreHomeLog('lan-gateway.ready', info);
    console.log(`[ISAAC phone] Pair on local Wi-Fi: ${info.pairingUrl}`);
  }).catch(error => appendCoreHomeLog('core.start-failed', { message: safeLogValue(error?.message || error) }));
});

app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (!coreSupervisor || coreQuitInProgress) return;
  event.preventDefault();
  coreQuitInProgress = true;
  Promise.resolve(voiceOutput?.shutdown()).catch(() => {})
    .then(() => lanGateway?.stop()).catch(() => {})
    .then(() => coreSupervisor.stop()).catch(() => {}).finally(() => app.quit());
});
app.on('will-quit', () => globalShortcut.unregisterAll());
