// Model cache management page: lists in-memory model caches and the
// local (on-disk) registry locations, plus hosted-provider workloads.

const hostedModelsEl = document.getElementById('hostedModels');
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
  if (ok == null) return '<span class="text-gray-400">unknown</span>';
  return `<span class="badge ${ok ? 'badge-ok' : 'badge-off'}">${ok ? yesLabel : noLabel}</span>`;
}

const ROLE_CHIP_STYLE = {
  indexing: 'background:#dbeafe;color:#1e40af;',
  inference: 'background:#ede9fe;color:#5b21b6;',
};

const MODEL_DESCRIPTIONS = {
  dense: 'Dense FastEmbed embedding model',
  sparse: 'Sparse FastEmbed (SPLADE)',
  m3: 'BGE-M3 unified dense and sparse embeddings',
  late_interaction: 'ColBERT late-interaction reranker',
  reranker: 'Cross-encoder reranker',
  docling: 'Docling PDF extraction component',
};

const CACHE_BY_MODEL_KIND = {
  dense: 'embeddings-dense',
  sparse: 'embeddings-sparse',
  m3: 'embeddings-m3',
  late_interaction: 'colbert-reranker',
  reranker: 'cross-encoder-reranker',
  docling: 'docling-converters',
};

function roleChip(role) {
  const style = ROLE_CHIP_STYLE[role] || 'background:#f3f4f6;color:#6b7280;';
  return `<span class="badge" style="${style}">${role}</span>`;
}

function accelChips(accelerators) {
  if (!accelerators || !accelerators.length) return '<span class="text-gray-400">—</span>';
  return accelerators.map((a) => {
    const short = String(a).replace('ExecutionProvider', '');
    const gpu = /CoreML|CUDA|Tensorrt|ROCM|MPS|XPU/i.test(a);
    const cls = gpu
      ? 'background:#dcfce7;color:#166534;'
      : 'background:#f3f4f6;color:#6b7280;';
    return `<span class="badge" style="${cls}">${short}</span>`;
  }).join(' ');
}

function renderHostedProviders(providers) {
  if (!providers || !providers.length) {
    hostedModelsEl.innerHTML = '<div class="text-sm text-gray-400">Hosted provider status is unavailable.</div>';
    return;
  }
  hostedModelsEl.innerHTML = providers.map((provider) => {
    const workloadChips = (provider.ask_stage_workloads || []).length
      ? provider.ask_stage_workloads.map((workload) => `<span class="badge" style="background:#dbeafe;color:#1e40af;">${workload}</span>`).join(' ')
      : '<span class="text-sm text-gray-400">No Ask stages available</span>';
    const credential = provider.credentials_configured
      ? '<span class="badge badge-ok">credential configured</span>'
      : '<span class="badge badge-off">no credential configured</span>';
    return `
      <article class="bg-white rounded-lg shadow p-4">
        <div class="flex items-center justify-between gap-2 mb-3">
          <h3 class="font-semibold text-gray-900">${provider.label}</h3>
          ${credential}
        </div>
        <div class="text-xs uppercase tracking-wide text-gray-500 mb-2">Available in Ask for</div>
        <div class="flex flex-wrap gap-1">${workloadChips}</div>
      </article>`;
  }).join('');
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
  const cachesByName = new Map((data.caches || []).map((cache) => [cache.name, cache]));
  statusLine.textContent =
    `${total} model(s) in memory across ${data.caches.length} cache(s). ` +
    `Local weights: ${data.local_models_root}`;

  renderHostedProviders(data.hosted_providers);

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
          <th class="pb-2 pr-4">used for</th><th class="pb-2 pr-4">accel</th>
          <th class="pb-2 pr-4">enabled</th><th class="pb-2 pr-4">on disk</th>
          <th class="pb-2 pr-4">in memory</th><th class="pb-2 pr-4">cache dir</th>
          <th class="pb-2"></th>
        </tr>
      </thead>
      <tbody>
        ${data.local_models.map((m) => {
          const cache = cachesByName.get(CACHE_BY_MODEL_KIND[m.kind]);
          const cacheStatus = cache
            ? `TTL ${fmtIdle(cache.idle_timeout_s)} · ${m.in_memory ? 'loaded' : 'loads lazily on next use'}`
            : (m.in_memory ? 'loaded' : 'loads lazily on next use');
          const doclingMpsNote = m.kind === 'docling'
            && (m.accelerator || []).some((value) => String(value).toLowerCase() === 'mps')
            ? ' · MPS GPU on Apple Silicon only — unavailable in Docker.'
            : '';
          return `
          <tr class="border-t text-sm"
              title="${(m.domains || []).length ? 'used by: ' + m.domains.join(', ') : 'not referenced by any domain'}">
            <td class="py-2 pr-4">${m.kind}</td>
            <td class="py-2 pr-4 model-key">
              <div class="mono font-semibold text-gray-900">${m.name || '—'}</div>
              <div class="text-xs text-gray-500 mt-1">${MODEL_DESCRIPTIONS[m.kind] || 'Local model'} · ${cacheStatus}${doclingMpsNote}</div>
            </td>
            <td class="py-2 pr-4">${(m.roles || []).map(roleChip).join(' ')}
              ${(m.domains || []).length
                ? `<span class="text-xs text-gray-400">×${m.domains.length}</span>`
                : `<span class="text-xs text-gray-300">${(m.roles || []).includes('indexing') ? 'unused' : 'on-demand'}</span>`}</td>
            <td class="py-2 pr-4">${accelChips(m.accelerator)}</td>
            <td class="py-2 pr-4">${badge(m.enabled, 'yes', 'no')}</td>
            <td class="py-2 pr-4">${badge(m.on_disk, 'local', 'not found')}</td>
            <td class="py-2 pr-4">${badge(m.in_memory, 'memory', 'cold')}</td>
            <td class="py-2 mono model-key text-xs pr-4">${m.cache_dir || '—'}</td>
            <td class="py-2">
              ${m.in_memory && m.ejectable !== false
                ? `<div class="flex gap-1"><button class="action-btn" data-act="reload" data-cache="${m.loaded_in}" data-key="${m.loaded_key}">Reload</button><button class="action-btn action-btn-danger" data-act="eject" data-cache="${m.loaded_in}" data-key="${m.loaded_key}">Eject</button></div>`
                : '<span class="text-xs text-gray-300">—</span>'}
            </td>
          </tr>`;
        }).join('')}
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

localModelsEl.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const { act, cache, key } = btn.dataset;
  btn.disabled = true;
  try {
    if (act === 'eject') await post('/models/cache/eject', { cache, key });
    else if (act === 'reload') await post('/models/cache/reload', { cache, key });
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
