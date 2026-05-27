"""Embedded JavaScript for the demo-mode browser panel."""

DEMO_PANEL_SCRIPT = r"""
(() => {
  const HUD_CONFIG = __AGENTYC_HUD_CONFIG_PLACEHOLDER__;
  const PANEL_ID = `agentyc-hud-panel-${HUD_CONFIG.sessionId}`;
  const STYLE_ID = `${PANEL_ID}-style`;
  const STORAGE_KEY = `${PANEL_ID}-state`;
  const MAX_ROWS = 8;

  if (window.__agentycHudBooted === HUD_CONFIG.sessionId) {
    return;
  }
  window.__agentycHudBooted = HUD_CONFIG.sessionId;

  const defaultState = {
    current: 'Waiting for the next action',
    status: 'Live',
    rows: [],
    reportOpen: false,
  };

  function loadState() {
    try {
      const raw = window.sessionStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return { ...defaultState };
      }
      const parsed = JSON.parse(raw);
      return {
        ...defaultState,
        ...parsed,
        rows: Array.isArray(parsed?.rows) ? parsed.rows.slice(0, MAX_ROWS) : [],
      };
    } catch {
      return { ...defaultState };
    }
  }

  let state = loadState();

  function saveState() {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Ignore storage failures on restricted pages.
    }
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) {
      return;
    }

    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      #${PANEL_ID} {
        position: fixed;
        top: 12px;
        right: 12px;
        width: 320px;
        z-index: 2147483647;
        background: rgba(10, 10, 12, 0.82);
        border: 1px solid rgba(255, 255, 255, 0.18);
        color: #f4f4f5;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        font-size: 12px;
        line-height: 1.45;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.24);
        backdrop-filter: blur(10px);
      }
      #${PANEL_ID} * {
        box-sizing: border-box;
      }
      #${PANEL_ID} .agentyc-hud-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 8px 10px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.12);
      }
      #${PANEL_ID} .agentyc-hud-title {
        display: flex;
        flex-direction: column;
        gap: 2px;
        min-width: 0;
      }
      #${PANEL_ID} .agentyc-hud-title strong {
        font-size: 11px;
        letter-spacing: 0.06em;
      }
      #${PANEL_ID} .agentyc-hud-status {
        color: rgba(255, 255, 255, 0.62);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      #${PANEL_ID} .agentyc-hud-actions {
        display: flex;
        align-items: center;
        gap: 6px;
        position: relative;
      }
      #${PANEL_ID} button {
        appearance: none;
        border: 1px solid rgba(255, 255, 255, 0.18);
        background: rgba(255, 255, 255, 0.04);
        color: #f4f4f5;
        font: inherit;
        padding: 4px 8px;
        cursor: pointer;
      }
      #${PANEL_ID} button:hover {
        background: rgba(255, 255, 255, 0.1);
      }
      #${PANEL_ID} .agentyc-hud-body {
        padding: 10px;
        display: grid;
        gap: 10px;
      }
      #${PANEL_ID} .agentyc-hud-current {
        padding: 8px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        background: rgba(255, 255, 255, 0.04);
      }
      #${PANEL_ID} .agentyc-hud-label {
        display: block;
        margin-bottom: 6px;
        color: rgba(255, 255, 255, 0.62);
        font-size: 10px;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }
      #${PANEL_ID} .agentyc-hud-current-text {
        color: #fafafa;
        word-break: break-word;
      }
      #${PANEL_ID} .agentyc-hud-feed {
        display: grid;
        gap: 6px;
      }
      #${PANEL_ID} .agentyc-hud-row {
        display: grid;
        grid-template-columns: 12px 1fr auto;
        gap: 8px;
        align-items: start;
        padding: 6px 8px;
        border: 1px solid rgba(255, 255, 255, 0.08);
        background: rgba(255, 255, 255, 0.03);
      }
      #${PANEL_ID} .agentyc-hud-dot {
        width: 8px;
        height: 8px;
        margin-top: 4px;
        background: #60a5fa;
      }
      #${PANEL_ID} .agentyc-hud-row[data-kind="tool_done"] .agentyc-hud-dot {
        background: #34d399;
      }
      #${PANEL_ID} .agentyc-hud-row[data-kind="tool_error"] .agentyc-hud-dot {
        background: #f87171;
      }
      #${PANEL_ID} .agentyc-hud-row[data-kind="intent"] .agentyc-hud-dot {
        background: #fbbf24;
      }
      #${PANEL_ID} .agentyc-hud-text {
        word-break: break-word;
      }
      #${PANEL_ID} .agentyc-hud-meta {
        color: rgba(255, 255, 255, 0.5);
        white-space: nowrap;
      }
      #${PANEL_ID} .agentyc-hud-menu {
        position: absolute;
        top: calc(100% + 6px);
        right: 0;
        width: 148px;
        display: none;
        grid-auto-flow: row;
        gap: 4px;
        padding: 6px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        background: rgba(12, 12, 14, 0.96);
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
      }
      #${PANEL_ID} .agentyc-hud-menu[data-open="true"] {
        display: grid;
      }
      #${PANEL_ID} .agentyc-hud-menu button {
        text-align: left;
        width: 100%;
      }
      #${PANEL_ID} .agentyc-hud-empty {
        color: rgba(255, 255, 255, 0.45);
        padding: 10px 8px;
        border: 1px dashed rgba(255, 255, 255, 0.12);
      }
    `;
    document.documentElement.appendChild(style);
  }

  function createEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (typeof text === 'string') {
      node.textContent = text;
    }
    return node;
  }

  function ensurePanel() {
    ensureStyle();

    let panel = document.getElementById(PANEL_ID);
    if (panel) {
      return panel;
    }

    panel = createEl('aside', '');
    panel.id = PANEL_ID;

    const header = createEl('div', 'agentyc-hud-header');
    const title = createEl('div', 'agentyc-hud-title');
    title.appendChild(createEl('strong', '', 'AGENTYC MCP'));
    title.appendChild(createEl('div', 'agentyc-hud-status', state.status));

    const actions = createEl('div', 'agentyc-hud-actions');
    const reportButton = createEl('button', '', 'REPORT');
    reportButton.type = 'button';
    reportButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      state.reportOpen = !state.reportOpen;
      render();
    });

    const menu = createEl('div', 'agentyc-hud-menu');
    menu.dataset.open = String(state.reportOpen);
    const actionsByKind = {
      bug: 'Bug report',
      feature: 'Feature request',
      security: 'Report privately',
    };
    Object.entries(actionsByKind).forEach(([kind, label]) => {
      const button = createEl('button', '', label);
      button.type = 'button';
      button.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        state.reportOpen = false;
        render();
        await openReport(kind);
      });
      menu.appendChild(button);
    });

    actions.appendChild(reportButton);
    actions.appendChild(menu);
    header.appendChild(title);
    header.appendChild(actions);

    const body = createEl('div', 'agentyc-hud-body');
    const current = createEl('section', 'agentyc-hud-current');
    current.appendChild(createEl('span', 'agentyc-hud-label', 'Current'));
    current.appendChild(createEl('div', 'agentyc-hud-current-text', state.current));
    const feed = createEl('section', 'agentyc-hud-feed');

    body.appendChild(current);
    body.appendChild(feed);
    panel.appendChild(header);
    panel.appendChild(body);
    document.documentElement.appendChild(panel);

    document.addEventListener('click', () => {
      if (!state.reportOpen) {
        return;
      }
      state.reportOpen = false;
      render();
    });

    render();
    return panel;
  }

  function sanitizeLocation(href) {
    try {
      const url = new URL(href);
      return `${url.origin}${url.pathname}`;
    } catch {
      return '';
    }
  }

  function escapeLine(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  function recentActivityLines() {
    return state.rows.slice(0, 6).map((row) => {
      const duration = row.durationLabel ? ` (${row.durationLabel})` : '';
      return `- ${row.label}${duration}`;
    });
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', 'true');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    (document.body || document.documentElement).appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    textarea.remove();
  }

  function buildReportText(kind) {
    const location = sanitizeLocation(window.location.href);
    const title = escapeLine(document.title);
    const lines = [
      `Report type: ${kind}`,
      `Agentyc version: ${HUD_CONFIG.version}`,
      `Session id: ${HUD_CONFIG.sessionId}`,
      `Page: ${location || 'n/a'}`,
    ];

    if (title) {
      lines.push(`Title: ${title}`);
    }

    lines.push('', 'Recent HUD activity:');
    const activityLines = recentActivityLines();
    if (activityLines.length === 0) {
      lines.push('- none captured yet');
    } else {
      lines.push(...activityLines);
    }

    lines.push('', 'Notes:');
    lines.push('- Add reproduction steps here.');
    return lines.join('\n');
  }

  async function openReport(kind) {
    const reportText = buildReportText(kind);
    try {
      await copyText(reportText);
      state.status = 'Copied sanitized report context';
    } catch {
      state.status = 'Opened report link';
    }
    render();

    const url = HUD_CONFIG.feedbackUrls?.[kind];
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  }

  function durationLabel(durationMs) {
    if (typeof durationMs !== 'number' || Number.isNaN(durationMs)) {
      return '';
    }
    if (durationMs >= 1000) {
      return `${(durationMs / 1000).toFixed(durationMs >= 10000 ? 0 : 1)}s`;
    }
    return `${Math.round(durationMs)}ms`;
  }

  function normalizeEntry(detail) {
    if (!detail || typeof detail.message !== 'string' || !detail.message.trim()) {
      return null;
    }

    const metadata = detail.metadata || {};
    const kind = typeof metadata.kind === 'string'
      ? metadata.kind
      : detail.level === 'error'
        ? 'tool_error'
        : detail.level === 'success'
          ? 'tool_done'
          : 'browser_event';
    const duration = typeof metadata.duration_ms === 'number' ? metadata.duration_ms : null;

    return {
      kind,
      label: escapeLine(detail.message),
      durationLabel: durationLabel(duration),
      timestamp: detail.timestamp || new Date().toISOString(),
    };
  }

  function applyEntry(entry) {
    state.current = entry.label;
    if (entry.kind === 'tool_error') {
      state.status = 'Attention needed';
    } else if (entry.kind === 'tool_done') {
      state.status = 'Updated';
    } else if (entry.kind === 'intent') {
      state.status = 'Intent updated';
    } else {
      state.status = 'Live';
    }

    state.rows = [entry, ...state.rows].slice(0, MAX_ROWS);
    saveState();
    render();
  }

  function render() {
    const panel = ensurePanel();
    panel.querySelector('.agentyc-hud-status').textContent = state.status;
    panel.querySelector('.agentyc-hud-current-text').textContent = state.current;

    const menu = panel.querySelector('.agentyc-hud-menu');
    menu.dataset.open = String(state.reportOpen);

    const feed = panel.querySelector('.agentyc-hud-feed');
    feed.innerHTML = '';

    if (state.rows.length === 0) {
      feed.appendChild(createEl('div', 'agentyc-hud-empty', 'No visible actions yet.'));
      return;
    }

    state.rows.forEach((row) => {
      const item = createEl('div', 'agentyc-hud-row');
      item.dataset.kind = row.kind;
      item.appendChild(createEl('div', 'agentyc-hud-dot'));
      item.appendChild(createEl('div', 'agentyc-hud-text', row.label));
      item.appendChild(createEl('div', 'agentyc-hud-meta', row.durationLabel || ''));
      feed.appendChild(item);
    });
  }

  window.addEventListener('agentyc-log', (event) => {
    const entry = normalizeEntry(event.detail);
    if (!entry) {
      return;
    }
    applyEntry(entry);
  });

  ensurePanel();
})();
"""
