const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('desktopAPI', {
  launchGame: game => ipcRenderer.invoke('game:launch', game),
  isaac: {
    createConversation: () => ipcRenderer.invoke('isaac:conversation:create'),
    bootstrapConversation: () => ipcRenderer.invoke('isaac:conversation:bootstrap'),
    listConversations: () => ipcRenderer.invoke('isaac:conversation:list'),
    selectConversation: conversationId => ipcRenderer.invoke('isaac:conversation:select', { conversationId }),
    renameConversation: (conversationId, title) => ipcRenderer.invoke('isaac:conversation:rename', { conversationId, title }),
    deleteConversation: conversationId => ipcRenderer.invoke('isaac:conversation:delete', { conversationId }),
    brainStatus: () => ipcRenderer.invoke('isaac:brain:status'),
    sendMessage: (conversationId, content) => ipcRenderer.invoke('isaac:conversation:message', { conversationId, content }),
    cancel: conversationId => ipcRenderer.invoke('isaac:conversation:cancel', { conversationId }),
    voiceStatus: () => ipcRenderer.invoke('isaac:voice:status'),
    setVoiceEnabled: enabled => ipcRenderer.invoke('isaac:voice:enabled', enabled),
    stopVoice: () => ipcRenderer.invoke('isaac:voice:stop'),
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
