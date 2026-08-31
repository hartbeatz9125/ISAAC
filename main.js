const { app, BrowserWindow, screen, globalShortcut, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const { SpotifyService } = require('./spotify-service');

app.commandLine.appendSwitch('high-dpi-support', '1');
app.commandLine.appendSwitch('force-device-scale-factor', '1');

let windows = [];
let spotifyService;

function safeLogValue(value) { return String(value ?? '').replace(/[\r\n]+/g, ' ').slice(0, 1000); }
function appendLaunchLog(entry) {
  const logDir = path.join(app.getPath('userData'), 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  const logPath = path.join(logDir, 'game-launch.log');
  fs.appendFileSync(logPath, JSON.stringify(entry) + '\n', 'utf8');
  return logPath;
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

app.whenReady().then(() => {
  ipcMain.handle('game:launch', (_event, game) => launchGame(game));
  spotifyService = new SpotifyService({ app, shell });
  ipcMain.handle('spotify:status', () => spotifyService.getStatus());
  ipcMain.handle('spotify:connect', () => spotifyService.connect());
  ipcMain.handle('spotify:disconnect', () => spotifyService.disconnect());
  ipcMain.handle('spotify:open-current', (_event, destination) => spotifyService.openCurrent(destination));
  if (process.argv.includes('--review')) {
    createReviewWindow();
  } else {
    const displays = orderedDisplays();
    createDisplayWindow(displays[0], 'left');
    createDisplayWindow(displays[1] || displays[0], 'right');
  }

  globalShortcut.register('CommandOrControl+Shift+Q', () => app.quit());
  globalShortcut.register('CommandOrControl+Shift+R', () => windows.forEach(w => w.reload()));
});

app.on('window-all-closed', () => app.quit());
app.on('will-quit', () => globalShortcut.unregisterAll());
