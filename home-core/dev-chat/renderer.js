'use strict';

const messages = document.getElementById('messages');
const status = document.getElementById('status');
const form = document.getElementById('composer');
const input = document.getElementById('input');
const send = document.getElementById('send');
const cancel = document.getElementById('cancel');
const memoryPanel = document.getElementById('memory-panel');
const memoryList = document.getElementById('memory-list');
const memoryQuery = document.getElementById('memory-query');
const voiceToggle = document.getElementById('voice-toggle');
const voiceStop = document.getElementById('voice-stop');
let conversationId = null;
let currentResponse = null;
let voiceEnabled = true;
let responseActive = false;
let completionRecoveryTimer = null;

function stopCompletionRecovery() {
  clearTimeout(completionRecoveryTimer);
  completionRecoveryTimer = null;
}

function startCompletionRecovery(targetConversationId) {
  stopCompletionRecovery();
  const reconcile = async () => {
    if (!responseActive || conversationId !== targetConversationId) return stopCompletionRecovery();
    try {
      const snapshot = await window.isaacDev.selectConversation(targetConversationId);
      const latest = snapshot?.messages?.at(-1);
      if (latest?.role === 'assistant') {
        if (currentResponse) currentResponse.textContent = latest.content || '';
        else addMessage('isaac', latest.content || '');
        currentResponse = null;
        responseActive = false;
        send.disabled = false;
        status.textContent = 'READY';
        input.focus();
        return stopCompletionRecovery();
      }
    } catch {}
    completionRecoveryTimer = setTimeout(reconcile, 1500);
  };
  completionRecoveryTimer = setTimeout(reconcile, 1500);
}

function addMessage(role, content = '') {
  const node = document.createElement('div');
  node.className = `message ${role}`;
  node.textContent = content;
  messages.appendChild(node);
  messages.scrollTop = messages.scrollHeight;
  return node;
}

function renderMemories(records) {
  memoryList.replaceChildren();
  for (const memory of records) {
    const entry = document.createElement('article');
    entry.className = 'memory-entry';
    const meta = document.createElement('small');
    meta.textContent = `${memory.type} • importance ${memory.importance} • accessed ${memory.accessCount}`;
    const editor = document.createElement('textarea');
    editor.value = memory.content;
    const actions = document.createElement('div');
    actions.className = 'memory-actions';
    const saveButton = document.createElement('button');
    saveButton.textContent = 'Save';
    saveButton.addEventListener('click', async () => {
      await window.isaacDev.updateMemory(memory.id, { content: editor.value });
      await loadMemories();
    });
    const deleteButton = document.createElement('button');
    deleteButton.textContent = 'Delete';
    deleteButton.addEventListener('click', async () => {
      await window.isaacDev.deleteMemory(memory.id);
      await loadMemories();
    });
    actions.append(saveButton, deleteButton);
    entry.append(meta, editor, actions);
    memoryList.append(entry);
  }
  if (!records.length) memoryList.textContent = 'No matching active memories.';
}

async function loadMemories(query = '') {
  try {
    const result = query
      ? await window.isaacDev.searchMemories(query)
      : await window.isaacDev.listMemories({ status: 'active', limit: 100 });
    renderMemories(result.memories || []);
  } catch (error) {
    memoryList.textContent = error.message;
  }
}

async function initialize() {
  try {
    const brain = await window.isaacDev.brainStatus();
    const voice = await window.isaacDev.voiceStatus();
    voiceEnabled = voice?.enabled !== false;
    voiceToggle.textContent = voiceEnabled ? 'Voice ON' : 'Voice OFF';
    status.textContent = String(brain.state || 'UNKNOWN').toUpperCase();
    const result = await window.isaacDev.bootstrapConversation();
    conversationId = result.conversationId;
    for (const message of result.messages || []) addMessage(message.role === 'assistant' ? 'isaac' : 'user', message.content);
    input.focus();
  } catch (error) {
    status.textContent = 'UNAVAILABLE';
    addMessage('isaac', error.message);
  }
}

window.isaacDev.onEvent(envelope => {
  if (envelope.conversationId && envelope.conversationId !== conversationId) return;
  if (envelope.type === 'isaac.thinking') status.textContent = 'THINKING';
  if (envelope.type === 'isaac.brain.ready') status.textContent = 'READY';
  if (envelope.type === 'isaac.response.started') {
    currentResponse = addMessage('isaac');
    responseActive = true;
    send.disabled = true;
  }
  if (envelope.type === 'isaac.response.delta' && currentResponse) {
    currentResponse.textContent += envelope.payload.delta;
    messages.scrollTop = messages.scrollHeight;
  }
  if (envelope.type === 'isaac.response.completed') {
    status.textContent = 'READY';
    currentResponse = null;
    responseActive = false;
    send.disabled = false;
    input.focus();
    stopCompletionRecovery();
  }
  if (envelope.type === 'isaac.voice.started') status.textContent = 'SPEAKING';
  if (envelope.type === 'isaac.voice.stopped' && status.textContent === 'SPEAKING') status.textContent = 'READY';
  if (envelope.type === 'isaac.voice.error') {
    status.textContent = 'VOICE ERROR';
    console.error('ISAAC voice error:', envelope.payload?.message);
  }
  if (envelope.type === 'isaac.response.cancelled') {
    status.textContent = 'CANCELLED';
    currentResponse = null;
    responseActive = false;
    send.disabled = false;
    stopCompletionRecovery();
  }
  if (envelope.type === 'isaac.error' && envelope.payload?.requestId) {
    status.textContent = 'ERROR';
    addMessage('isaac', envelope.payload.message || 'Generation failed');
    responseActive = false;
    send.disabled = false;
    stopCompletionRecovery();
  }
  if (envelope.type.startsWith('isaac.memory.') && !memoryPanel.hidden) loadMemories(memoryQuery.value.trim());
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content || !conversationId) return;
  addMessage('user', content);
  input.value = '';
  responseActive = true;
  send.disabled = true;
  const activeConversationId = conversationId;
  try {
    await window.isaacDev.sendMessage(conversationId, content);
    startCompletionRecovery(activeConversationId);
  } catch (error) {
    addMessage('isaac', error.message);
    responseActive = false;
    send.disabled = false;
    stopCompletionRecovery();
  }
});

cancel.addEventListener('click', () => conversationId && window.isaacDev.cancel(conversationId));
voiceToggle.addEventListener('click', async () => {
  const state = await window.isaacDev.setVoiceEnabled(!voiceEnabled);
  voiceEnabled = state.enabled;
  voiceToggle.textContent = voiceEnabled ? 'Voice ON' : 'Voice OFF';
});
voiceStop.addEventListener('click', () => window.isaacDev.stopVoice());
document.getElementById('memories').addEventListener('click', () => {
  memoryPanel.hidden = false;
  loadMemories();
});
document.getElementById('memory-close').addEventListener('click', () => { memoryPanel.hidden = true; });
document.getElementById('memory-refresh').addEventListener('click', () => { memoryQuery.value = ''; loadMemories(); });
document.getElementById('memory-search').addEventListener('click', () => loadMemories(memoryQuery.value.trim()));
input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});
initialize();
