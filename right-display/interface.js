'use strict';

const conversation = document.getElementById('conversation');
const composer = document.getElementById('composer');
const input = document.getElementById('input');
const stateLabel = document.getElementById('state-label');
const inputStatus = document.getElementById('input-status');
const chatPicker = document.getElementById('chat-picker');
const chatTitle = document.getElementById('chat-title');
const chatDrawer = document.getElementById('chat-drawer');
const chatList = document.getElementById('chat-list');

let conversationId = null;
let currentResponse = null;
let requestActive = false;
let reactorState = 'idle';
let completionRecoveryTimer = null;

function stopCompletionRecovery() {
  clearTimeout(completionRecoveryTimer);
  completionRecoveryTimer = null;
}

function finishRecoveredResponse(content) {
  if (currentResponse) currentResponse.textContent = content;
  else addMessage('isaac', content);
  currentResponse = null;
  requestActive = false;
  if (reactorState !== 'speaking') setState('idle', 'READY');
  inputStatus.textContent = 'ENTER TO SEND';
  input.focus();
  stopCompletionRecovery();
}

function startCompletionRecovery(targetConversationId) {
  stopCompletionRecovery();
  const reconcile = async () => {
    if (!requestActive || conversationId !== targetConversationId) return stopCompletionRecovery();
    try {
      const snapshot = await window.desktopAPI.isaac.selectConversation(targetConversationId);
      const latest = snapshot?.messages?.at(-1);
      if (latest?.role === 'assistant') return finishRecoveredResponse(latest.content || '');
    } catch {}
    completionRecoveryTimer = setTimeout(reconcile, 1500);
  };
  completionRecoveryTimer = setTimeout(reconcile, 1500);
}

function setState(state, label = state.toUpperCase()) {
  reactorState = state;
  document.body.dataset.isaacState = state;
  stateLabel.textContent = label;
  window.dispatchEvent(new CustomEvent('isaac-state', { detail: state }));
}

function addMessage(role, content = '') {
  const message = document.createElement('div');
  message.className = `message ${role}`;
  message.textContent = content;
  conversation.appendChild(message);
  conversation.scrollTop = conversation.scrollHeight;
  return message;
}

function showConversation(result) {
  stopCompletionRecovery();
  conversationId = result.conversationId;
  chatTitle.textContent = result.title || 'New Chat';
  conversation.replaceChildren();
  for (const message of result.messages || []) addMessage(message.role === 'assistant' ? 'isaac' : 'kenneth', message.content);
  currentResponse = null;
  requestActive = false;
  input.disabled = false;
  inputStatus.textContent = 'ENTER TO SEND';
  chatDrawer.hidden = true;
  chatPicker.setAttribute('aria-expanded', 'false');
  input.focus();
}

function beginRename(row, chat) {
  const editor = document.createElement('input');
  let finished = false;
  editor.className = 'chat-rename';
  editor.value = chat.title;
  editor.maxLength = 80;
  const finish = async save => {
    if (finished) return;
    finished = true;
    const title = editor.value.trim();
    if (save && title) {
      await window.desktopAPI.isaac.renameConversation(chat.conversationId, title);
      if (chat.conversationId === conversationId) chatTitle.textContent = title;
    }
    await loadChats();
  };
  editor.addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); finish(true); }
    if (event.key === 'Escape') { event.preventDefault(); finish(false); }
  });
  editor.addEventListener('blur', () => finish(true), { once: true });
  row.firstElementChild.replaceWith(editor);
  editor.focus();
  editor.select();
}

async function loadChats() {
  const result = await window.desktopAPI.isaac.listConversations();
  chatList.replaceChildren();
  for (const chat of result.conversations || []) {
    const row = document.createElement('div'); row.className = 'chat-entry';
    const select = document.createElement('button'); select.textContent = chat.title;
    select.onclick = async () => { if (requestActive) return; showConversation(await window.desktopAPI.isaac.selectConversation(chat.conversationId)); chatDrawer.hidden = true; };
    const rename = document.createElement('button'); rename.className = 'mini'; rename.textContent = '✎';
    rename.onclick = () => beginRename(row, chat);
    const remove = document.createElement('button'); remove.className = 'mini'; remove.textContent = '×';
    remove.onclick = async () => { if (!confirm(`Delete “${chat.title}”?`)) return; showConversation(await window.desktopAPI.isaac.deleteConversation(chat.conversationId)); await loadChats(); };
    row.append(select, rename, remove); chatList.append(row);
  }
}

function resizeInput() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 126)}px`;
}

async function connect() {
  try {
    const result = await window.desktopAPI.isaac.bootstrapConversation();
    if (!result?.conversationId) throw new Error('ISAAC Core is not ready');
    showConversation(result);
    const brain = await window.desktopAPI.isaac.brainStatus();
    const ready = brain?.state === 'ready';
    setState('idle', ready ? 'READY' : 'LOADING');
    input.disabled = !ready;
    if (ready) input.focus();
  } catch {
    setState('idle', 'RECONNECTING');
    input.disabled = true;
    setTimeout(connect, 1200);
  }
}

window.desktopAPI.isaac.onEvent(envelope => {
  if (envelope.conversationId && envelope.conversationId !== conversationId) return;
  if (envelope.type === 'isaac.brain.ready') {
    requestActive = false;
    input.disabled = false;
    inputStatus.textContent = 'ENTER TO SEND';
    setState('idle', 'READY');
    input.focus();
  }
  if (envelope.type === 'isaac.thinking' || envelope.type === 'isaac.response.started') {
    setState('thinking', 'THINKING');
  }
  if (envelope.type === 'isaac.response.started') {
    currentResponse = addMessage('isaac');
    requestActive = true;
    inputStatus.textContent = 'GENERATING';
  }
  if (envelope.type === 'isaac.response.delta' && currentResponse) {
    currentResponse.textContent += envelope.payload?.delta || '';
    conversation.scrollTop = conversation.scrollHeight;
  }
  if (envelope.type === 'isaac.response.completed') {
    if (currentResponse && !currentResponse.textContent && envelope.payload?.content) currentResponse.textContent = envelope.payload.content;
    currentResponse = null;
    requestActive = false;
    if (reactorState !== 'speaking') setState('idle', 'READY');
    inputStatus.textContent = 'ENTER TO SEND';
    input.focus();
    stopCompletionRecovery();
  }
  if (envelope.type === 'isaac.voice.started') {
    window.dispatchEvent(new CustomEvent('isaac-voice-envelope', { detail: envelope.payload }));
    setState('speaking', 'SPEAKING');
  }
  if (envelope.type === 'isaac.voice.stopped' && reactorState === 'speaking') {
    setState('idle', 'READY');
  }
  if (envelope.type === 'isaac.response.cancelled') {
    currentResponse = null;
    requestActive = false;
    setState('idle', 'CANCELLED');
    inputStatus.textContent = 'ENTER TO SEND';
    stopCompletionRecovery();
  }
  if (envelope.type === 'isaac.error' && envelope.payload?.requestId) {
    addMessage('isaac', envelope.payload?.message || 'Something went wrong.');
    currentResponse = null;
    requestActive = false;
    setState('idle', 'ERROR');
    inputStatus.textContent = 'ENTER TO SEND';
    stopCompletionRecovery();
  }
});

composer.addEventListener('submit', async event => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content || !conversationId || requestActive) return;
  addMessage('kenneth', content);
  if (chatTitle.textContent === 'New Chat') chatTitle.textContent = content.replace(/\s+/g, ' ').slice(0, 57) + (content.replace(/\s+/g, ' ').length > 57 ? '…' : '');
  input.value = '';
  resizeInput();
  requestActive = true;
  const activeConversationId = conversationId;
  setState('thinking', 'THINKING');
  inputStatus.textContent = 'GENERATING';
  try {
    await window.desktopAPI.isaac.sendMessage(conversationId, content);
    startCompletionRecovery(activeConversationId);
  } catch (error) {
    addMessage('isaac', error.message || 'ISAAC Core is unavailable.');
    requestActive = false;
    setState('idle', 'UNAVAILABLE');
    inputStatus.textContent = 'ENTER TO SEND';
    stopCompletionRecovery();
  }
});

input.addEventListener('input', resizeInput);
input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

connect();

chatPicker.addEventListener('click', async () => { chatDrawer.hidden = !chatDrawer.hidden; chatPicker.setAttribute('aria-expanded', String(!chatDrawer.hidden)); if (!chatDrawer.hidden) await loadChats(); });
document.getElementById('new-chat').addEventListener('click', async () => { if (requestActive) return; showConversation(await window.desktopAPI.isaac.createConversation()); chatDrawer.hidden = true; await loadChats(); input.focus(); });
composer.addEventListener('pointerdown', () => { chatDrawer.hidden = true; chatPicker.setAttribute('aria-expanded', 'false'); });

let voiceEnabled = true;
const voiceToggle = document.getElementById('voice-toggle');
function updateVoiceControl(enabled) {
  voiceEnabled = enabled;
  voiceToggle.setAttribute('aria-pressed', String(enabled));
  voiceToggle.title = enabled ? 'Voice on — click to mute' : 'Voice muted — click to enable';
}
window.desktopAPI.isaac.voiceStatus().then(status => updateVoiceControl(status?.enabled !== false)).catch(() => {});
voiceToggle.addEventListener('click', async () => {
  try { const status = await window.desktopAPI.isaac.setVoiceEnabled(!voiceEnabled); updateVoiceControl(status.enabled); }
  catch { inputStatus.textContent = 'VOICE UNAVAILABLE'; }
});
document.getElementById('stop-response').addEventListener('click', async () => {
  try {
    await window.desktopAPI.isaac.stopVoice();
    if (conversationId && requestActive) await window.desktopAPI.isaac.cancel(conversationId);
    else setState('idle', 'READY');
  } catch { inputStatus.textContent = 'STOP UNAVAILABLE'; }
});
