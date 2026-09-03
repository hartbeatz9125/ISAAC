'use strict';

const conversation = document.getElementById('conversation');
const composer = document.getElementById('composer');
const input = document.getElementById('input');
const stateLabel = document.getElementById('state-label');
const inputStatus = document.getElementById('input-status');

let conversationId = null;
let currentResponse = null;
let requestActive = false;
let reactorState = 'idle';

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

function resizeInput() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 126)}px`;
}

async function connect() {
  try {
    const result = await window.desktopAPI.isaac.createConversation();
    if (!result?.conversationId) throw new Error('ISAAC Core is not ready');
    conversationId = result.conversationId;
    setState('idle', 'READY');
    input.disabled = false;
    input.focus();
  } catch {
    setState('idle', 'RECONNECTING');
    input.disabled = true;
    setTimeout(connect, 1200);
  }
}

window.desktopAPI.isaac.onEvent(envelope => {
  if (envelope.conversationId && envelope.conversationId !== conversationId) return;
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
  }
  if (envelope.type === 'isaac.voice.started') {
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
  }
  if (envelope.type === 'isaac.error' && envelope.payload?.requestId) {
    addMessage('isaac', envelope.payload?.message || 'Something went wrong.');
    currentResponse = null;
    requestActive = false;
    setState('idle', 'ERROR');
    inputStatus.textContent = 'ENTER TO SEND';
  }
});

composer.addEventListener('submit', async event => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content || !conversationId || requestActive) return;
  addMessage('kenneth', content);
  input.value = '';
  resizeInput();
  requestActive = true;
  setState('thinking', 'THINKING');
  inputStatus.textContent = 'GENERATING';
  try {
    await window.desktopAPI.isaac.sendMessage(conversationId, content);
  } catch (error) {
    addMessage('isaac', error.message || 'ISAAC Core is unavailable.');
    requestActive = false;
    setState('idle', 'UNAVAILABLE');
    inputStatus.textContent = 'ENTER TO SEND';
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
