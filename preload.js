const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('desktopAPI', {
  launchGame: game => ipcRenderer.invoke('game:launch', game),
  isaac: {
    createConversation: () => ipcRenderer.invoke('isaac:conversation:create'),
    sendMessage: (conversationId, content) => ipcRenderer.invoke('isaac:conversation:message', { conversationId, content }),
    cancel: conversationId => ipcRenderer.invoke('isaac:conversation:cancel', { conversationId }),
    onEvent: callback => {
      const listener = (_event, envelope) => callback(envelope);
      ipcRenderer.on('isaac:event', listener);
      return () => ipcRenderer.removeListener('isaac:event', listener);
    }
  },
  spotify: {
    status: () => ipcRenderer.invoke('spotify:status'),
    connect: () => ipcRenderer.invoke('spotify:connect'),
    disconnect: () => ipcRenderer.invoke('spotify:disconnect'),
    openCurrent: destination => ipcRenderer.invoke('spotify:open-current', destination)
  }
});
