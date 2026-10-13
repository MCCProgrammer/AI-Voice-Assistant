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
  hud: false,      // futuristic rings around the orb (mini mode)

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

    if (this.hud) this.drawHud(ctx, c, size);

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

  // Rotating HUD rings: a dashed outer ring, tick marks and three arcs that stretch with the audio level.
  drawHud(ctx, c, size) {
    const hue = this.colors[2][0];
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    ctx.lineCap = 'round';

    ctx.lineWidth = size * 0.004;
    ctx.strokeStyle = `hsla(${hue}, 100%, 72%, 0.45)`;
    ctx.setLineDash([size * 0.018, size * 0.014]);
    ctx.beginPath();
    ctx.arc(c, c, c * 0.88, this.spin * 1.5, this.spin * 1.5 + Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);

    for (let i = 0; i < 72; i++) {
      const a = (i / 72) * Math.PI * 2 - this.spin * 0.5;
      const major = i % 6 === 0;
      const r1 = c * 0.93;
      const r2 = c * (major ? 0.985 : 0.955);
      ctx.strokeStyle = `hsla(${hue}, 90%, 78%, ${major ? 0.55 : 0.18})`;
      ctx.lineWidth = size * (major ? 0.004 : 0.0025);
      ctx.beginPath();
      ctx.moveTo(c + Math.cos(a) * r1, c + Math.sin(a) * r1);
      ctx.lineTo(c + Math.cos(a) * r2, c + Math.sin(a) * r2);
      ctx.stroke();
    }

    ctx.shadowColor = `hsla(${this.colors[0][0]}, 100%, 65%, 0.9)`;
    ctx.shadowBlur = size * 0.03;
    ctx.lineWidth = size * 0.009;
    ctx.strokeStyle = `hsla(${this.colors[0][0]}, 100%, 74%, 0.9)`;
    for (let i = 0; i < 3; i++) {
      const start = -this.spin * 2.4 + (i * Math.PI * 2) / 3;
      ctx.beginPath();
      ctx.arc(c, c, c * 0.79, start, start + Math.PI / 5 + this.level * 0.9);
      ctx.stroke();
    }
    ctx.restore();
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
  if (kind === 'user') {
    // Misheard? Click your message to fix it and send it again.
    el.title = 'Click to edit and resend';
    el.addEventListener('click', () => {
      $('input').value = el.textContent;
      $('input').focus();
    });
  }
  messages.append(el);
  scrollDown();
  return el;
}

const isMini = () => document.body.classList.contains('mini');

// Resizes the floating window to fit the orb, caption and card.
function fitMini() {
  if (!isMini()) return;
  requestAnimationFrame(() => {
    const bottom = document.querySelector('.mini-hud').getBoundingClientRect().bottom;
    api()?.fit_mini(Math.ceil(bottom + 18));
  });
}

let cardTimer;
function showMiniCard(card) {
  clearTimeout(cardTimer);
  const el = card ? renderCard(card, true) : null;
  $('miniCard').replaceChildren(...(el ? [el] : []));
  fitMini();
}

function toast(text) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = text;
  $('toasts').append(el);
  setTimeout(() => el.remove(), 4000);
}

// ---------- Mini mode captions ----------

let captionTimer;
function caption(text, kind) {
  const el = $('caption');
  clearTimeout(captionTimer);
  el.className = `caption show ${kind}`;
  el.textContent = text;
}

function captionAppend(delta) {
  const el = $('caption');
  if (!el.classList.contains('assistant')) caption('', 'assistant');
  let text = el.textContent + delta;
  if (text.length > 190) text = `…${text.slice(-180).replace(/^\S*\s/, '')}`; // keep the latest words
  el.textContent = text;
}

function fadeCaption(delay = 7000) {
  clearTimeout(captionTimer);
  captionTimer = setTimeout(() => $('caption').classList.remove('show'), delay);
  clearTimeout(cardTimer);
  cardTimer = setTimeout(() => showMiniCard(null), delay * 3);
}

function renderHint() {
  const hint = $('hint');
  if (state === 'idle') {
    const wake = info.wakeWord && info.wakeReady ? `Say “Hey ${info.name}” or press ` : 'Press ';
    hint.innerHTML = `${wake}<kbd>Space</kbd>`;
  } else if (state === 'listening') {
    hint.innerHTML = followUp ? 'Answer when you’re ready — or say “never mind”' : 'Take your time — I’ll wait for you to finish';
  } else if (state === 'speaking' || state === 'thinking') {
    hint.innerHTML = '<kbd>Esc</kbd> to stop';
  } else {
    hint.textContent = '';
  }
}

let followUp = false;
function setState(next, extra = {}) {
  if (next === 'listening' && state !== 'listening') chime(true);
  state = next;
  followUp = next === 'listening' && !!extra.followUp;
  orb.setState(next);
  $('status').textContent = followUp ? 'Waiting for your answer…' : (STATUS[next] || next);
  $('micBtn').classList.toggle('active', next !== 'idle');
  $('miniStatus').textContent = next === 'idle'
    ? (info.wakeWord && info.wakeReady ? `Say “Hey ${info.name}”` : 'Tap the orb to talk')
    : (followUp ? 'Your answer' : (STATUS[next] || next).replace('…', ''));
  if (next === 'idle') fadeCaption();
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
  if (next.reminders) renderReminders(info.reminders);
  if (next.shortcuts) renderSkills(info.shortcuts);
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
      case 'state': setState(event.state, event); break;
      case 'data':
        info = { ...info, ...event };
        renderReminders(event.reminders);
        renderSkills(event.shortcuts);
        break;
      case 'level': orb.setLevel(event.value); break;
      case 'user':
        addMessage('user', event.text);
        caption(`“${event.text}”`, 'user');
        showMiniCard(null);
        break;
      case 'card': {
        const el = renderCard(event.card);
        if (el) {
          messages.querySelector('.empty')?.remove();
          messages.append(el);
          scrollDown();
        }
        showMiniCard(event.card);
        break;
      }
      case 'assistant_start':
        current = null;
        toolsRow = null;
        break;
      case 'assistant_delta':
        if (!current) {
          current = addMessage('assistant typing', '');
        }
        current.textContent += event.text;
        captionAppend(event.text);
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
        $('miniStatus').textContent = event.label;
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
        caption(event.message, 'error');
        break;
    }
  },
};

// ---------- Actions ----------

function talk() {
  if (state === 'idle') api()?.listen();
  else api()?.stop();
}

// ---------- Tabs: Chat · Reminders · Skills ----------

function showTab(name) {
  document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('[data-panel]').forEach((p) => { p.hidden = p.dataset.panel !== name; });
}
document.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));

const timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' });
function relative(date) {
  const minutes = Math.round((date - Date.now()) / 60000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return `in ${minutes} min`;
  if (minutes < 60 * 24) return `in ${Math.round(minutes / 60)} h`;
  return `in ${Math.round(minutes / 1440)} days`;
}

function renderReminders(items = []) {
  const count = $('reminderCount');
  count.textContent = items.length;
  count.hidden = !items.length;
  const panel = $('reminders');
  if (!items.length) {
    panel.replaceChildren(h('div', { class: 'panel-empty' },
      h('div', { class: 'panel-empty-icon' }, '🔔'),
      h('strong', {}, 'No reminders yet'),
      h('p', {}, 'Ask me to remind you of something, at a time or on a day.'),
      ...['Remind me to call mum tomorrow at 6pm', 'Remind me to take out the bins on Thursday at 8am']
        .map((t) => h('button', { type: 'button', class: 'example', onclick: () => { showTab('chat'); send(t); } }, `“${t}”`))));
    return;
  }
  const groups = new Map();
  for (const r of items) {
    const day = r.due.slice(0, 10);
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push(r);
  }
  panel.replaceChildren(...[...groups].map(([day, list]) => h('section', { class: 'rem-group' },
    h('div', { class: 'rem-day' }, `${dayName(day, 'long')} · ${fullDate(day)}`),
    ...list.map((r) => {
      const due = new Date(r.due);
      return h('div', { class: 'rem' },
        h('div', { class: 'rem-time' }, timeFmt.format(due)),
        h('div', { class: 'rem-body' }, h('div', { class: 'rem-text' }, r.text), h('div', { class: 'rem-rel' }, relative(due))),
        h('button', { type: 'button', class: 'rem-del', title: 'Delete reminder', onclick: () => api()?.delete_reminder(r.id) }, '✕'));
    }))));
}

const SKILLS = [
  ['🌤️', 'Weather', 'Now, hourly and 7-day forecast for any place.', ['What\'s the weather tomorrow in Costa da Caparica?', 'Will it rain this weekend in Lisbon?']],
  ['🔔', 'Reminders & timers', 'Reminders are saved and alert you even after a restart.', ['Remind me to call mum at 6pm', 'Set a timer for 10 minutes', 'What are my reminders?']],
  ['💻', 'Your computer', 'Folders, apps, volume and battery.', ['Open my Downloads folder', 'Open Spotify', 'Set the volume to 30', 'How\'s my battery?']],
  ['🔗', 'Shortcuts', 'Name a link or folder once, open it by voice.', ['Open my GitHub repositories', 'Save a shortcut called school for my Documents folder']],
  ['🔎', 'Web & news', 'Searches the web, reads pages and the latest headlines.', ['What\'s new in tech today?', 'Search for the best beaches near Lisbon']],
  ['📖', 'Knowledge', 'Wikipedia summaries and quick facts.', ['Who was Alan Turing?', 'Tell me about Costa da Caparica']],
  ['🎵', 'YouTube', 'Finds and plays videos and music.', ['Play some lofi on YouTube']],
  ['🧠', 'Memory & notes', 'Remembers things about you and keeps notes.', ['Remember that my favourite team is Benfica', 'Take a note: buy sunscreen', 'What do you know about me?']],
];

function renderSkills(shortcuts = {}) {
  const tips = h('div', { class: 'tips' },
    h('strong', {}, '🎙️ Talking to me'),
    h('ul', {},
      h('li', {}, `Say “Hey ${info.name}”, press Space or click the orb.`),
      h('li', {}, `Take your time: I wait up to ${info.listenSeconds || 10} seconds for you to start, and you can pause mid-sentence.`),
      h('li', {}, 'When I ask you something, the mic stays open for your answer — say “never mind” or “stop” to cancel.'),
      h('li', {}, 'Misheard? Click your message to fix it and resend.')));
  const entries = Object.entries(shortcuts);
  const saved = entries.length ? h('div', { class: 'skill' },
    h('div', { class: 'skill-head' }, h('span', { class: 'skill-icon' }, '⭐'), h('strong', {}, 'Your shortcuts')),
    h('div', { class: 'chips' }, entries.map(([name, target]) => h('button', {
      type: 'button', title: target, onclick: () => { showTab('chat'); send(`Open ${name}`); },
    }, name)))) : null;
  $('skills').replaceChildren(...[tips, saved, ...SKILLS.map(([icon, title, desc, examples]) => h('div', { class: 'skill' },
    h('div', { class: 'skill-head' }, h('span', { class: 'skill-icon' }, icon), h('strong', {}, title)),
    h('p', {}, desc),
    h('div', { class: 'chips' }, examples.map((t) => h('button', { type: 'button', onclick: () => { showTab('chat'); send(t); } }, t)))))].filter(Boolean));
}

function send(text) {
  if (!text.trim()) return;
  api()?.send_text(text);
}

$('micBtn').addEventListener('click', talk);
$('composer').addEventListener('submit', (e) => {
  e.preventDefault();
  showTab('chat');
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
$('miniBtn').addEventListener('click', () => api()?.mini());
$('expandBtn').addEventListener('click', () => api()?.expand());
$('quitBtn').addEventListener('click', () => api()?.quit());
$('orb').addEventListener('click', talk);
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
  if ((await api().mode()) === 'mini') {
    document.body.classList.add('mini');
    orb.hud = true;
  }
  document.body.classList.add('ready');
  const current = await api().info();
  applyInfo(current);
  setState(current.state || 'idle');
});
setTimeout(() => document.body.classList.add('ready'), 1500); // never stay invisible

renderReminders([]);
renderSkills({});
setState('idle');
orb.frame();
