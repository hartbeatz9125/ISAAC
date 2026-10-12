(() => {
  'use strict';
  const root = document.documentElement;
  const wave = document.getElementById('voice-wave');
  const caption = document.getElementById('presence-label');
  const motion = document.getElementById('motion-toggle');
  const bars = Array.from({ length: 22 }, () => {
    const bar = document.createElement('i'); wave.appendChild(bar); return bar;
  });
  let state = 'idle', stateAt = performance.now(), wakeUntil = performance.now() + 4000;
  let envelope = [], stepMs = 50, audioStart = 0;
  let reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let previous = 0;
  function motionSetting() {
    document.body.classList.toggle('reduced-motion', reduced);
    motion.textContent = reduced ? 'Motion off' : 'Motion on';
    motion.setAttribute('aria-pressed', String(!reduced));
  }
  motion.addEventListener('click', () => { reduced = !reduced; motionSetting(); });
  motionSetting();
  window.addEventListener('isaac-voice-envelope', event => {
    const data = event.detail || {};
    envelope = Array.isArray(data.amplitudeEnvelope) ? data.amplitudeEnvelope : [];
    stepMs = Math.max(10, Number(data.envelopeStepMs) || 50);
    audioStart = Number(data.audibleStartUnixMs) || Date.now();
  });
  window.addEventListener('isaac-state', event => {
    state = event.detail; stateAt = performance.now();
    caption.textContent = state === 'speaking' ? 'SPEAKING' : state === 'thinking' ? 'THINKING' : 'AT HOME';
    if (state !== 'speaking') envelope = [];
  });
  document.getElementById('input').addEventListener('focus', () => { wakeUntil = performance.now() + 3500; });
  function frame(now) {
    requestAnimationFrame(frame);
    if (reduced || document.hidden || now - previous < 33) return;
    previous = now;
    const t = (now - stateAt) / 1000;
    let energy = .025, headlights = .04;
    if (now < wakeUntil) headlights = .15 + .35 * Math.sin(Math.PI * Math.min(1, (wakeUntil - now) / 4000));
    if (state === 'thinking') { energy = .12 + .07 * Math.sin(t * 2.5); headlights = .22; }
    if (state === 'speaking') {
      const index = Math.floor((Date.now() - audioStart) / stepMs);
      const level = envelope[index];
      energy = Number.isFinite(level) ? Math.max(0, Math.min(1, level)) : .06;
      headlights = .3;
    }
    root.style.setProperty('--voice-energy', energy.toFixed(3));
    root.style.setProperty('--headlight-energy', headlights.toFixed(3));
    bars.forEach((bar, i) => { bar.style.height = (state === 'speaking' ? 3 + energy * 24 * (.3 + .7 * Math.abs(Math.sin(i * .64))) : 3) + 'px'; });
  }
  requestAnimationFrame(frame);
})();
