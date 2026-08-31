const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('desktopAPI', {
  launchGame: game => ipcRenderer.invoke('game:launch', game),
  spotify: {
    status: () => ipcRenderer.invoke('spotify:status'),
    connect: () => ipcRenderer.invoke('spotify:connect'),
    disconnect: () => ipcRenderer.invoke('spotify:disconnect'),
    openCurrent: destination => ipcRenderer.invoke('spotify:open-current', destination)
  }
});
