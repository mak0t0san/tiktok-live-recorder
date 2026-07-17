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

  return `
    <div class="row">
      <h3>@${esc(rec.user)}</h3>
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

async function refresh() {
  const data = await (await api('/api/status')).json();
  const seen = new Set();

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

refresh();
refreshFiles();
setInterval(refresh, 2000);
setInterval(refreshFiles, 10000);
