'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('isaacDev', {
  createConversation: () => ipcRenderer.invoke('isaac:conversation:create'),
  sendMessage: (conversationId, content) => ipcRenderer.invoke('isaac:conversation:message', { conversationId, content }),
  cancel: conversationId => ipcRenderer.invoke('isaac:conversation:cancel', { conversationId }),
  brainStatus: () => ipcRenderer.invoke('isaac:brain:status'),
  memoryStatus: () => ipcRenderer.invoke('isaac:memory:status'),
  listMemories: filters => ipcRenderer.invoke('isaac:memory:list', filters || {}),
  searchMemories: query => ipcRenderer.invoke('isaac:memory:search', { query }),
  getMemory: id => ipcRenderer.invoke('isaac:memory:get', { id }),
  createMemory: memory => ipcRenderer.invoke('isaac:memory:create', memory),
  updateMemory: (id, changes) => ipcRenderer.invoke('isaac:memory:update', { id, changes }),
  deleteMemory: id => ipcRenderer.invoke('isaac:memory:delete', { id }),
  voiceStatus: () => ipcRenderer.invoke('isaac:voice:status'),
  setVoiceEnabled: enabled => ipcRenderer.invoke('isaac:voice:enabled', enabled),
  stopVoice: () => ipcRenderer.invoke('isaac:voice:stop'),
  onEvent: callback => {
    const listener = (_event, envelope) => callback(envelope);
    ipcRenderer.on('isaac:event', listener);
    return () => ipcRenderer.removeListener('isaac:event', listener);
  }
});
