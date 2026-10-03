// Model cache management page: lists in-memory model caches and the
// local (on-disk) registry locations; supports eject / reload / eject-all.

const cacheList = document.getElementById('cacheList');
const localModelsEl = document.getElementById('localModels');
const localPathsEl = document.getElementById('localPaths');
const statusLine = document.getElementById('statusLine');
const refreshBtn = document.getElementById('refreshBtn');
const ejectAllBtn = document.getElementById('ejectAllBtn');

function fmtIdle(secs) {
  if (secs == null) return '—';
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ${secs % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function badge(ok, yesLabel, noLabel) {
  return `<span class="badge ${ok ? 'badge-ok' : 'badge-off'}">${ok ? yesLabel : noLabel}</span>`;
}

async function post(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(t || `${resp.status}`);
  }
  return resp.json();
}

function render(data) {
  const total = data.caches.reduce((n, c) => n + c.models.length, 0);
  statusLine.textContent =
    `${total} model(s) in memory across ${data.caches.length} cache(s). ` +
    `Local weights: ${data.local_models_root}`;

  cacheList.innerHTML = '';
  for (const c of data.caches) {
    const card = document.createElement('div');
    card.className = 'bg-white rounded-lg shadow p-4';
    const rows = c.models.map((m) => `
      <tr class="border-t">
        <td class="py-2 pr-4 mono model-key text-sm">${m.key}</td>
        <td class="py-2 pr-4 text-sm whitespace-nowrap text-right">idle ${fmtIdle(m.idle_secs)}</td>
        <td class="py-2 pr-2 text-right">
          <button class="action-btn" data-act="reload" data-cache="${c.name}" data-key="${m.key}">Reload</button>
        </td>
        <td class="py-2 text-right">
          <button class="action-btn action-btn-danger" data-act="eject" data-cache="${c.name}" data-key="${m.key}">Eject</button>
        </td>
      </tr>`).join('');
    card.innerHTML = `
      <div class="flex items-center justify-between mb-1">
        <div>
          <span class="font-semibold text-gray-900">${c.name}</span>
          <span class="text-sm text-gray-500 ml-2">${c.description || ''}</span>
        </div>
        <div class="text-sm text-gray-500">TTL ${fmtIdle(c.idle_timeout_s)} · ${c.models.length} loaded</div>
      </div>
      ${c.models.length
        ? `<table class="w-full model-table"><colgroup><col><col class="col-idle"><col class="col-act"><col class="col-act"></colgroup><tbody>${rows}</tbody></table>`
        : '<div class="text-sm text-gray-400 py-2">empty — loads lazily on next use</div>'}
      ${c.models.length
        ? `<button class="action-btn mt-2" data-act="eject-cache" data-cache="${c.name}">Eject all in ${c.name}</button>`
        : ''}`;
    cacheList.appendChild(card);
  }

  localPathsEl.innerHTML = `
    <div>models root: <span class="mono">${data.local_models_root || '—'}</span></div>
    ${data.hf_home ? `<div>HF_HOME: <span class="mono">${data.hf_home}</span></div>` : ''}`;

  if (!data.local_models || !data.local_models.length) {
    localModelsEl.innerHTML = '<div class="text-sm text-gray-400">No local models configured.</div>';
    return;
  }
  localModelsEl.innerHTML = `
    <table class="w-full">
      <thead>
        <tr class="text-left text-xs text-gray-500">
          <th class="pb-2 pr-4">kind</th><th class="pb-2 pr-4">model</th>
          <th class="pb-2 pr-4">enabled</th><th class="pb-2 pr-4">on disk</th>
          <th class="pb-2 pr-4">in memory</th><th class="pb-2 pr-4">cache dir</th>
          <th class="pb-2"></th>
        </tr>
      </thead>
      <tbody>
        ${data.local_models.map((m) => `
          <tr class="border-t text-sm">
            <td class="py-2 pr-4">${m.kind}</td>
            <td class="py-2 pr-4 mono model-key">${m.name || '—'}</td>
            <td class="py-2 pr-4">${badge(m.enabled, 'yes', 'no')}</td>
            <td class="py-2 pr-4">${badge(m.on_disk, 'local', 'not found')}</td>
            <td class="py-2 pr-4">${badge(m.in_memory, 'memory', 'cold')}</td>
            <td class="py-2 mono model-key text-xs pr-4">${m.cache_dir || '—'}</td>
            <td class="py-2">
              ${m.in_memory
                ? `<button class="action-btn action-btn-danger" data-act="eject" data-cache="${m.loaded_in}" data-key="${m.loaded_key}">Eject</button>`
                : '<span class="text-xs text-gray-300">—</span>'}
            </td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

async function load() {
  statusLine.textContent = 'Loading…';
  try {
    const resp = await fetch('/models/cache');
    if (!resp.ok) throw new Error(await resp.text());
    render(await resp.json());
  } catch (err) {
    statusLine.textContent = `Failed to load cache status: ${err.message || err}`;
  }
}

cacheList.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const { act, cache, key } = btn.dataset;
  btn.disabled = true;
  try {
    if (act === 'eject') await post('/models/cache/eject', { cache, key });
    else if (act === 'reload') await post('/models/cache/reload', { cache, key });
    else if (act === 'eject-cache') await post('/models/cache/eject', { cache });
    await load();
  } catch (err) {
    alert(err.message || String(err));
    btn.disabled = false;
  }
});

ejectAllBtn.addEventListener('click', async () => {
  if (!confirm('Eject every cached model? They will reload lazily on next use.')) return;
  ejectAllBtn.disabled = true;
  try {
    await post('/models/cache/eject-all');
    await load();
  } catch (err) {
    alert(err.message || String(err));
  } finally {
    ejectAllBtn.disabled = false;
  }
});

refreshBtn.addEventListener('click', load);
load();
