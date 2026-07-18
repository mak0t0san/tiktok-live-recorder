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

function fmtDuration(startedAt, now) {
  if (!startedAt) return '';
  const s = Math.max(0, Math.floor(now - startedAt));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? `${h}h ` : '') + `${m}m ${sec}s`;
}

function displayState(rec) {
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
  }
  if (state === 'error' && rec.error) meta.push(esc(rec.error));

  const stoppable = rec.alive && !rec.stopped;
  const resumable = rec.stopped;
  const previewable = rec.previewable && !rec.stopped;

  const name = rec.nickname ? esc(rec.nickname) : `@${esc(rec.user)}`;
  const handle = rec.nickname ? `<div class="handle">@${esc(rec.user)}</div>` : '';

  return `
    <div class="row">
      <div class="id">
        ${avatarHtml(rec)}
        <div><h3>${name}</h3>${handle}</div>
      </div>
      <span class="chip ${state}">${state}</span>
    </div>
    <p class="meta">${meta.join(' · ')}</p>
    <div class="actions">
      <button data-action="preview" ${previewable ? '' : 'disabled'}>
        ${activePreviews.has(rec.user) ? 'Hide preview' : 'Preview'}</button>
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

  const anyStoppable = data.recordings.some((r) => r.alive && !r.stopped);
  const anyResumable = data.recordings.some((r) => r.stopped);
  document.getElementById('stop-all').hidden = !anyStoppable;
  document.getElementById('resume-all').hidden = !anyResumable;
}

async function refresh() {
  const data = await (await api('/api/status')).json();
  const seen = new Set();
  renderGlobalControls(data);

  for (const rec of data.recordings) {
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
  }

  for (const card of [...cards.children]) {
    if (!seen.has(card.dataset.user)) { closePreview(card.dataset.user); card.remove(); }
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
      <td><a href="/files/${encodeURIComponent(f.name)}" download>Download</a></td>
    </tr>`).join('');
  document.getElementById('files-empty').hidden = data.files.length > 0;
}

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

/* -- following picker ----------------------------------------------------- */

const followingList = document.getElementById('following-list');
const followingMsg = document.getElementById('following-msg');
let followingLoaded = false;

function followRowHtml(entry) {
  const avatar = entry.avatar_url
    ? `<img class="avatar" src="${esc(entry.avatar_url)}" alt=""
        onerror="this.hidden=true">`
    : `<div class="avatar avatar-fallback">${esc(entry.unique_id[0].toUpperCase())}</div>`;
  const name = entry.nickname ? esc(entry.nickname) : `@${esc(entry.unique_id)}`;
  return `
    <div class="follow-row" data-user="${esc(entry.unique_id)}">
      ${avatar}
      <div class="who"><div>${name}</div>
        <div class="handle">@${esc(entry.unique_id)}</div></div>
      <button data-add="${esc(entry.unique_id)}" class="primary">Add</button>
    </div>`;
}

async function loadFollowing(force = false) {
  followingMsg.textContent = 'Loading… (the first fetch can take a while)';
  followingList.innerHTML = '';
  try {
    const resp = await api(`/api/following${force ? '?refresh=1' : ''}`);
    const data = await resp.json();
    if (!resp.ok) {
      followingMsg.textContent = data.detail || 'Could not load following list';
      return;
    }
    followingLoaded = true;
    followingList.innerHTML = data.following.map(followRowHtml).join('');
    followingMsg.textContent = data.following.length
      ? '' : 'Everyone you follow is already monitored.';
  } catch (err) {
    followingMsg.textContent = 'Could not load following list';
  }
}

document.getElementById('following-section').addEventListener('toggle', (e) => {
  if (e.target.open && !followingLoaded) loadFollowing();
});

document.getElementById('following-refresh').addEventListener('click', () => {
  loadFollowing(true);
});

followingList.addEventListener('click', async (e) => {
  const user = e.target.dataset?.add;
  if (!user) return;
  e.target.disabled = true;
  const resp = await api('/api/users', {
    method: 'POST', body: JSON.stringify({ user }),
  });
  if (resp.ok) {
    followingList.querySelector(`[data-user="${CSS.escape(user)}"]`)?.remove();
    if (!followingList.children.length) {
      followingMsg.textContent = 'Everyone you follow is already monitored.';
    }
    refresh();
  } else {
    e.target.disabled = false;
    alert((await resp.json()).detail || 'Could not add user');
  }
});

refresh();
refreshFiles();
setInterval(refresh, 2000);
setInterval(refreshFiles, 10000);
