# AxioLex UI Style Changes - Complete Summary for Application to Other Repositories

## Homepage/Discover Tab Layout
- Restructured to query-first main column plus right-rail parameters
- Query box as visual anchor
- Parameters moved to right-rail panel with gap: 8px
- Replaced number inputs with sliders for temperature/cutoff
- Hybrid search and zero-relevance filtering as inline toggles (toggle-option class)
- Results panel pinned directly under query
- Tabs made thinner
- Query-heading margin-top: 15px

## Font Sizes
- All internal text: 16px
- Main H1 title: 24px
- Query input: 16px
- Toggle-option: font-size 14px, font-weight 400
- Toggle checkbox: 16px x 16px

## Tool Management Page
- Metric cards for stats (total/local/MCP counts)
- Row-cards for tools with source-type pill, inline tool ID, category tag
- Filter toggle (local/MCP) with filter-btn class
- Success banner with icon (later removed icon and border)
- Source file switching: Current Source File on line 1, Switch to on line 2
- Switch File button inline with select dropdown
- Source-file-row select width: 49%, margin-bottom: 8px

## MCP Providers Tab
- Card-based layout with connection health indicators (green plug icon)
- Tool count at a glance
- Dense metadata (transport, endpoint, auth) in labeled sub-grid
- Labeled action buttons instead of icon-only
- "Inspect Tools" changed to "Retrieve Tools"
- Transport, Endpoint, API Key displayed on same line
- Removed "Tools Indexed"
- Removed padding and border from provider-actions
- Sort providers by enabled status first, then by name

## Settings Page
- Consistent theme with settings-section, settings-grid, settings-actions
- Success banner for messages
- Settings field styling with consistent spacing

## Status Page
- Consistent theme with status-section styling
- Cleaner layout matching other pages

## Button Styling
- Primary buttons: background #111111, color white, border-radius 6px, font-weight 500
- Primary hover: background #333333
- Primary disabled: background #cccccc, color #666666, cursor not-allowed
- Secondary buttons: background #f5f3ed, color #111111, border 1px solid #d5d5d5
- Secondary hover: background #e8e4dc
- Secondary disabled: background #f5f3ed, color #999999, border-color #e0e0e0, cursor not-allowed
- Removed search-actions specific button overrides

## Filter Button Styling
- Background #f5f3ed, color #111111, border 1px solid #d5d5d5
- Active state: background #111111, color white, border-color #111111

## Search Results
- Muted min-height: 36px (was 98px)
- Search-results max-height: 3000px (was 600px)
- Card-based layout matching tool-management theme
- Expandable descriptions with -webkit-line-clamp: 1
- Click to expand/collapse (onclick toggle expanded class)
- Metrics grid for data columns
- Tool ID, description (one line), and metrics
- Removed Tool Title
- Hybrid search shows scores in brackets: "Rank 1 (Score: 34.123), 45.67%"

## Success Banner
- Removed success-icon span
- Removed border from .success-banner CSS
- Background #e8f5e9, color #2e7d32

## Color Palette
- Primary black: #111111
- Secondary black: #333333
- Disabled gray: #cccccc
- Disabled text: #666666, #999999
- Beige background: #f5f3ed
- Beige hover: #e8e4dc
- Border color: #d5d5d5, #e0e0e0
- Card border: #e5e5e5
- Success green background: #e8f5e9
- Success green text: #2e7d32
- Muted text: #666
- Primary text: #111
- Secondary text: #444
- Link blue: #1f5faa
- Warning orange: orange
- Error red: red

## CSS Classes Added
- .success-banner, .success-icon
- .metric-cards, .metric-card, .metric-value, .metric-label
- .source-file-row, .current-file-value
- .filter-btn, .filter-btn.active
- .tool-row-card, .source-pill, .category-tag, .tool-actions
- .providers-header, .providers-actions, .providers-list
- .provider-card, .provider-health-icon, .provider-name, .provider-status
- .provider-meta, .provider-meta-item, .provider-meta-label, .provider-meta-value
- .provider-stats, .provider-actions
- .discovery-progress-box, .discovery-progress-title, .discovery-steps
- .discovered-tools-box, .discovered-tools-title, .discovered-tools-list
- .settings-section, .settings-grid, .settings-field, .settings-actions
- .status-section, .status-actions
- .search-results-list, .search-result-card, .search-result-header
- .search-result-info, .search-result-id, .search-result-title, .search-result-description
- .search-result-description.expanded, .search-result-metrics
- .search-result-metric, .search-result-metric-label, .search-result-metric-value
- .search-result-metric-value.highlight

## Key CSS Values
- .parameter-rail gap: 8px
- .query-heading margin-top: 15px
- .toggle-option font-size: 14px, font-weight 400
- .toggle-option input width/height: 16px
- #search-results max-height: 3000px
- #search-results > .muted min-height: 36px
- .source-file-row select width: 49%, margin-bottom: 8px
