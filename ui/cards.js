// Visual cards for tool results (weather, search, news, Wikipedia, video, notes, timers, system).
// renderCard(card, compact) returns a DOM element; compact is used by the floating mini mode.

const h = (tag, props = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value);
  }
  node.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return node;
};

const openUrl = (url) => window.pywebview?.api?.open_url(url);
const ask = (text) => window.pywebview?.api?.send_text(text);

// ---------- Weather ----------

const WEATHER = [
  [[0], '☀️', '🌙', 'Clear'], [[1], '🌤️', '🌙', 'Mainly clear'], [[2], '⛅', '☁️', 'Partly cloudy'],
  [[3], '☁️', '☁️', 'Overcast'], [[45, 48], '🌫️', '🌫️', 'Fog'], [[51, 53, 55, 56, 57], '🌦️', '🌧️', 'Drizzle'],
  [[61, 63, 65, 66, 67], '🌧️', '🌧️', 'Rain'], [[71, 73, 75, 77], '🌨️', '🌨️', 'Snow'],
  [[80, 81, 82], '🌦️', '🌧️', 'Showers'], [[85, 86], '🌨️', '🌨️', 'Snow showers'], [[95, 96, 99], '⛈️', '⛈️', 'Thunderstorm'],
];
const weather = (code, day = true) => {
  const row = WEATHER.find(([codes]) => codes.includes(code)) || [[], '🌡️', '🌡️', 'Unknown'];
  return { icon: day ? row[1] : row[2], text: row[3] };
};

const localDate = (offset = 0) => {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return d.toLocaleDateString('en-CA'); // YYYY-MM-DD
};
const dayName = (iso, style = 'short') => {
  if (iso === localDate(0)) return 'Today';
  if (iso === localDate(1)) return 'Tomorrow';
  return new Date(`${iso}T12:00`).toLocaleDateString('en-GB', { weekday: style });
};
const fullDate = (iso) => new Date(`${iso}T12:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });

function hourlyChart(hours) {
  const W = 340; const H = 112; const top = 26; const bottom = 84;
  const temps = hours.map((x) => x.temp);
  const lo = Math.min(...temps) - 1; const hi = Math.max(...temps) + 1;
  const x = (i) => 12 + (i / (hours.length - 1)) * (W - 24);
  const y = (t) => bottom - ((t - lo) / (hi - lo)) * (bottom - top);
  const line = hours.map((p, i) => `${x(i).toFixed(1)},${y(p.temp).toFixed(1)}`).join(' ');
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('class', 'wx-chart');
  const nowHour = new Date().getHours();
  const isToday = hours[0]?.time.startsWith(localDate(0));
  svg.innerHTML = `
    <defs><linearGradient id="wxFill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffb35c" stop-opacity=".45"/><stop offset="1" stop-color="#ffb35c" stop-opacity="0"/>
    </linearGradient></defs>
    ${hours.map((p, i) => `<rect x="${x(i) - 4}" y="${bottom + 14 - p.rain * 0.12}" width="8" height="${p.rain * 0.12}" rx="2" fill="#5b8cff" opacity="${p.rain ? 0.75 : 0}"/>`).join('')}
    <polygon points="${x(0)},${bottom} ${line} ${x(hours.length - 1)},${bottom}" fill="url(#wxFill)"/>
    <polyline points="${line}" fill="none" stroke="#ffb35c" stroke-width="2.5" stroke-linejoin="round"/>
    ${isToday ? `<line x1="${x(nowHour)}" x2="${x(nowHour)}" y1="${top - 8}" y2="${bottom + 14}" stroke="#ffffff55" stroke-dasharray="3 3"/>` : ''}
    ${hours.map((p, i) => (i % 3 ? '' : `
      <circle cx="${x(i)}" cy="${y(p.temp)}" r="3" fill="#ffb35c"/>
      <text x="${x(i)}" y="${y(p.temp) - 9}" class="t">${p.temp}°</text>
      <text x="${x(i)}" y="${H - 2}" class="l">${p.time.slice(11, 13)}h</text>`)).join('')}`;
  return svg;
}

function weatherCard(card, compact) {
  const root = h('div', { class: `card weather${compact ? ' compact' : ''}` });
  const { place } = card;
  const heard = card.query.toLowerCase().split(',')[0].trim();
  const misheard = heard && !place.name.toLowerCase().includes(heard) && !heard.includes(place.name.toLowerCase());

  const draw = (focus) => {
    const day = card.days.find((d) => d.date === focus) || card.days[0];
    const isToday = day.date === card.days[0].date;
    const w = isToday ? weather(card.now.code, card.now.day) : weather(day.code);
    const hours = card.hours.filter((x) => x.time.startsWith(day.date));

    const hero = h('div', { class: 'wx-hero' },
      h('div', { class: 'wx-icon' }, w.icon),
      h('div', { class: 'wx-main' },
        h('div', { class: 'wx-day' }, `${dayName(day.date, 'long')} · ${fullDate(day.date)}`),
        isToday
          ? h('div', { class: 'wx-temp' }, `${card.now.temp}°`, h('span', {}, ` ${day.max}° / ${day.min}°`))
          : h('div', { class: 'wx-temp' }, `${day.max}°`, h('span', {}, ` / ${day.min}°`)),
        h('div', { class: 'wx-cond' }, isToday ? `${w.text} · feels like ${card.now.feels}°` : w.text)),
      compact ? null : h('div', { class: 'wx-stats' },
        h('span', {}, `💧 ${day.rain}% rain${day.precip_mm ? ` · ${day.precip_mm} mm` : ''}`),
        h('span', {}, `💨 ${isToday ? card.now.wind : day.wind} km/h`),
        h('span', {}, `🔆 UV ${day.uv}`),
        h('span', {}, `🌅 ${day.sunrise}  🌇 ${day.sunset}`)));

    const days = h('div', { class: 'wx-days' },
      card.days.slice(0, compact ? 4 : 7).map((d) => h('button', {
        type: 'button',
        class: d.date === day.date ? 'active' : '',
        onclick: (e) => { e.stopPropagation(); draw(d.date); },
      },
      h('span', { class: 'd' }, dayName(d.date)),
      h('span', { class: 'i' }, weather(d.code).icon),
      h('span', { class: 'hi' }, `${d.max}°`),
      h('span', { class: 'lo' }, `${d.min}°`),
      d.rain >= 20 ? h('span', { class: 'r' }, `${d.rain}%`) : h('span', { class: 'r' }, ''))));

    root.replaceChildren(...[
      h('div', { class: 'card-head' },
        h('span', { class: 'card-icon' }, '📍'),
        h('div', { class: 'card-title' },
          h('strong', {}, place.name),
          h('span', { class: 'muted' }, [place.region, place.country].filter(Boolean).join(', '))),
        misheard ? h('span', { class: 'heard', title: 'What I understood' }, `heard “${card.query}”`) : null),
      hero,
      !compact && hours.length ? hourlyChart(hours) : null,
      days,
      !compact && card.alternatives.length ? h('div', { class: 'alts' },
        h('span', { class: 'muted' }, 'Not the right place?'),
        card.alternatives.map((alt) => h('button', {
          type: 'button',
          onclick: () => ask(`What's the weather in ${alt} ${dayName(day.date).toLowerCase() === 'today' ? 'today' : `on ${fullDate(day.date)}`}?`),
        }, alt))) : null,
    ].filter(Boolean));
  };
  draw(card.focus);
  return root;
}

// ---------- Lists: web results and news ----------

const favicon = (domain) => `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`;

function linksCard(card, compact) {
  const items = card.items.slice(0, compact ? 3 : 6);
  return h('div', { class: `card links${compact ? ' compact' : ''}` },
    h('div', { class: 'card-head' }, h('span', { class: 'card-icon' }, '🔎'),
      h('div', { class: 'card-title' }, h('strong', {}, card.title))),
    h('div', { class: 'list' }, items.map((item) => h('button', { type: 'button', class: 'row', onclick: () => openUrl(item.url) },
      h('img', { class: 'fav', src: favicon(item.domain), alt: '' }),
      h('div', { class: 'row-text' },
        h('div', { class: 'row-title' }, item.title),
        h('div', { class: 'row-meta' }, item.domain),
        compact ? null : h('div', { class: 'row-snippet' }, item.snippet))))));
}

function newsCard(card, compact) {
  const items = card.items.slice(0, compact ? 3 : 6);
  return h('div', { class: `card news${compact ? ' compact' : ''}` },
    h('div', { class: 'card-head' }, h('span', { class: 'card-icon' }, '📰'),
      h('div', { class: 'card-title' }, h('strong', {}, 'Headlines'), h('span', { class: 'muted' }, card.topic))),
    h('div', { class: 'list' }, items.map((item, i) => h('button', { type: 'button', class: 'row', onclick: () => openUrl(item.url) },
      !compact && item.image ? h('img', { class: 'thumb', src: item.image, alt: '', onerror: (e) => e.target.remove() })
        : h('span', { class: 'num' }, i + 1),
      h('div', { class: 'row-text' },
        h('div', { class: 'row-title' }, item.title),
        h('div', { class: 'row-meta' }, [item.source, item.date && !item.date.includes('T') ? item.date : null].filter(Boolean).join(' · ')))))));
}

// ---------- Single items ----------

function wikiCard(card, compact) {
  return h('div', { class: `card wiki${compact ? ' compact' : ''}` },
    h('div', { class: 'wiki-body' },
      card.image && !compact ? h('img', { class: 'wiki-img', src: card.image, alt: '' }) : null,
      h('div', {},
        h('div', { class: 'card-kicker' }, 'Wikipedia'),
        h('strong', { class: 'wiki-title' }, card.title),
        card.description ? h('div', { class: 'muted' }, card.description) : null,
        h('p', { class: 'wiki-text' }, card.summary || ''),
        card.url && !compact ? h('button', { type: 'button', class: 'link', onclick: () => openUrl(card.url) }, 'Read on Wikipedia ↗') : null)));
}

function pageCard(card) {
  return h('div', { class: 'card page' },
    h('button', { type: 'button', class: 'row', onclick: () => openUrl(card.url) },
      h('img', { class: 'fav', src: favicon(card.domain), alt: '' }),
      h('div', { class: 'row-text' },
        h('div', { class: 'card-kicker' }, 'Read page'),
        h('div', { class: 'row-title' }, card.title),
        h('div', { class: 'row-meta' }, card.domain))));
}

function videoCard(card, compact) {
  return h('div', { class: `card video${compact ? ' compact' : ''}` },
    h('button', { type: 'button', class: 'video-thumb', onclick: () => openUrl(card.url) },
      h('img', { src: card.thumb, alt: '' }), h('span', { class: 'play' }, '▶')),
    h('div', { class: 'video-title' }, h('span', { class: 'card-kicker' }, 'Now playing on YouTube'), card.title));
}

function gauge(label, value, suffix = '%') {
  const v = Math.max(0, Math.min(100, value ?? 0));
  const color = v > 85 ? '#ff5c7a' : v > 60 ? '#ffb35c' : '#38e1ff';
  const wrap = h('div', { class: 'gauge' });
  wrap.innerHTML = `<svg viewBox="0 0 64 64"><circle cx="32" cy="32" r="26" class="track"/>
    <circle cx="32" cy="32" r="26" class="bar" stroke="${color}" stroke-dasharray="${(v / 100) * 163.4} 163.4"/></svg>
    <div class="gauge-value">${value ?? '—'}${value == null ? '' : suffix}</div>`;
  wrap.append(h('div', { class: 'gauge-label' }, label));
  return wrap;
}

function systemCard(card) {
  return h('div', { class: 'card system' },
    h('div', { class: 'card-head' }, h('span', { class: 'card-icon' }, '💻'),
      h('div', { class: 'card-title' }, h('strong', {}, 'This computer'), h('span', { class: 'muted' }, card.os))),
    h('div', { class: 'gauges' },
      gauge('CPU', Math.round(card.cpu_percent)),
      gauge('Memory', Math.round(card.memory_percent)),
      gauge(card.charging ? 'Battery ⚡' : 'Battery', card.battery_percent)));
}

function timerCard(card) {
  const time = h('div', { class: 'timer-time' });
  const bar = h('div', { class: 'timer-fill' });
  const root = h('div', { class: 'card timer' },
    h('div', { class: 'card-head' }, h('span', { class: 'card-icon' }, '⏱️'),
      h('div', { class: 'card-title' }, h('strong', {}, card.label), h('span', { class: 'muted' }, 'Timer'))),
    time, h('div', { class: 'timer-bar' }, bar));
  const tick = () => {
    const left = Math.max(0, Math.round((card.ends_at - Date.now()) / 1000));
    time.textContent = left ? `${String(Math.floor(left / 60)).padStart(2, '0')}:${String(left % 60).padStart(2, '0')}` : 'Done ✓';
    bar.style.width = `${(left / card.seconds) * 100}%`;
    root.classList.toggle('done', !left);
    if (root.isConnected) root.dataset.shown = '1';
    const removed = root.dataset.shown && !root.isConnected; // stop once the card leaves the screen
    if (left && !removed) setTimeout(tick, 1000);
  };
  tick();
  return root;
}

function notesCard(card) {
  return h('div', { class: 'card notes' },
    h('div', { class: 'card-head' }, h('span', { class: 'card-icon' }, '📝'),
      h('div', { class: 'card-title' }, h('strong', {}, 'Notes'), h('span', { class: 'muted' }, `${card.items.length}`))),
    card.items.length
      ? h('ul', {}, card.items.map((n, i) => h('li', { class: i === card.highlight ? 'new' : '' }, n.text)))
      : h('div', { class: 'muted' }, 'No notes yet.'));
}

function memoryCard(card) {
  return h('div', { class: 'card memory' },
    h('span', { class: 'card-icon' }, '🧠'),
    h('div', {}, h('div', { class: 'card-kicker' }, 'Remembered'), h('div', {}, card.text)));
}

function notFoundCard(card) {
  const folder = card.kind === 'folder';
  return h('div', { class: 'card notfound' },
    h('span', { class: 'card-icon' }, folder ? '📁' : '📍'),
    h('div', {}, h('strong', {}, `Couldn't find “${card.query}”`),
      h('div', { class: 'muted' }, folder ? 'Try the exact folder name, or a path like ~/Documents/Work.'
        : 'Try saying the name again, or add the country.')));
}

const dueText = (iso) => {
  const d = new Date(iso);
  return `${dayName(iso.slice(0, 10), 'long')} at ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`;
};

function reminderCard(card) {
  return h('div', { class: `card reminder${card.alert ? ' alert' : ''}` },
    h('span', { class: 'big-icon' }, card.alert ? '🔔' : '⏰'),
    h('div', {},
      h('div', { class: 'card-kicker' }, card.alert ? 'Reminder' : 'Reminder saved'),
      h('div', { class: 'main-line' }, card.text),
      h('div', { class: 'sub-line' }, dueText(card.due))));
}

function remindersCard(card, compact) {
  const items = card.items.slice(0, compact ? 3 : 8);
  return h('div', { class: 'card notes' },
    h('div', { class: 'card-head' }, h('span', { class: 'card-icon' }, '🔔'),
      h('div', { class: 'card-title' }, h('strong', {}, 'Upcoming reminders'), h('span', { class: 'muted' }, `${card.items.length}`))),
    items.length
      ? h('ul', {}, items.map((r) => h('li', {}, h('strong', {}, dueText(r.due)), ` — ${r.text}`)))
      : h('div', { class: 'muted' }, 'Nothing scheduled.'));
}

function folderCard(card) {
  return h('div', { class: 'card folder' },
    h('span', { class: 'big-icon' }, '📂'),
    h('div', { style: 'min-width:0' },
      h('div', { class: 'card-kicker' }, 'Opened folder'),
      h('div', { class: 'main-line' }, card.name),
      h('div', { class: 'sub-line' }, `${card.path}${card.count != null ? ` · ${card.count} items` : ''}`)));
}

function shortcutCard(card) {
  const isUrl = /^https?:\/\//.test(card.target);
  return h('div', { class: 'card shortcut' },
    isUrl ? h('img', { class: 'fav', src: favicon(new URL(card.target).hostname), alt: '', style: 'width:28px;height:28px' })
      : h('span', { class: 'big-icon' }, '📂'),
    h('div', { style: 'min-width:0' },
      h('div', { class: 'card-kicker' }, card.saved ? 'Shortcut saved' : 'Opened shortcut'),
      h('div', { class: 'main-line' }, card.name),
      h('div', { class: 'sub-line' }, card.target)));
}

const RENDERERS = {
  weather: weatherCard, links: linksCard, news: newsCard, wiki: wikiCard, page: pageCard, video: videoCard,
  system: systemCard, timer: timerCard, notes: notesCard, memory: memoryCard, notfound: notFoundCard,
  reminder: reminderCard, reminders: remindersCard, folder: folderCard, shortcut: shortcutCard,
};

window.renderCard = (card, compact = false) => {
  const render = RENDERERS[card.type];
  return render ? render(card, compact) : null;
};
