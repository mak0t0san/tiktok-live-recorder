/* Dashboard logic: polls /api/status and /api/files, renders cards, and
 * drives per-user actions (stop / resume / remove / HLS preview). */

const cards = document.getElementById('cards');
const activePreviews = new Map(); // user -> { hls, video }

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (resp.status === 401) { location.href = '/login'; throw new Error('unauthenticated'); }
  return resp;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function fmtBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log2(n) / 10));
  return `${(n / 2 ** (10 * i)).toFixed(i ? 1 : 0)} ${units[i]}`;
}

function fmtDurationSecs(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? `${h}h ` : '') + `${m}m ${sec}s`;
}

function fmtDuration(startedAt, now) {
  if (!startedAt) return '';
  return fmtDurationSecs(now - startedAt);
}

function fmtAgo(ts, now) {
  const s = Math.max(0, Math.floor(now - ts));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function displayState(rec) {
  if (rec.paused && !rec.alive) return 'paused';
  if (rec.stopped && !rec.alive) return 'stopped';
  if (!rec.alive && rec.state !== 'stopped') return rec.monitored ? 'restarting' : 'stale';
  return rec.state;
}

function avatarHtml(rec) {
  if (rec.avatar) {
    return `<img class="avatar" src="${esc(rec.avatar)}" alt=""
      onerror="this.hidden=true">`;
  }
  const letter = (rec.user || '?')[0].toUpperCase();
  return `<div class="avatar avatar-fallback">${esc(letter)}</div>`;
}

function cardHtml(rec, now) {
  const state = displayState(rec);
  const meta = [];
  if (state === 'recording') {
    meta.push(fmtDuration(rec.started_at, now), fmtBytes(rec.bytes_written));
  } else if (rec.last_recorded_at) {
    meta.push(`Last live: ${fmtAgo(rec.last_recorded_at, now)} · ${fmtDurationSecs(rec.last_duration)}`);
  }
  if (state === 'error' && rec.error) meta.push(esc(rec.error));

  const stoppable = rec.alive && !rec.paused;
  const resumable = rec.paused || rec.stopped;
  const previewable = rec.previewable && !rec.stopped && !rec.paused;
  const checkable = state === 'waiting' && rec.alive && !rec.paused;

  const name = rec.nickname ? esc(rec.nickname) : `@${esc(rec.user)}`;
  const handle = rec.nickname ? `<div class="handle">@${esc(rec.user)}</div>` : '';
  const chip = state === 'recording'
    ? '<span class="chip recording"><span class="rec-dot"></span>recording</span>'
    : `<span class="chip ${state}">${state}</span>`;

  return `
    <div class="row">
      <div class="id">
        ${avatarHtml(rec)}
        <div><h3>${name}</h3>${handle}</div>
      </div>
      ${chip}
    </div>
    <p class="meta">${meta.join(' · ')}</p>
    <div class="actions">
      <button data-action="preview" ${previewable ? '' : 'disabled'}>
        ${activePreviews.has(rec.user) ? 'Hide preview' : 'Preview'}</button>
      <button data-action="check" ${checkable ? '' : 'disabled'}>Check now</button>
      <button data-action="stop" ${stoppable ? '' : 'disabled'}>Stop</button>
      <button data-action="resume" ${resumable ? '' : 'hidden'}>Resume</button>
      <button data-action="remove" class="danger">Remove</button>
    </div>
  `;
}

function closePreview(user) {
  const p = activePreviews.get(user);
  if (!p) return;
  if (p.hls) p.hls.destroy();
  p.video.remove();
  activePreviews.delete(user);
}

function openPreview(user, card) {
  const video = document.createElement('video');
  video.controls = true;
  video.muted = true;
  video.autoplay = true;
  card.appendChild(video);

  const src = `/preview/${encodeURIComponent(user)}/index.m3u8`;
  let hls = null;
  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({ liveSyncDurationCount: 2 });
    hls.loadSource(src);
    hls.attachMedia(video);
  } else {
    video.src = src; // Safari plays HLS natively
  }
  activePreviews.set(user, { hls, video });
}

async function act(user, action, card) {
  if (action === 'preview') {
    if (activePreviews.has(user)) closePreview(user);
    else openPreview(user, card);
    refresh();
    return;
  }
  if (action === 'check') {
    const btn = card.querySelector('[data-action="check"]');
    btn.disabled = true;
    btn.textContent = 'Checking…';
    await api(`/api/recordings/${encodeURIComponent(user)}/check-now`, { method: 'POST' });
    // The next 2s status poll rebuilds the card (and this button) with the
    // outcome; no immediate refresh so "Checking…" stays visible briefly.
    setTimeout(refresh, 500);
    return;
  }
  if (action === 'stop') {
    closePreview(user);
    await api(`/api/recordings/${encodeURIComponent(user)}/stop`, { method: 'POST' });
  } else if (action === 'resume') {
    await api(`/api/recordings/${encodeURIComponent(user)}/resume`, { method: 'POST' });
  } else if (action === 'remove') {
    if (!confirm(`Stop monitoring @${user}? An in-progress recording will be aborted.`)) return;
    closePreview(user);
    await api(`/api/users/${encodeURIComponent(user)}`, { method: 'DELETE' });
  }
  refresh();
}

function renderGlobalControls(data) {
  const pauseBtn = document.getElementById('pause-toggle');
  pauseBtn.hidden = false;
  pauseBtn.textContent = data.paused ? 'Resume monitoring' : 'Pause monitoring';
  document.getElementById('paused-banner').hidden = !data.paused;

  const anyStoppable = data.recordings.some((r) => r.alive && !r.paused);
  const anyResumable = data.recordings.some((r) => r.paused || r.stopped);
  document.getElementById('stop-all').hidden = !anyStoppable;
  document.getElementById('resume-all').hidden = !anyResumable;
}

/* -- sorting --------------------------------------------------------------- */

const STATUS_ORDER = {
  recording: 0, converting: 1, uploading: 1, waiting: 2, starting: 2,
  restarting: 2, paused: 3, stopped: 3, error: 4, stale: 4, unknown: 5,
};

function sortRecs(recs) {
  const key = localStorage.getItem('tlr-sort') || 'status';
  return [...recs].sort((a, b) => {
    if (key === 'user') return a.user.localeCompare(b.user);
    if (key === 'name') return (a.nickname || a.user).localeCompare(b.nickname || b.user);
    const d = (STATUS_ORDER[displayState(a)] ?? 9) - (STATUS_ORDER[displayState(b)] ?? 9);
    return d || a.user.localeCompare(b.user);
  });
}

async function refresh() {
  const data = await (await api('/api/status')).json();
  const seen = new Set();
  renderGlobalControls(data);

  const sorted = sortRecs(data.recordings);
  for (const rec of sorted) {
    seen.add(rec.user);
    let card = cards.querySelector(`[data-user="${CSS.escape(rec.user)}"]`);
    if (!card) {
      card = document.createElement('div');
      card.className = 'card';
      card.dataset.user = rec.user;
      card.addEventListener('click', (e) => {
        const action = e.target.dataset?.action;
        if (action) act(rec.user, action, card);
      });
      cards.appendChild(card);
    }
    const video = card.querySelector('video');
    card.innerHTML = cardHtml(rec, data.now);
    if (video && activePreviews.has(rec.user)) card.appendChild(video);
    else if (video) video.remove();

    const st = displayState(rec);
    card.classList.toggle('recording', st === 'recording');
    card.classList.toggle('paused', st === 'paused' || st === 'stopped');
  }

  for (const card of [...cards.children]) {
    if (!seen.has(card.dataset.user)) { closePreview(card.dataset.user); card.remove(); }
  }

  // Reorder the DOM only when the order actually changed: moving a node with
  // a playing <video> can hiccup playback.
  const desired = sorted.map((r) => r.user);
  const current = [...cards.children].map((c) => c.dataset.user);
  if (desired.join('\n') !== current.join('\n')) {
    for (const user of desired) {
      const card = cards.querySelector(`[data-user="${CSS.escape(user)}"]`);
      if (card) cards.appendChild(card);
    }
  }
  document.getElementById('cards-empty').hidden = data.recordings.length > 0;
}

async function refreshFiles() {
  const data = await (await api('/api/files')).json();
  const tbody = document.getElementById('files');
  tbody.innerHTML = data.files.map((f) => `
    <tr>
      <td>${esc(f.name)}${f.raw ? ' <span class="chip">raw</span>' : ''}</td>
      <td class="num">${fmtBytes(f.size)}</td>
      <td class="num">${new Date(f.mtime * 1000).toLocaleString()}</td>
      <td>
        ${f.raw && f.convertible
    ? `<button data-file-action="convert" data-file-name="${encodeURIComponent(f.name)}">Convert</button> `
    : ''}
        <a href="/files/${encodeURIComponent(f.name)}" download>Download</a>
      </td>
    </tr>`).join('');
  document.getElementById('files-empty').hidden = data.files.length > 0;
}

document.getElementById('files').addEventListener('click', async (e) => {
  const convertBtn = e.target.closest('button[data-file-action="convert"]');
  if (!convertBtn) return;
  const name = decodeURIComponent(convertBtn.dataset.fileName);
  convertBtn.disabled = true;
  const resp = await api(`/api/files/${encodeURIComponent(name)}/convert`, { method: 'POST' });
  if (!resp.ok) {
    convertBtn.disabled = false;
    const body = await resp.json();
    alert(body.detail || 'Could not convert file');
    return;
  }
  await refreshFiles();
});

document.getElementById('add-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const input = document.getElementById('add-user');
  const user = input.value.trim();
  if (!user) return;
  const resp = await api('/api/users', { method: 'POST', body: JSON.stringify({ user }) });
  if (resp.ok) { input.value = ''; refresh(); }
  else alert((await resp.json()).detail || 'Could not add user');
});

document.getElementById('logout').addEventListener('click', async () => {
  await api('/api/logout', { method: 'POST' });
  location.href = '/login';
});

/* -- global stop / resume / pause ---------------------------------------- */

document.getElementById('stop-all').addEventListener('click', async () => {
  if (!confirm('Stop all recorders? In-flight recordings are finalized; '
    + 'each user stays stopped until resumed.')) return;
  for (const user of [...activePreviews.keys()]) closePreview(user);
  await api('/api/recordings/stop-all', { method: 'POST' });
  refresh();
});

document.getElementById('resume-all').addEventListener('click', async () => {
  await api('/api/recordings/resume-all', { method: 'POST' });
  refresh();
});

document.getElementById('pause-toggle').addEventListener('click', async (e) => {
  const pausing = e.target.textContent.startsWith('Pause');
  if (pausing && !confirm('Pause monitoring? In-flight recordings are '
    + 'finalized and nothing records until you resume.')) return;
  await api(`/api/monitoring/${pausing ? 'pause' : 'resume'}`, { method: 'POST' });
  refresh();
});

const sortSelect = document.getElementById('sort-select');
sortSelect.value = localStorage.getItem('tlr-sort') || 'status';
sortSelect.addEventListener('change', () => {
  localStorage.setItem('tlr-sort', sortSelect.value);
  refresh();
});

refresh();
refreshFiles();
setInterval(refresh, 2000);
setInterval(refreshFiles, 10000);
