// UI for the Jarvis desktop app. Python pushes events through window.ui.handle(...)
// and the UI calls Python through window.pywebview.api.

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview?.api;

const STATUS = {
  idle: 'Ready',
  listening: 'Listening…',
  transcribing: 'Got it…',
  thinking: 'Thinking…',
  speaking: 'Speaking',
};

// ---------- Orb ----------
// Three soft blobs drawn with additive blending. Their shape wobbles with the
// microphone level while listening and with the voice level while speaking.

const PALETTES = {
  idle: [[226, 90, 62], [258, 85, 66], [192, 95, 60]],
  listening: [[190, 100, 60], [210, 100, 62], [172, 90, 55]],
  transcribing: [[250, 90, 66], [280, 85, 64], [210, 95, 62]],
  thinking: [[265, 90, 66], [295, 80, 62], [225, 95, 64]],
  speaking: [[215, 100, 62], [190, 100, 58], [250, 90, 66]],
  error: [[350, 95, 62], [10, 90, 60], [330, 85, 60]],
};

const orb = {
  canvas: $('orb'),
  state: 'idle',
  level: 0,        // smoothed audio level 0..1
  target: 0,       // latest audio level
  colors: PALETTES.idle.map((c) => [...c]),
  spin: 0,
  t: 0,

  setState(state) { this.state = state; },
  setLevel(value) { this.target = Math.max(0, Math.min(1, value)); },

  frame() {
    const { canvas } = this;
    const dpr = window.devicePixelRatio || 1;
    const size = canvas.clientWidth * dpr;
    if (canvas.width !== size) { canvas.width = size; canvas.height = size; }
    const ctx = canvas.getContext('2d');
    const c = size / 2;
    this.t += 1 / 60;

    // Ease level and colors towards their targets.
    const active = this.state === 'listening' || this.state === 'speaking';
    const goal = active ? this.target : 0;
    this.level += (goal - this.level) * (goal > this.level ? 0.35 : 0.08);
    const palette = PALETTES[this.state] || PALETTES.idle;
    this.colors.forEach((col, i) => col.forEach((v, j) => { col[j] += (palette[i][j] - v) * 0.05; }));

    const thinking = this.state === 'thinking' || this.state === 'transcribing';
    this.spin += thinking ? 0.035 : 0.004;
    const breathe = Math.sin(this.t * (thinking ? 3 : 1.2)) * 0.02;
    const radius = c * (0.5 + breathe + this.level * 0.12);

    ctx.clearRect(0, 0, size, size);

    // Outer glow
    const glow = ctx.createRadialGradient(c, c, radius * 0.4, c, c, c);
    const [h, s, l] = this.colors[0];
    glow.addColorStop(0, `hsla(${h}, ${s}%, ${l}%, ${0.32 + this.level * 0.3})`);
    glow.addColorStop(1, 'hsla(0, 0%, 0%, 0)');
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, size, size);

    ctx.globalCompositeOperation = 'lighter';
    this.colors.forEach(([hue, sat, lig], i) => {
      const wobble = 0.05 + this.level * 0.22 + (thinking ? 0.05 : 0);
      ctx.beginPath();
      for (let a = 0; a <= Math.PI * 2 + 0.01; a += Math.PI / 90) {
        const n = Math.sin(a * 3 + this.t * (1.1 + i * 0.4) + i * 2)
          + Math.sin(a * 5 - this.t * (0.8 + i * 0.3) + i) * 0.6
          + Math.sin(a * 2 + this.spin * (i + 1)) * 0.8;
        const r = radius * (0.88 + i * 0.05) * (1 + n * wobble * 0.33);
        const x = c + Math.cos(a + this.spin * (i % 2 ? -1 : 1)) * r;
        const y = c + Math.sin(a + this.spin * (i % 2 ? -1 : 1)) * r;
        if (a === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      const g = ctx.createRadialGradient(c - radius * 0.3, c - radius * 0.35, radius * 0.1, c, c, radius * 1.15);
      g.addColorStop(0, `hsla(${hue}, ${sat}%, ${Math.min(lig + 8, 80)}%, 0.55)`);
      g.addColorStop(0.6, `hsla(${hue}, ${sat}%, ${lig - 6}%, 0.32)`);
      g.addColorStop(1, `hsla(${hue}, ${sat}%, ${lig - 20}%, 0)`);
      ctx.fillStyle = g;
      ctx.fill();
    });

    // Bright core
    const core = ctx.createRadialGradient(c, c, 0, c, c, radius * 0.55);
    core.addColorStop(0, `rgba(255, 255, 255, ${0.06 + this.level * 0.3})`);
    core.addColorStop(1, 'rgba(255, 255, 255, 0)');
    ctx.fillStyle = core;
    ctx.beginPath();
    ctx.arc(c, c, radius * 0.6, 0, Math.PI * 2);
    ctx.fill();

    // Orbiting particles while thinking
    if (thinking) {
      for (let i = 0; i < 14; i++) {
        const a = this.spin * 2.2 + (i / 14) * Math.PI * 2;
        const r = radius * (1.18 + Math.sin(this.t * 2 + i) * 0.04);
        ctx.fillStyle = `hsla(${this.colors[1][0]}, 90%, 75%, ${0.25 + 0.6 * ((i + 1) / 14)})`;
        ctx.beginPath();
        ctx.arc(c + Math.cos(a) * r, c + Math.sin(a) * r, size * 0.006 * ((i + 4) / 14), 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalCompositeOperation = 'source-over';
    requestAnimationFrame(() => this.frame());
  },
};

// ---------- Sounds ----------

let audioCtx;
function chime(up = true) {
  try {
    audioCtx ??= new AudioContext();
    const now = audioCtx.currentTime;
    [0, 0.09].forEach((delay, i) => {
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.value = up ? [660, 990][i] : [740, 494][i];
      gain.gain.setValueAtTime(0, now + delay);
      gain.gain.linearRampToValueAtTime(0.08, now + delay + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + delay + 0.25);
      osc.connect(gain).connect(audioCtx.destination);
      osc.start(now + delay);
      osc.stop(now + delay + 0.3);
    });
  } catch { /* audio not allowed yet */ }
}

// ---------- Chat ----------

const messages = $('messages');
let current = null;   // assistant bubble being streamed
let toolsRow = null;  // chips of tools used in the current reply
let info = { name: 'Jarvis', wakeWord: true, wakeReady: false, muted: false, hasKey: true };
let state = 'idle';

function scrollDown() { messages.scrollTop = messages.scrollHeight; }

function addMessage(kind, text) {
  messages.querySelector('.empty')?.remove();
  const el = document.createElement('div');
  el.className = `msg ${kind}`;
  el.textContent = text;
  messages.append(el);
  scrollDown();
  return el;
}

function toast(text) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = text;
  $('toasts').append(el);
  setTimeout(() => el.remove(), 4000);
}

function renderHint() {
  const hint = $('hint');
  if (state === 'idle') {
    const wake = info.wakeWord && info.wakeReady ? `Say “Hey ${info.name}” or press ` : 'Press ';
    hint.innerHTML = `${wake}<kbd>Space</kbd>`;
  } else if (state === 'listening') {
    hint.textContent = 'Speak now — I’ll stop when you pause';
  } else if (state === 'speaking' || state === 'thinking') {
    hint.innerHTML = '<kbd>Esc</kbd> to stop';
  } else {
    hint.textContent = '';
  }
}

function setState(next) {
  if (next === 'listening' && state !== 'listening') chime(true);
  state = next;
  orb.setState(next);
  $('status').textContent = STATUS[next] || next;
  $('micBtn').classList.toggle('active', next !== 'idle');
  if (next !== 'thinking') $('tool').hidden = true;
  renderHint();
}

function applyInfo(next) {
  info = { ...info, ...next };
  $('name').textContent = info.name;
  document.title = info.name;
  $('model').textContent = info.model || '';
  $('wakeBtn').classList.toggle('on', info.wakeWord);
  $('wakeBtn').title = `Listen for “Hey ${info.name}”`;
  $('muteBtn').classList.toggle('on', !info.muted);
  if (!info.hasKey) {
    addMessage('error', 'No OpenRouter API key found. Copy .env.example to .env, add OPENROUTER_API_KEY and restart.');
  }
  renderHint();
}

// ---------- Events from Python ----------

window.ui = {
  handle(event) {
    switch (event.type) {
      case 'info': applyInfo(event); break;
      case 'state': setState(event.state); break;
      case 'level': orb.setLevel(event.value); break;
      case 'user': addMessage('user', event.text); break;
      case 'assistant_start':
        current = null;
        toolsRow = null;
        break;
      case 'assistant_delta':
        if (!current) {
          current = addMessage('assistant typing', '');
        }
        current.textContent += event.text;
        scrollDown();
        break;
      case 'assistant_end':
        if (current) {
          current.textContent = current.textContent.trim();
          current.classList.remove('typing');
        }
        current = null;
        break;
      case 'tool': {
        const chip = $('tool');
        chip.textContent = event.label;
        chip.hidden = false;
        if (!toolsRow) {
          messages.querySelector('.empty')?.remove();
          toolsRow = document.createElement('div');
          toolsRow.className = 'tools-used';
          messages.append(toolsRow);
        }
        const tag = document.createElement('span');
        tag.textContent = event.label;
        toolsRow.append(tag);
        current?.classList.remove('typing');
        current = null; // text after the tool goes into a new bubble
        scrollDown();
        break;
      }
      case 'toast': toast(event.message); break;
      case 'error':
        orb.setState('error');
        setTimeout(() => orb.setState(state), 1200);
        addMessage('error', event.message);
        break;
    }
  },
};

// ---------- Actions ----------

function talk() {
  if (state === 'idle') api()?.listen();
  else api()?.stop();
}

function send(text) {
  if (!text.trim()) return;
  api()?.send_text(text);
}

$('micBtn').addEventListener('click', talk);
$('composer').addEventListener('submit', (e) => {
  e.preventDefault();
  send($('input').value);
  $('input').value = '';
});
messages.addEventListener('click', (e) => {
  const button = e.target.closest('.suggestions button');
  if (button) send(button.textContent);
});
$('wakeBtn').addEventListener('click', () => {
  applyInfo({ wakeWord: !info.wakeWord });
  api()?.set_wake_word(info.wakeWord);
});
$('muteBtn').addEventListener('click', () => {
  applyInfo({ muted: !info.muted });
  api()?.set_muted(info.muted);
});
$('clearBtn').addEventListener('click', () => {
  api()?.clear();
  messages.innerHTML = '';
  toast('Started a new conversation');
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { api()?.stop(); return; }
  if (e.code === 'Space' && document.activeElement !== $('input')) {
    e.preventDefault();
    if (!e.repeat) talk();
  }
});

window.addEventListener('pywebviewready', async () => {
  applyInfo(await api().info());
  setState('idle');
});

setState('idle');
orb.frame();
