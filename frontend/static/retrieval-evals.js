document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('retrievalEvalForm');
  const activeDomain = document.getElementById('activeDomain');
  const payloadPreview = document.getElementById('payloadPreview');
  const decompositionMeta = document.getElementById('decompositionMeta');
  const decompositionQueries = document.getElementById('decompositionQueries');
  const retrievalMeta = document.getElementById('retrievalMeta');
  const retrievalResults = document.getElementById('retrievalResults');
  const colbertMeta = document.getElementById('colbertMeta');
  const colbertResults = document.getElementById('colbertResults');
  const rerankMeta = document.getElementById('rerankMeta');
  const coverageMeta = document.getElementById('coverageMeta');
  const rerankResults = document.getElementById('rerankResults');

  window.toggleCollapsible = function(header, event) {
    event.preventDefault();
    const parent = header.parentElement;
    const content = parent.querySelector('.collapsible-content');
    const chevron = header.querySelector('.chevron');
    const isExpanded = header.getAttribute('aria-expanded') === 'true';

    if (isExpanded) {
      content.style.display = 'none';
      header.setAttribute('aria-expanded', 'false');
      if (chevron) chevron.textContent = '▲';
    } else {
      content.style.display = 'block';
      header.setAttribute('aria-expanded', 'true');
      if (chevron) chevron.textContent = '▼';
    }
  };

  function textFromItem(item) {
    const payload = (item && item.payload) || {};
    return payload.text || payload.snippet || payload.content || '';
  }

  function card(item, scoreLabel, scoreValue) {
    const payload = (item && item.payload) || {};
    const compound = (item && item.compound_retrieval) || {};
    const matchedQueries = Array.isArray(compound.matched_queries) ? compound.matched_queries : [];
    const scoreText = scoreValue === null || scoreValue === undefined ? '' : `${scoreLabel}: ${Number(scoreValue).toFixed(4)}`;
    return `
      <div class="border rounded p-3 bg-gray-50">
        <div class="text-xs text-gray-600 mb-1">
          <span>${payload.url || 'unknown-url'}</span>
          <span> • ${payload.section || 'N/A'}</span>
          <span> • ${payload.subsection || 'N/A'}</span>
          <span> • chunk ${payload.chunk_index ?? 'N/A'}</span>
          ${scoreText ? `<span> • ${scoreText}</span>` : ''}
        </div>
        ${matchedQueries.length ? `<div class="text-xs text-indigo-700 mb-1">Matched queries: ${matchedQueries.join(' | ')}</div>` : ''}
        <div class="text-sm text-gray-900 whitespace-pre-wrap">${textFromItem(item)}</div>
      </div>
    `;
  }

  function setDomainOptions(domains) {
    const preferred = localStorage.getItem('active_domain') || '';
    activeDomain.innerHTML = `<option value="">(default)</option>${domains.map((d) => `<option value="${d}">${d}</option>`).join('')}`;
    if (preferred && domains.includes(preferred)) {
      activeDomain.value = preferred;
    }
  }

  function syncActiveDomainSelect(domain) {
    const val = String(domain || '').trim();
    const hasOption = Array.from(activeDomain.options).some((option) => option.value === val);
    if (!val || hasOption) {
      activeDomain.value = val;
    }
  }

  async function loadDomains() {
    try {
      const res = await fetch('/api/domains');
      if (!res.ok) return;
      const data = await res.json();
      setDomainOptions(Array.isArray(data.domains) ? data.domains : []);
    } catch (_) {
      setDomainOptions(['default', 'mountains', 'oceans', 'finance']);
    }
  }

  activeDomain.addEventListener('change', () => {
    const val = String(activeDomain.value || '').trim();
    if (val) {
      localStorage.setItem('active_domain', val);
    } else {
      localStorage.removeItem('active_domain');
    }
  });

  window.addEventListener('storage', (event) => {
    if (event.key === 'active_domain') {
      syncActiveDomainSelect(event.newValue);
    }
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const query = String(document.getElementById('query').value || '').trim();
    if (!query) {
      alert('Query is required');
      return;
    }

    const runEvalBtn = document.getElementById('runEvalBtn');
    if (runEvalBtn) {
      runEvalBtn.disabled = true;
      runEvalBtn.innerText = 'Running...';
      runEvalBtn.style.opacity = '0.6';
      runEvalBtn.style.cursor = 'not-allowed';
    }

    const urlFilter = String(document.getElementById('urlFilter').value || '').trim();
    const payload = {
      query,
      active_domain: String(activeDomain.value || '').trim() || undefined,
      search_mode: String(document.getElementById('searchMode').value || 'dense').trim(),
      top_k: Number(document.getElementById('topK').value || 8),
      score_threshold: Number(document.getElementById('scoreThreshold').value || 0.35),
      query_filter: urlFilter ? { url: urlFilter } : null,
      with_payload: !!document.getElementById('withPayload').checked,
      exact: !!document.getElementById('exact').checked,
      split_compound_queries: !!document.getElementById('splitCompoundQueries').checked,
      max_compound_queries: Number(document.getElementById('maxCompoundQueries').value || 4),
      use_colbert: !!document.getElementById('useColbert').checked,
      colbert_top_n: Number(document.getElementById('colbertTopN').value || 8),
      enable_cross_encoder_rerank: !!document.getElementById('enableCrossEncoderRerank').checked,
      cross_encoder_top_n: Number(document.getElementById('crossEncoderTopN').value || 5),
      ensure_subquery_coverage: !!document.getElementById('ensureSubqueryCoverage').checked,
      min_results_per_subquery: Number(document.getElementById('minResultsPerSubquery').value || 1),
      coverage_max_reserved: Number(document.getElementById('coverageMaxReserved').value || 4),
    };

    payloadPreview.textContent = JSON.stringify(payload, null, 2);
    retrievalResults.innerHTML = '<div class="text-sm text-gray-500">Running retrieval...</div>';
    decompositionMeta.textContent = 'Evaluating query...';
    decompositionQueries.textContent = '';
    colbertResults.innerHTML = '';
    rerankResults.innerHTML = '';

    try {
      const res = await fetch('/retrieval-evals/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Pipeline failed');
      }

      const decomposition = data.decomposition || {};
      const decompositionItems = Array.isArray(decomposition.queries) ? decomposition.queries : [];
      decompositionMeta.textContent = `Compound: ${decomposition.is_compound ? 'yes' : 'no'} | Normalized: ${decomposition.normalized_query || decomposition.original_query || 'n/a'} | Reason: ${decomposition.reason || 'n/a'}`;
      decompositionQueries.textContent = decompositionItems.map((item, index) => `${index + 1}. ${item}`).join('\n');

      const retrieval = data.retrieval || {};
      const retrievalItems = Array.isArray(retrieval.results) ? retrieval.results : [];
      const queryCount = Array.isArray(retrieval.query_results) ? retrieval.query_results.length : 1;
      retrievalMeta.textContent = `Requested mode: ${retrieval.requested_search_mode || 'n/a'} | Effective mode: ${retrieval.effective_search_mode || 'n/a'} | Queries: ${queryCount} | Fusion: ${retrieval.fusion_method || 'none'} | Returned: ${retrievalItems.length}`;
      retrievalResults.innerHTML = retrievalItems.map((item) => card(item, 'retrieval score', item.score)).join('') || '<div class="text-sm text-gray-500">No retrieval results</div>';

      const colbert = data.colbert;
      if (colbert) {
        const rows = Array.isArray(colbert.all_scored) ? colbert.all_scored : [];
        colbertMeta.textContent = `Model: ${colbert.model || 'n/a'} | Top-N: ${colbert.top_n ?? 'n/a'} | Returned: ${colbert.count_after_top_n ?? rows.length}`;
        colbertResults.innerHTML = rows.map((row) => card(row.item, 'colbert score', row.colbert_score)).join('') || '<div class="text-sm text-gray-500">No ColBERT results</div>';
      } else {
        colbertMeta.textContent = 'ColBERT disabled';
        colbertResults.innerHTML = '<div class="text-sm text-gray-500">Enable ColBERT to view section.</div>';
      }

      const reranked = data.reranked || {};
      const rerankRows = Array.isArray(reranked.items) ? reranked.items : [];
      const crossEncoderEnabled = reranked.cross_encoder_enabled !== false && reranked.model !== null;
      rerankMeta.textContent = `Model: ${reranked.model || 'n/a'} | Enabled: ${crossEncoderEnabled ? 'yes' : 'no'} | Returned: ${rerankRows.length}`;
      const coverage = data.coverage || {};
      const uncovered = Array.isArray(coverage.uncovered_queries) ? coverage.uncovered_queries : [];
      coverageMeta.textContent = `Coverage: ${coverage.enabled ? 'enabled' : 'disabled'} | Covered: ${coverage.covered_queries ?? 'n/a'}/${coverage.requested_queries ?? 'n/a'} | Reserved: ${coverage.reserved_items ?? 0} | Satisfied: ${coverage.guarantee_satisfied ? 'yes' : 'no'}${uncovered.length ? ` | Uncovered: ${uncovered.join(' | ')}` : ''}`;
      if (crossEncoderEnabled) {
        rerankResults.innerHTML = rerankRows.map((row) => card(row.item, 'cross-encoder score', row.cross_encoder_score)).join('') || '<div class="text-sm text-gray-500">No reranked results</div>';
      } else {
        rerankResults.innerHTML = '<div class="text-sm text-gray-500">Cross-Encoder reranking disabled - showing retrieval results</div>';
      }
    } catch (err) {
      retrievalResults.innerHTML = `<div class="text-sm text-red-600">Error: ${err.message}</div>`;
      decompositionMeta.textContent = '';
      decompositionQueries.textContent = '';
      colbertMeta.textContent = '';
      rerankMeta.textContent = '';
      coverageMeta.textContent = '';
    } finally {
      if (runEvalBtn) {
        runEvalBtn.disabled = false;
        runEvalBtn.innerText = 'Run Retrieval Eval';
        runEvalBtn.style.opacity = '1';
        runEvalBtn.style.cursor = 'pointer';
      }
    }
  });

  loadDomains();
});
