document.addEventListener('DOMContentLoaded', () => {
    const tabs = Array.from(document.querySelectorAll('[data-index-tab]'));
    const panels = Array.from(document.querySelectorAll('[data-index-tab-panel]'));
    const validTabs = new Set(tabs.map(tab => tab.dataset.indexTab));
    const tabPanelsContainer = document.querySelector('.index-tab-panels');
    const tabsContainer = document.querySelector('.index-tabs');
    const contentPanels = Array.from(document.querySelectorAll('.content-panel'));
    const pageHeader = document.querySelector('.index-page-header');
    const pageTitle = document.querySelector('.index-page-title');
    const pageSubtitle = document.querySelector('.index-page-subtitle');
    const domainControl = document.querySelector('.index-domain-control');

    function activateTab(tabName) {
        if (!validTabs.has(tabName)) return;

        tabs.forEach((tab) => {
            const isActive = tab.dataset.indexTab === tabName;
            tab.classList.toggle('index-tab-active', isActive);
            tab.setAttribute('aria-selected', String(isActive));
        });

        panels.forEach((panel) => {
            panel.hidden = panel.dataset.indexTabPanel !== tabName;
        });

        window.location.hash = `index-${tabName}`;
    }

    function showIngestionView() {
        if (tabPanelsContainer) tabPanelsContainer.style.display = '';
        if (tabsContainer) tabsContainer.style.display = '';
        contentPanels.forEach(panel => panel.style.display = 'none');
        updateNavActiveState('openIngestionBtn');
        updatePageHeader('Extract & generate embeddings', 'Ingest content into the active domain\'s index');
        // Activate the default tab (pdf) and set hash
        activateTab('pdf');
    }

    function showContentPanel(panelId) {
        if (tabPanelsContainer) tabPanelsContainer.style.display = 'none';
        if (tabsContainer) tabsContainer.style.display = 'none';
        contentPanels.forEach(panel => {
            panel.style.display = panel.id === panelId ? '' : 'none';
        });

        const panelHeaders = {
            'content-panel-list-docs': ['Knowledge Base Catalog', 'See indexed / embedded documents with chunk counts'],
            'content-panel-debug-index': ['View Payload from Qdrant Vector Store', 'View raw document payloads and metadata'],
            'content-panel-search': ['Vector-Based Semantic Search', 'Search documents using semantic similarity'],
            'content-panel-delete-index': ['Delete Document from Knowledge Base', 'Preview and delete indexed chunks for a given document URL']
        };

        const [title, subtitle] = panelHeaders[panelId] || ['Content', ''];
        updatePageHeader(title, subtitle);
    }

    function updatePageHeader(title, subtitle) {
        if (pageTitle) pageTitle.textContent = title;
        if (pageSubtitle) pageSubtitle.textContent = subtitle;
    }

    function getActiveDomain() {
        try {
            return String(localStorage.getItem('active_domain') || '').trim();
        } catch (_) {
            return '';
        }
    }

    function updateIngestionDomainDisplay() {
        const domainDisplay = document.getElementById('ingestionDomainDisplay');
        if (!domainDisplay) return;
        const domain = getActiveDomain();
        domainDisplay.textContent = domain || '(default)';
    }

    // Listen for domain changes from the ingestion view's domain selector
    const ingestionDomainSelect = document.getElementById('activeDomain');
    if (ingestionDomainSelect) {
        ingestionDomainSelect.addEventListener('change', () => {
            updateIngestionDomainDisplay();
        });
    }

    // Listen for storage events (sync across tabs)
    window.addEventListener('storage', (event) => {
        if (event.key === 'active_domain') {
            updateIngestionDomainDisplay();
        }
    });

    // Initial update
    updateIngestionDomainDisplay();

    function updateNavActiveState(activeBtnId) {
        document.querySelectorAll('.index-nav-item').forEach(item => {
            item.classList.remove('index-nav-item-active');
        });
        const activeBtn = document.getElementById(activeBtnId);
        if (activeBtn) activeBtn.classList.add('index-nav-item-active');
    }

    tabs.forEach((tab) => {
        tab.addEventListener('click', () => activateTab(tab.dataset.indexTab));
    });

    // Content ingestion button
    const openIngestionBtn = document.getElementById('openIngestionBtn');
    if (openIngestionBtn) {
        openIngestionBtn.addEventListener('click', showIngestionView);
    }

    // List documents button
    const openListDocsBtn = document.getElementById('openListDocsBtn');
    if (openListDocsBtn) {
        openListDocsBtn.addEventListener('click', () => {
            showContentPanel('content-panel-list-docs');
            updateNavActiveState('openListDocsBtn');
            initListDocsPanel();
            window.location.hash = 'explore-knowledge-base';
        });
    }

    // Debug index button
    const openDebugBtn = document.getElementById('openDebugBtn');
    if (openDebugBtn) {
        openDebugBtn.addEventListener('click', () => {
            showContentPanel('content-panel-debug-index');
            updateNavActiveState('openDebugBtn');
            initDebugIndexPanel();
            window.location.hash = 'view-metadata';
        });
    }

    // Search button
    const openSearchBtn = document.getElementById('openSearchBtn');
    if (openSearchBtn) {
        openSearchBtn.addEventListener('click', () => {
            showContentPanel('content-panel-search');
            updateNavActiveState('openSearchBtn');
            initSearchPanel();
            window.location.hash = 'semantic-search';
        });
    }

    // Delete index button
    const openDeleteIndexBtn = document.getElementById('openDeleteIndexBtn');
    if (openDeleteIndexBtn) {
        openDeleteIndexBtn.addEventListener('click', () => {
            showContentPanel('content-panel-delete-index');
            updateNavActiveState('openDeleteIndexBtn');
            initDeleteIndexPanel();
            window.location.hash = 'delete-documents';
        });
    }

    // Handle hash-based navigation on page load
    const hash = window.location.hash.replace('#', '');
    if (hash === 'explore-knowledge-base') {
        showContentPanel('content-panel-list-docs');
        updateNavActiveState('openListDocsBtn');
        initListDocsPanel();
    } else if (hash === 'view-metadata') {
        showContentPanel('content-panel-debug-index');
        updateNavActiveState('openDebugBtn');
        initDebugIndexPanel();
    } else if (hash === 'semantic-search') {
        showContentPanel('content-panel-search');
        updateNavActiveState('openSearchBtn');
        initSearchPanel();
    } else if (hash === 'delete-documents') {
        showContentPanel('content-panel-delete-index');
        updateNavActiveState('openDeleteIndexBtn');
        initDeleteIndexPanel();
    } else if (hash.startsWith('index-')) {
        const tabName = hash.replace('index-', '');
        if (validTabs.has(tabName)) {
            activateTab(tabName);
        }
    }

    // List documents panel logic
    function initListDocsPanel() {
        const fetchBtn = document.getElementById('listDocsFetchBtn');
        const downloadBtn = document.getElementById('listDocsDownloadBtn');
        const urlInput = document.getElementById('listDocsUrl');
        const output = document.getElementById('listDocsOutput');
        const activeDomainSelect = document.getElementById('listDocsActiveDomain');
        const maxRowsInput = document.getElementById('listDocsMaxRows');
        let lastJson = null;

        if (!fetchBtn || !downloadBtn || !urlInput || !output || !activeDomainSelect) return;

        function getActiveDomain() {
            try {
                return String(localStorage.getItem('active_domain') || '').trim();
            } catch (_) {
                return '';
            }
        }

        function setActiveDomain(domain) {
            try {
                const val = String(domain || '').trim();
                if (val) {
                    localStorage.setItem('active_domain', val);
                } else {
                    localStorage.removeItem('active_domain');
                }
            } catch (_) {
                // no-op
            }
        }

        function syncActiveDomainSelect(domain) {
            const val = String(domain || '').trim();
            const hasOption = Array.from(activeDomainSelect.options).some((option) => option.value === val);
            if (!val || hasOption) {
                activeDomainSelect.value = val;
            }
        }

        async function initDomainContext() {
            try {
                const resp = await fetch('/api/ui/runtime-context');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const ctx = await resp.json();
                const domains = Array.isArray(ctx.domains) ? ctx.domains : [];

                while (activeDomainSelect.options.length > 1) {
                    activeDomainSelect.remove(1);
                }

                domains.forEach((domain) => {
                    if (domain && domain !== 'default') {
                        const option = document.createElement('option');
                        option.value = domain;
                        option.textContent = domain;
                        activeDomainSelect.appendChild(option);
                    }
                });

                const backendDomain = String(ctx.active_domain || '').trim();
                const localDomain = getActiveDomain();
                const selected = localDomain && domains.includes(localDomain)
                    ? localDomain
                    : backendDomain || '';
                activeDomainSelect.value = selected;
                setActiveDomain(selected);
            } catch (error) {
                const initialDomain = getActiveDomain();
                activeDomainSelect.value = initialDomain || '';
                console.warn('Failed to initialize runtime domain context:', error);
            }

            activeDomainSelect.addEventListener('change', () => {
                setActiveDomain(activeDomainSelect.value);
            });

            window.addEventListener('storage', (event) => {
                if (event.key === 'active_domain') {
                    syncActiveDomainSelect(event.newValue);
                }
            });
        }

        function aggregateByBaseUrl(items) {
            const aggregated = {};
            items.forEach(item => {
                const baseUrl = item.base_url || 'Unknown';
                if (!aggregated[baseUrl]) {
                    aggregated[baseUrl] = {
                        base_url: baseUrl,
                        title: item.title || baseUrl,
                        total_chunks: 0,
                        updated_at: item.updated_at || new Date().toISOString(),
                        count: 0
                    };
                }
                aggregated[baseUrl].total_chunks += parseInt(item.total_chunks || 0, 10);
                if (item.updated_at && item.updated_at > aggregated[baseUrl].updated_at) {
                    aggregated[baseUrl].updated_at = item.updated_at;
                }
                aggregated[baseUrl].count++;
            });
            return Object.values(aggregated);
        }

        async function fetchDebug() {
            try {
                const url = (urlInput.value || '').trim();
                const activeDomain = getActiveDomain();
                const domainPart = activeDomain ? `${url ? '&' : '?'}active_domain=${encodeURIComponent(activeDomain)}` : '';
                const qs = `${url ? `?url=${encodeURIComponent(url)}` : ''}${domainPart}`;
                const resp = await fetch(`/list-docs-data${qs}`);
                const data = await resp.json();
                lastJson = data;

                const items = (Array.isArray(data?.documents) ? data.documents : []);
                const aggregatedItems = aggregateByBaseUrl(items);

                const table = document.createElement('table');
                table.style.width = '100%';
                table.style.borderCollapse = 'collapse';

                const thead = document.createElement('thead');
                const headerRow = document.createElement('tr');
                ['Base URL', 'Title', 'Chunks', 'Updated At'].forEach(h => {
                    const th = document.createElement('th');
                    th.textContent = h;
                    th.style.textAlign = 'left';
                    th.style.padding = '6px 8px';
                    headerRow.appendChild(th);
                });
                thead.appendChild(headerRow);
                table.appendChild(thead);

                const maxDisplay = Math.max(1, Math.min(parseInt(maxRowsInput?.value || '20', 10), aggregatedItems.length));
                const showing = document.getElementById('listDocsShowingCount');
                showing.textContent = `Showing ${maxDisplay} out of ${aggregatedItems.length} unique base URLs (${items.length} total documents)`;

                const tbody = document.createElement('tbody');
                aggregatedItems.slice(0, maxDisplay).forEach(d => {
                    const tr = document.createElement('tr');
                    [d.base_url, d.title, d.count, d.updated_at].forEach(val => {
                        const td = document.createElement('td');
                        td.style.padding = '6px 8px';

                        if (val === d.base_url) {
                            const container = document.createElement('div');
                            container.className = 'url-cell';
                            container.style.maxWidth = '500px';

                            const urlSpan = document.createElement('span');
                            urlSpan.className = 'url-text';
                            urlSpan.textContent = val != null ? val : '';
                            urlSpan.title = d.base_url;
                            container.appendChild(urlSpan);

                            const copyBtn = document.createElement('span');
                            copyBtn.className = 'copy-btn';
                            copyBtn.textContent = 'Copy';
                            copyBtn.onclick = (e) => {
                                e.stopPropagation();
                                const textToCopy = d.base_url;
                                const originalText = copyBtn.textContent;

                                if (navigator.clipboard) {
                                    navigator.clipboard.writeText(textToCopy).then(() => {
                                        showCopySuccess();
                                    }).catch(() => {
                                        fallbackCopyTextToClipboard(textToCopy);
                                    });
                                } else {
                                    fallbackCopyTextToClipboard(textToCopy);
                                }

                                function showCopySuccess() {
                                    copyBtn.textContent = 'Copied!';
                                    copyBtn.style.backgroundColor = '#10B981';
                                    setTimeout(() => {
                                        copyBtn.textContent = originalText;
                                        copyBtn.style.backgroundColor = '';
                                    }, 2000);
                                }

                                function fallbackCopyTextToClipboard(text) {
                                    const textArea = document.createElement('textarea');
                                    textArea.value = text;
                                    textArea.style.position = 'fixed';
                                    textArea.style.left = '-9999px';
                                    textArea.style.top = '0';
                                    document.body.appendChild(textArea);
                                    textArea.focus();
                                    textArea.select();

                                    try {
                                        const successful = document.execCommand('copy');
                                        if (successful) {
                                            showCopySuccess();
                                        } else {
                                            showCopyError();
                                        }
                                    } catch (err) {
                                        console.error('Fallback copy failed:', err);
                                        showCopyError();
                                    }

                                    document.body.removeChild(textArea);
                                }

                                function showCopyError() {
                                    copyBtn.textContent = 'Press Ctrl+C';
                                    copyBtn.style.backgroundColor = '#F59E0B';

                                    const range = document.createRange();
                                    range.selectNodeContents(urlSpan);
                                    const selection = window.getSelection();
                                    selection.removeAllRanges();
                                    selection.addRange(range);

                                    setTimeout(() => {
                                        copyBtn.textContent = originalText;
                                        copyBtn.style.backgroundColor = '';
                                        selection.removeAllRanges();
                                    }, 2000);
                                }
                            };
                            container.appendChild(copyBtn);

                            td.appendChild(container);
                            td.style.position = 'relative';
                            td.style.overflow = 'hidden';
                        } else {
                            td.textContent = val != null ? val : '';
                        }

                        tr.appendChild(td);
                    });
                    tbody.appendChild(tr);
                });
                table.appendChild(tbody);

                output.innerHTML = '';
                output.appendChild(table);
            } catch (e) {
                output.textContent = `Error: ${e}`;
                downloadBtn.disabled = false;
            }
        }

        async function downloadJson() {
            downloadBtn.disabled = true;
            output.textContent = 'Preparing download...';

            try {
                let dataToDownload = lastJson;
                if (!dataToDownload) {
                    const activeDomain = getActiveDomain();
                    const qs = activeDomain ? `?active_domain=${encodeURIComponent(activeDomain)}` : '';
                    const resp = await fetch(`/list-docs-data${qs}`);
                    if (!resp.ok) {
                        const errorText = await resp.text();
                        throw new Error(`HTTP ${resp.status}: ${errorText}`);
                    }
                    dataToDownload = await resp.json();
                    lastJson = dataToDownload;
                }

                const jsonString = JSON.stringify(dataToDownload, null, 2);
                const blob = new Blob([jsonString], { type: 'application/json;charset=utf-8' });
                const url = window.URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = url;
                link.download = `list_docs_${new Date().toISOString().slice(0, 10)}.json`;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                window.URL.revokeObjectURL(url);
                output.textContent = 'Download started.';
            } catch (err) {
                console.error('Download error:', err);
                output.textContent = `Download failed: ${err?.message || 'Unknown error'}`;
            } finally {
                downloadBtn.disabled = false;
            }
        }

        initDomainContext();
        fetchBtn.addEventListener('click', fetchDebug);
        downloadBtn.addEventListener('click', downloadJson);
    }

    // Debug index panel logic
    function initDebugIndexPanel() {
        const fetchBtn = document.getElementById('debugIndexFetchBtn');
        const downloadBtn = document.getElementById('debugIndexDownloadBtn');
        const urlInput = document.getElementById('debugIndexUrl');
        const output = document.getElementById('debugIndexOutput');
        const activeDomainSelect = document.getElementById('debugIndexActiveDomain');
        const maxRowsInput = document.getElementById('debugIndexMaxRows');
        let lastJson = null;

        if (!fetchBtn || !downloadBtn || !urlInput || !output || !activeDomainSelect) return;

        function getActiveDomain() {
            try {
                return String(localStorage.getItem('active_domain') || '').trim();
            } catch (_) {
                return '';
            }
        }

        function setActiveDomain(domain) {
            try {
                const val = String(domain || '').trim();
                if (val) {
                    localStorage.setItem('active_domain', val);
                } else {
                    localStorage.removeItem('active_domain');
                }
            } catch (_) {
                // no-op
            }
        }

        function syncActiveDomainSelect(domain) {
            const val = String(domain || '').trim();
            const hasOption = Array.from(activeDomainSelect.options).some((option) => option.value === val);
            if (!val || hasOption) {
                activeDomainSelect.value = val;
            }
        }

        async function initDomainContext() {
            try {
                const resp = await fetch('/api/ui/runtime-context');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const ctx = await resp.json();
                const domains = Array.isArray(ctx.domains) ? ctx.domains : [];

                while (activeDomainSelect.options.length > 1) {
                    activeDomainSelect.remove(1);
                }

                domains.forEach((domain) => {
                    if (domain && domain !== 'default') {
                        const option = document.createElement('option');
                        option.value = domain;
                        option.textContent = domain;
                        activeDomainSelect.appendChild(option);
                    }
                });

                const backendDomain = String(ctx.active_domain || '').trim();
                const localDomain = getActiveDomain();
                const selected = localDomain && domains.includes(localDomain)
                    ? localDomain
                    : backendDomain || '';
                activeDomainSelect.value = selected;
                setActiveDomain(selected);
            } catch (error) {
                const initialDomain = getActiveDomain();
                activeDomainSelect.value = initialDomain || '';
                console.warn('Failed to initialize runtime domain context:', error);
            }

            activeDomainSelect.addEventListener('change', () => {
                setActiveDomain(activeDomainSelect.value);
            });

            window.addEventListener('storage', (event) => {
                if (event.key === 'active_domain') {
                    syncActiveDomainSelect(event.newValue);
                }
            });
        }

        function pickPrimaryArray(data) {
            if (!data) return [];
            if (Array.isArray(data)) return data;
            const candidates = ['documents', 'chunks', 'items', 'results', 'points', 'payloads', 'data', 'list'];
            for (const k of candidates) {
                if (data && typeof data === 'object' && Array.isArray(data[k])) return data[k];
            }
            let best = [];
            let maxLen = 0;
            const seen = new Set();
            function dfs(node) {
                if (!node || typeof node !== 'object') return;
                if (seen.has(node)) return;
                seen.add(node);
                if (Array.isArray(node)) {
                    if (node.length > maxLen) {
                        best = node;
                        maxLen = node.length;
                    }
                    for (const el of node) dfs(el);
                    return;
                }
                for (const v of Object.values(node)) dfs(v);
            }
            dfs(data);
            return best;
        }

        async function fetchDebug() {
            try {
                const url = (urlInput.value || '').trim();
                const params = new URLSearchParams();
                if (url) params.set('url', url);
                const activeDomain = getActiveDomain();
                if (activeDomain) params.set('active_domain', activeDomain);
                const qs = params.toString() ? `?${params.toString()}` : '';
                const resp = await fetch(`/debug-index${qs}`);
                const data = await resp.json();
                lastJson = data;

                let maxRows = parseInt(maxRowsInput?.value || '10', 10);
                if (!Number.isFinite(maxRows) || maxRows < 1) maxRows = 10;

                const primary = pickPrimaryArray(data);
                const docCount = primary.length;
                document.getElementById('debugIndexResultCount').textContent = `(${docCount} documents)`;

                const jsonStr = JSON.stringify(data, (key, value) => {
                    if (typeof value === 'object' && value !== null) {
                        if (Array.isArray(value) && value.length > maxRows) {
                            return [...value.slice(0, maxRows), `... ${value.length - maxRows} more items`];
                        }
                    }
                    return value;
                }, 2);

                output.textContent = jsonStr;
                downloadBtn.disabled = false;

                const truncatedWarning = document.getElementById('debugIndexTruncatedWarning');
                const showingCount = document.getElementById('debugIndexShowingCount');

                if (docCount > maxRows) {
                    showingCount.textContent = `Showing ${Math.min(maxRows, docCount)} of ${docCount} document chunks`;
                    truncatedWarning.classList.remove('hidden');
                } else {
                    showingCount.textContent = `Showing all ${docCount} document chunks`;
                    truncatedWarning.classList.add('hidden');
                }
            } catch (e) {
                output.textContent = `Error: ${e}`;
                downloadBtn.disabled = true;
            }
        }

        function downloadJson() {
            if (!lastJson) return;
            const blob = new Blob([JSON.stringify(lastJson, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'debug_index.json';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
        }

        initDomainContext();
        fetchBtn.addEventListener('click', fetchDebug);
        downloadBtn.addEventListener('click', downloadJson);
    }

    // Search panel logic
    function initSearchPanel() {
        const searchSubmitBtn = document.getElementById('searchSubmitBtn');
        const searchResults = document.getElementById('searchResults');
        const activeDomainSelect = document.getElementById('searchActiveDomain');

        if (!searchSubmitBtn || !searchResults || !activeDomainSelect) return;

        function getActiveDomain() {
            try {
                return String(localStorage.getItem('active_domain') || '').trim();
            } catch (_) {
                return '';
            }
        }

        function setActiveDomain(domain) {
            try {
                const val = String(domain || '').trim();
                if (val) {
                    localStorage.setItem('active_domain', val);
                } else {
                    localStorage.removeItem('active_domain');
                }
            } catch (_) {
                // no-op
            }
        }

        function syncActiveDomainSelect(domain) {
            const val = String(domain || '').trim();
            const hasOption = Array.from(activeDomainSelect.options).some((option) => option.value === val);
            if (!val || hasOption) {
                activeDomainSelect.value = val;
            }
        }

        async function initDomainContext() {
            try {
                const resp = await fetch('/api/ui/runtime-context');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const ctx = await resp.json();
                const domains = Array.isArray(ctx.domains) ? ctx.domains : [];

                while (activeDomainSelect.options.length > 1) {
                    activeDomainSelect.remove(1);
                }

                domains.forEach((domain) => {
                    if (domain && domain !== 'default') {
                        const option = document.createElement('option');
                        option.value = domain;
                        option.textContent = domain;
                        activeDomainSelect.appendChild(option);
                    }
                });

                const backendDomain = String(ctx.active_domain || '').trim();
                const localDomain = getActiveDomain();
                const selected = localDomain && domains.includes(localDomain)
                    ? localDomain
                    : backendDomain || '';
                activeDomainSelect.value = selected;
                setActiveDomain(selected);
            } catch (error) {
                const initialDomain = getActiveDomain();
                activeDomainSelect.value = initialDomain || '';
                console.warn('Failed to initialize runtime domain context:', error);
            }

            activeDomainSelect.addEventListener('change', () => {
                setActiveDomain(activeDomainSelect.value);
            });

            window.addEventListener('storage', (event) => {
                if (event.key === 'active_domain') {
                    syncActiveDomainSelect(event.newValue);
                }
            });
        }

        function displaySearchResults(results) {
            if (!results || results.length === 0) {
                searchResults.innerHTML = `
                    <div class="text-center py-4 text-gray-500">
                        No results found
                    </div>
                `;
                return;
            }

            const resultsHtml = results.map(result => `
                <div class="search-result bg-gray-50 p-4 rounded mb-4">
                    <div class="metadata text-sm text-gray-600 mb-2">
                        <span class="url">${result.payload?.url || 'N/A'}</span>
                        <span class="section">• ${result.payload?.section || 'N/A'}</span>
                        <span class="subsection">• ${result.payload?.subsection || 'N/A'}</span>
                        <span class="chunk-index">• Chunk: ${result.payload?.chunk_index || 'N/A'}</span>
                        <span class="score">• Score: ${(result.score * 100).toFixed(2)}%</span>
                    </div>
                    <div class="chunk-content text-gray-800">
                        ${result.payload?.text || 'No content'}
                    </div>
                </div>
            `).join('');

            searchResults.innerHTML = resultsHtml;
        }

        async function performSearch() {
            const query = document.getElementById('searchQuery').value;
            const limit = parseInt(document.getElementById('searchLimit').value);
            const urlFilter = document.getElementById('searchUrlFilter').value;
            const searchMode = String(document.getElementById('searchMode')?.value || 'dense').trim().toLowerCase();
            const scoreThresholdRaw = document.getElementById('searchScoreThreshold')?.value;
            const scoreThreshold = scoreThresholdRaw !== undefined && scoreThresholdRaw !== null
                ? parseFloat(scoreThresholdRaw)
                : undefined;
            const exactMatch = document.getElementById('searchExactMatch')?.value === 'true';
            const withPayload = document.getElementById('searchWithPayload')?.value === 'true';

            if (!query.trim()) {
                alert('Please enter a search query');
                return;
            }

            try {
                searchResults.innerHTML = '<div class="text-center py-4">Searching...</div>';

                const payload = {
                    query: query.trim() || null,
                    query_filter: urlFilter ? { url: urlFilter } : null,
                    limit,
                    active_domain: getActiveDomain() || undefined,
                    search_mode: searchMode,
                    exact_match: exactMatch,
                    with_payload: withPayload,
                };
                if (Number.isFinite(scoreThreshold)) {
                    payload.score_threshold = scoreThreshold;
                }

                const response = await fetch('/search', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(payload)
                });

                const responseData = await response.json();
                const data = responseData;

                if (!response.ok) {
                    throw new Error(data.detail || 'Search failed');
                }

                displaySearchResults(data.results);
            } catch (error) {
                searchResults.innerHTML = `
                    <div class="text-center py-4 text-red-600">
                        Error: ${error.message}
                    </div>
                `;
            }
        }

        initDomainContext();
        searchSubmitBtn.addEventListener('click', performSearch);
    }

    // Delete index panel logic
    function initDeleteIndexPanel() {
        const fetchPreviewBtn = document.getElementById('deleteIndexFetchPreviewBtn');
        const confirmDeleteBtn = document.getElementById('deleteIndexConfirmDeleteBtn');
        const urlInput = document.getElementById('deleteIndexUrl');
        const sampleLimitInput = document.getElementById('deleteIndexSampleLimit');
        const previewOutput = document.getElementById('deleteIndexPreviewOutput');
        const previewSummary = document.getElementById('deleteIndexPreviewSummary');
        const statusMessage = document.getElementById('deleteIndexStatusMessage');
        const activeDomainSelect = document.getElementById('deleteIndexActiveDomain');

        let lastPreview = null;
        let lastRequest = { url: null, base_url: null };

        if (!fetchPreviewBtn || !confirmDeleteBtn || !urlInput || !activeDomainSelect) return;

        function getActiveDomain() {
            try {
                return String(localStorage.getItem('active_domain') || '').trim();
            } catch (_) {
                return '';
            }
        }

        function setActiveDomain(domain) {
            try {
                const val = String(domain || '').trim();
                if (val) {
                    localStorage.setItem('active_domain', val);
                } else {
                    localStorage.removeItem('active_domain');
                }
            } catch (_) {
                // no-op
            }
        }

        function syncActiveDomainSelect(domain) {
            const val = String(domain || '').trim();
            const hasOption = Array.from(activeDomainSelect.options).some((option) => option.value === val);
            if (!val || hasOption) {
                activeDomainSelect.value = val;
            }
        }

        async function initDomainContext() {
            try {
                const resp = await fetch('/api/ui/runtime-context');
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const ctx = await resp.json();
                const domains = Array.isArray(ctx.domains) ? ctx.domains : [];

                while (activeDomainSelect.options.length > 1) {
                    activeDomainSelect.remove(1);
                }

                domains.forEach((domain) => {
                    if (domain && domain !== 'default') {
                        const option = document.createElement('option');
                        option.value = domain;
                        option.textContent = domain;
                        activeDomainSelect.appendChild(option);
                    }
                });

                const backendDomain = String(ctx.active_domain || '').trim();
                const localDomain = getActiveDomain();
                const selected = localDomain && domains.includes(localDomain)
                    ? localDomain
                    : backendDomain || '';
                activeDomainSelect.value = selected;
                setActiveDomain(selected);
            } catch (error) {
                const initialDomain = getActiveDomain();
                activeDomainSelect.value = initialDomain || '';
                console.warn('Failed to initialize runtime domain context:', error);
            }

            activeDomainSelect.addEventListener('change', () => {
                setActiveDomain(activeDomainSelect.value);
            });

            window.addEventListener('storage', (event) => {
                if (event.key === 'active_domain') {
                    syncActiveDomainSelect(event.newValue);
                }
            });
        }

        function setConfirmDeleteEnabled(enabled) {
            if (!confirmDeleteBtn) return;
            confirmDeleteBtn.disabled = !enabled;
            if (enabled) {
                confirmDeleteBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            } else {
                if (!confirmDeleteBtn.classList.contains('opacity-50')) {
                    confirmDeleteBtn.classList.add('opacity-50');
                }
                if (!confirmDeleteBtn.classList.contains('cursor-not-allowed')) {
                    confirmDeleteBtn.classList.add('cursor-not-allowed');
                }
            }
        }

        function setStatus(text, type = 'info') {
            if (!statusMessage) return;
            statusMessage.textContent = text || '';
            statusMessage.className = 'mb-2 text-sm ' + (type === 'error'
                ? 'text-red-700'
                : type === 'success'
                ? 'text-green-700'
                : 'text-gray-700');
        }

        urlInput.addEventListener('input', () => {
            lastPreview = null;
            lastRequest = { url: null, base_url: null };
            setConfirmDeleteEnabled(false);
        });

        async function fetchPreview() {
            try {
                const url = (urlInput.value || '').trim();
                if (!url) {
                    setStatus('Please enter a URL to fetch.', 'error');
                    return;
                }

                let sampleLimit = parseInt(sampleLimitInput.value || '5', 10);
                if (!Number.isFinite(sampleLimit) || sampleLimit < 1) sampleLimit = 5;

                setStatus('Fetching preview from server...', 'info');
                setConfirmDeleteEnabled(false);

                const activeDomain = getActiveDomain();
                const domainPart = activeDomain ? `&active_domain=${encodeURIComponent(activeDomain)}` : '';
                const qs = `?url=${encodeURIComponent(url)}&sample_limit=${encodeURIComponent(sampleLimit)}${domainPart}`;
                const resp = await fetch(`/admin/delete-preview${qs}`);
                if (!resp.ok) {
                    const text = await resp.text();
                    throw new Error(`Server error (${resp.status}): ${text}`);
                }

                const data = await resp.json();
                lastPreview = data;
                lastRequest = { url: data.input_url || url, base_url: data.base_url || null };

                const total = data.total_chunks || 0;
                previewSummary.textContent = `(total chunks: ${total}, showing up to ${data.sample_limit || sampleLimit})`;

                if (total === 0) {
                    setStatus('No chunks found for this URL / base URL. Nothing to delete.', 'info');
                    setConfirmDeleteEnabled(false);
                } else {
                    setStatus(`Found ${total} chunk(s). Review the sample below, then click Confirm Delete.`, 'info');
                    setConfirmDeleteEnabled(true);
                }

                previewOutput.textContent = JSON.stringify(data, null, 2);
            } catch (e) {
                console.error(e);
                setStatus(`Error fetching preview: ${e.message || e}`, 'error');
                previewOutput.textContent = '';
                previewSummary.textContent = '';
                setConfirmDeleteEnabled(false);
            }
        }

        async function confirmDelete() {
            try {
                if (!lastRequest.url && !lastRequest.base_url) {
                    setStatus('Fetch a preview before deleting.', 'error');
                    return;
                }
                setStatus('Deleting documents from Qdrant...', 'info');
                setConfirmDeleteEnabled(false);

                const resp = await fetch('/admin/delete-by-base-url', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        url: lastRequest.url,
                        active_domain: getActiveDomain() || undefined,
                    }),
                });

                if (!resp.ok) {
                    const text = await resp.text();
                    throw new Error(`Server error (${resp.status}): ${text}`);
                }

                const result = await resp.json();
                setStatus(`Deleted ${result.deleted_points || 0} chunk(s) for base URL: ${result.base_url}`, 'success');
            } catch (e) {
                console.error(e);
                setStatus(`Error deleting documents: ${e.message || e}`, 'error');
            }
        }

        initDomainContext();
        fetchPreviewBtn.addEventListener('click', fetchPreview);
        confirmDeleteBtn.addEventListener('click', confirmDelete);
    }
});
