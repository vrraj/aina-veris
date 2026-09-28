// Retrieval evals — tab switching, labeled-dataset editor, and batch run-set.
// All user/dataset content is rendered via textContent/DOM nodes (never
// innerHTML) since dataset YAML is user-editable.
document.addEventListener('DOMContentLoaded', () => {
  // --- Tab switching (mirrors the index-page .index-tabs pattern) ---
  const tabs = Array.from(document.querySelectorAll('[data-eval-tab]'));
  const panels = Array.from(document.querySelectorAll('[data-eval-panel]'));
  function activateTab(name) {
    tabs.forEach((t) => {
      const active = t.dataset.evalTab === name;
      t.classList.toggle('index-tab-active', active);
      t.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    panels.forEach((p) => { p.hidden = p.dataset.evalPanel !== name; });
  }
  tabs.forEach((t) => t.addEventListener('click', () => activateTab(t.dataset.evalTab)));

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '');

  // --- Shared domain list ---
  async function fetchDomains() {
    try {
      const res = await fetch('/api/domains');
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data.domains) ? data.domains : [];
    } catch (_) {
      return [];
    }
  }

  async function fetchDatasets() {
    try {
      const res = await fetch('/api/retrieval-evals/datasets');
      if (!res.ok) return [];
      const data = await res.json();
      return Array.isArray(data.datasets) ? data.datasets : [];
    } catch (_) {
      return [];
    }
  }

  // =================== Tab 2: Evaluations (run-set) ===================
  const runSetDataset = $('runSetDataset');
  const runSetDomains = $('runSetDomains');
  const runSetBtn = $('runSetBtn');
  const runSetStatus = $('runSetStatus');
  const runSetResults = $('runSetResults');

  async function initRunSet() {
    const [datasets, domains] = await Promise.all([fetchDatasets(), fetchDomains()]);
    runSetDataset.textContent = '';
    datasets.forEach((name) => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      runSetDataset.appendChild(opt);
    });
    runSetDomains.textContent = '';
    domains.forEach((domain) => {
      const label = document.createElement('label');
      label.className = 'flex items-center gap-2 py-1';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = domain;
      const span = document.createElement('span');
      span.textContent = domain;
      label.appendChild(cb);
      label.appendChild(span);
      runSetDomains.appendChild(label);
    });
  }

  function verdictCell(row) {
    const td = document.createElement('td');
    if (row.error) {
      td.textContent = `error: ${row.error}`;
      td.className = 'text-red-600';
      return td;
    }
    const parts = [];
    parts.push(row.doc_hit ? `hit@${row.first_hit_rank ?? '?'}` : 'miss');
    if (row.retrieval_first_hit_rank && row.retrieval_first_hit_rank !== row.first_hit_rank) {
      parts.push(`(retr@${row.retrieval_first_hit_rank})`);
    }
    if (row.page_hit === true) parts.push('p✓');
    if (row.page_hit === false) parts.push('p✗');
    if (row.content_hit === true) parts.push('text✓');
    if (row.content_hit === false) parts.push('text✗');
    td.textContent = parts.join(' ');
    td.className = row.doc_hit ? 'text-green-700' : 'text-red-600';
    td.style.cursor = 'pointer';
    td.title = 'Click to load this query in the Query Evaluation tab';
    td.addEventListener('click', () => {
      const q = $('query');
      if (q) q.value = row.query;
      activateTab('query');
    });
    return td;
  }

  function renderRunSet(data) {
    runSetResults.textContent = '';
    const domains = data.domains || Object.keys(data.runs || {});
    if (!domains.length) return;

    const queries = [];
    const firstRun = data.runs[domains[0]];
    (firstRun?.per_query || []).forEach((r) => queries.push(r.query));

    const table = document.createElement('table');
    table.className = 'collection-table w-full text-sm';

    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    const qth = document.createElement('th');
    qth.className = 'text-left';
    qth.textContent = 'Query';
    headRow.appendChild(qth);
    domains.forEach((d) => {
      const th = document.createElement('th');
      th.className = 'text-left';
      th.textContent = d;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    queries.forEach((query, qi) => {
      const tr = document.createElement('tr');
      const qtd = document.createElement('td');
      qtd.textContent = query;
      tr.appendChild(qtd);
      domains.forEach((d) => {
        const row = (data.runs[d]?.per_query || [])[qi];
        tr.appendChild(row ? verdictCell(row) : document.createElement('td'));
      });
      tbody.appendChild(tr);
    });

    // Aggregate row
    const aggRow = document.createElement('tr');
    const aggLabel = document.createElement('td');
    aggLabel.textContent = 'aggregate';
    aggLabel.style.fontWeight = '700';
    aggRow.appendChild(aggLabel);
    domains.forEach((d) => {
      const td = document.createElement('td');
      const a = data.runs[d]?.aggregate;
      if (a) {
        const bits = [
          `hit ${a.doc_hits}/${a.queries} (${(a.hit_rate * 100).toFixed(0)}%)`,
          `MRR ${a.mrr.toFixed(2)}`,
        ];
        if (a.page_hit_rate !== null && a.page_hit_rate !== undefined) {
          bits.push(`p ${(a.page_hit_rate * 100).toFixed(0)}%`);
        }
        if (a.content_hit_rate !== null && a.content_hit_rate !== undefined) {
          bits.push(`text ${(a.content_hit_rate * 100).toFixed(0)}%`);
        }
        if (a.errors) bits.push(`err ${a.errors}`);
        td.textContent = bits.join(' · ');
      }
      aggRow.appendChild(td);
    });
    tbody.appendChild(aggRow);
    table.appendChild(tbody);

    const wrap = document.createElement('div');
    wrap.className = 'collection-table-wrap';
    wrap.appendChild(table);
    runSetResults.appendChild(wrap);
  }

  runSetBtn.addEventListener('click', async () => {
    const dataset = runSetDataset.value;
    if (!dataset) { runSetStatus.textContent = 'Pick a dataset first.'; return; }
    const domains = Array.from(runSetDomains.querySelectorAll('input:checked')).map((c) => c.value);
    if (!domains.length) { runSetStatus.textContent = 'Select at least one domain.'; return; }

    runSetBtn.disabled = true;
    runSetBtn.textContent = 'Running…';
    runSetStatus.textContent = `Running ${dataset} across ${domains.length} domain(s) — this runs every query through the full retrieval pipeline.`;
    runSetResults.textContent = '';

    const payload = {
      dataset,
      domains,
      search_mode: $('runSetSearchMode').value,
      top_k: Number($('runSetTopK').value || 8),
      split_compound_queries: $('runSetSplit').checked,
      max_compound_queries: 4,
      enable_cross_encoder_rerank: $('runSetCrossEncoder').checked,
      cross_encoder_top_n: 5,
      exact: $('runSetExact').checked,
    };
    try {
      const res = await fetch('/api/retrieval-evals/run-set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'run-set failed');
      renderRunSet(data);
      runSetStatus.textContent = `Done — ${dataset} × ${domains.join(', ')}`;
    } catch (err) {
      runSetStatus.textContent = `Error: ${err.message}`;
    } finally {
      runSetBtn.disabled = false;
      runSetBtn.textContent = 'Run Evaluation Set';
    }
  });

  // =================== Tab 3: Datasets (editor) ===================
  const datasetSelect = $('datasetSelect');
  const datasetName = $('datasetName');
  const datasetDescription = $('datasetDescription');
  const datasetRows = $('datasetRows');
  const datasetStatus = $('datasetStatus');

  function addRow(caseData) {
    const c = caseData || {};
    const tr = document.createElement('tr');

    const mk = (value, cls) => {
      const td = document.createElement('td');
      const input = document.createElement('input');
      input.type = 'text';
      input.value = esc(value);
      input.className = cls || 'w-full rounded-md border-gray-300 shadow-sm';
      td.appendChild(input);
      return { td, input };
    };

    const q = mk(c.query);
    const doc = mk(c.expected_document);
    const page = mk(c.expected_page ?? '', 'w-20 rounded-md border-gray-300 shadow-sm');
    const contain = mk(Array.isArray(c.must_contain) ? c.must_contain.join(', ') : '');

    const modeTd = document.createElement('td');
    const modeSel = document.createElement('select');
    modeSel.className = 'rounded-md border-gray-300 shadow-sm';
    ['any', 'all'].forEach((m) => {
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      modeSel.appendChild(opt);
    });
    modeSel.value = c.must_contain_mode === 'all' ? 'all' : 'any';
    modeTd.appendChild(modeSel);

    const delTd = document.createElement('td');
    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'text-red-600';
    delBtn.textContent = '✕';
    delBtn.addEventListener('click', () => tr.remove());
    delTd.appendChild(delBtn);

    [q.td, doc.td, page.td, contain.td, modeTd, delTd].forEach((td) => tr.appendChild(td));
    tr._caseFields = { q: q.input, doc: doc.input, page: page.input, contain: contain.input, mode: modeSel };
    datasetRows.appendChild(tr);
  }

  function collectDataset() {
    const queries = [];
    datasetRows.querySelectorAll('tr').forEach((tr) => {
      const f = tr._caseFields;
      if (!f) return;
      const query = f.q.value.trim();
      const doc = f.doc.value.trim();
      if (!query && !doc) return;
      const entry = { query, expected_document: doc };
      const page = f.page.value.trim();
      if (page) entry.expected_page = Number(page);
      const contain = f.contain.value.split(',').map((s) => s.trim()).filter(Boolean);
      if (contain.length) {
        entry.must_contain = contain;
        entry.must_contain_mode = f.mode.value;
      }
      queries.push(entry);
    });
    return { description: datasetDescription.value.trim(), queries };
  }

  async function refreshDatasetList(selected) {
    const names = await fetchDatasets();
    datasetSelect.textContent = '';
    names.forEach((name) => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      datasetSelect.appendChild(opt);
    });
    if (selected && names.includes(selected)) datasetSelect.value = selected;
    return names;
  }

  async function loadDataset(name) {
    if (!name) return;
    try {
      const res = await fetch(`/api/retrieval-evals/datasets/${encodeURIComponent(name)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'load failed');
      datasetName.value = name;
      datasetDescription.value = data.description || '';
      datasetRows.textContent = '';
      (data.queries || []).forEach((c) => addRow(c));
      datasetStatus.textContent = `Loaded ${name} — ${(data.queries || []).length} queries`;
    } catch (err) {
      datasetStatus.textContent = `Error loading dataset: ${err.message}`;
    }
  }

  datasetSelect.addEventListener('change', () => loadDataset(datasetSelect.value));

  $('datasetNewBtn').addEventListener('click', () => {
    datasetName.value = '';
    datasetDescription.value = '';
    datasetRows.textContent = '';
    addRow();
    datasetStatus.textContent = 'Editing a new dataset — set a name and Save.';
  });

  $('datasetAddRowBtn').addEventListener('click', () => addRow());

  $('datasetSaveBtn').addEventListener('click', async () => {
    const name = datasetName.value.trim();
    if (!/^[A-Za-z0-9_-]+$/.test(name)) {
      datasetStatus.textContent = 'Name must match ^[A-Za-z0-9_-]+$';
      return;
    }
    const payload = collectDataset();
    try {
      const res = await fetch(`/api/retrieval-evals/datasets/${encodeURIComponent(name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'save failed');
      datasetStatus.textContent = `Saved ${name} — ${data.queries} queries`;
      await refreshDatasetList(name);
      await initRunSet();
    } catch (err) {
      datasetStatus.textContent = `Error saving: ${err.message}`;
    }
  });

  $('datasetDeleteBtn').addEventListener('click', async () => {
    const name = datasetName.value.trim() || datasetSelect.value;
    if (!name || !confirm(`Delete dataset "${name}"?`)) return;
    try {
      const res = await fetch(`/api/retrieval-evals/datasets/${encodeURIComponent(name)}`, { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'delete failed');
      datasetStatus.textContent = `Deleted ${name} (backup at ${data.backup_path || 'n/a'})`;
      datasetName.value = '';
      datasetRows.textContent = '';
      await refreshDatasetList();
      await initRunSet();
    } catch (err) {
      datasetStatus.textContent = `Error deleting: ${err.message}`;
    }
  });

  // --- Init ---
  (async () => {
    await initRunSet();
    const names = await refreshDatasetList();
    if (names.length) await loadDataset(names[0]);
  })();
});
