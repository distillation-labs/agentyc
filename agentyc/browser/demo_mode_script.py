"""Embedded JavaScript for the demo-mode browser HUD."""

DEMO_PANEL_SCRIPT = r"""
(() => {
  const HUD_CONFIG = __AGENTYC_HUD_CONFIG_PLACEHOLDER__;
  const PANEL_ID = `agentyc-hud-panel-${HUD_CONFIG.sessionId}`;
  const STYLE_ID = `${PANEL_ID}-style`;
  const STORAGE_KEY = `${PANEL_ID}-state`;
  const MAX_ROWS = 10;
  const CHATTY_THRESHOLD_MS = 2000;
  const CHATTY_TOOLS = new Set([
    'browser_find_elements',
    'browser_get_attribute',
    'browser_get_dropdown_options',
    'browser_get_focused_element',
    'browser_get_html',
    'browser_get_state',
    'browser_search_page',
  ]);

  if (window.__agentycHudBooted === HUD_CONFIG.sessionId) {
    return;
  }

  const defaultState = {
    current: 'Waiting for the next action',
    status: 'Live',
    statusKind: 'browser_event',
    rows: [],
    reportOpen: false,
    advanced: false,
    lastTool: '',
    lastArgs: 'none',
    lastSession: HUD_CONFIG.sessionId,
    lastDuration: 'n/a',
    lastError: '',
  };

  function loadState() {
    try {
      const saved = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) || 'null');
      if (!saved || typeof saved !== 'object') {
        return { ...defaultState };
      }
      return {
        ...defaultState,
        ...saved,
        rows: Array.isArray(saved.rows) ? saved.rows.slice(0, MAX_ROWS) : [],
      };
    } catch {
      return { ...defaultState };
    }
  }

  const state = loadState();

  function saveState() {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch {
      // Best effort only.
    }
  }

  function createEl(tag, className, text) {
    const element = document.createElement(tag);
    if (className) {
      element.className = className;
    }
    if (text) {
      element.textContent = text;
    }
    return element;
  }

  function documentRoot() {
    return document.documentElement || document.body || document.head || null;
  }

  function relativeTime(isoString) {
    if (!isoString) {
      return 'just now';
    }
    const delta = Math.max(0, Date.now() - Date.parse(isoString));
    if (delta < 1000) {
      return 'just now';
    }
    if (delta < 60_000) {
      return `${Math.round(delta / 1000)}s ago`;
    }
    return `${Math.round(delta / 60_000)}m ago`;
  }

  function durationLabel(durationMs) {
    if (typeof durationMs !== 'number' || Number.isNaN(durationMs)) {
      return '';
    }
    return durationMs >= 1000 ? `${(durationMs / 1000).toFixed(1)}s` : `${Math.round(durationMs)}ms`;
  }

  function escapeLine(value) {
    if (!value) {
      return '';
    }
    return String(value).replace(/\s+/g, ' ').trim();
  }

  function statusFromKind(kind) {
    if (kind === 'tool_error') {
      return 'Attention needed';
    }
    if (kind === 'tool_done') {
      return 'Updated';
    }
    if (kind === 'tool_start') {
      return 'Running';
    }
    if (kind === 'intent') {
      return 'Intent updated';
    }
    return 'Live';
  }

  function normalizeEntry(detail) {
    const metadata = detail.metadata && typeof detail.metadata === 'object' ? detail.metadata : {};
    const extraDetails = metadata.details && typeof metadata.details === 'object' ? metadata.details : {};
    const durationMs = typeof metadata.duration_ms === 'number' ? metadata.duration_ms : null;
    const kind =
      metadata.kind ||
      (detail.level === 'error' ? 'tool_error' : detail.level === 'success' ? 'tool_done' : 'browser_event');
    return {
      kind,
      label: escapeLine(detail.message) || 'Browser activity',
      timestamp: detail.timestamp || new Date().toISOString(),
      durationMs,
      durationLabel: durationLabel(durationMs),
      tool: typeof metadata.tool_name === 'string' ? metadata.tool_name : '',
      session: typeof metadata.session_id === 'string' ? metadata.session_id : HUD_CONFIG.sessionId,
      argsSummary: typeof extraDetails.args_summary === 'string' ? extraDetails.args_summary : 'none',
      error:
        typeof metadata.error === 'string'
          ? escapeLine(metadata.error)
          : typeof extraDetails.error === 'string'
            ? escapeLine(extraDetails.error)
            : '',
    };
  }

  function shouldSkipEntry(entry) {
    if (!entry.tool || entry.error || !CHATTY_TOOLS.has(entry.tool)) {
      return false;
    }
    if (entry.kind === 'tool_start') {
      return true;
    }
    return entry.kind === 'tool_done' && typeof entry.durationMs === 'number' && entry.durationMs < CHATTY_THRESHOLD_MS;
  }

  function buildReportText() {
    const lines = [
      'Agentyc browser report',
      `Page: ${location.href}`,
      `Current step: ${state.current}`,
      '',
      'Recent activity:',
      ...state.rows.slice(0, 6).map((row) => {
        const suffix = row.durationLabel ? ` (${row.durationLabel})` : '';
        return `- ${row.label}${suffix}`;
      }),
    ];
    return lines.join('\n');
  }

  function openReportWindow() {
    const report = buildReportText();
    const popup = window.open('', '_blank', 'noopener,noreferrer,width=720,height=640');
    if (!popup) {
      navigator.clipboard?.writeText(report).catch(() => {});
      return;
    }
    popup.document.write(
      `<html><head><title>Agentyc Browser Report</title></head><body><pre style="font-family: ui-monospace, SFMono-Regular, monospace; white-space: pre-wrap; padding: 24px; line-height: 1.5;">${report.replace(/</g, '&lt;')}</pre></body></html>`
    );
    popup.document.close();
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) {
      return true;
    }
    const root = documentRoot();
    if (!root) {
      return false;
    }
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      #${PANEL_ID}{position:fixed;top:16px;right:16px;width:360px;max-width:calc(100vw - 32px);padding:12px;background:rgba(6,7,11,.92);color:#f8fafc;border:1px solid rgba(96,165,250,.32);box-shadow:0 18px 40px rgba(2,6,23,.42);z-index:2147483647;font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:12px;line-height:1.45;pointer-events:none;border-radius:10px;backdrop-filter:blur(12px) saturate(125%)}
      #${PANEL_ID} *{box-sizing:border-box}
      #${PANEL_ID}[data-kind="tool_error"]{border-color:rgba(248,113,113,.55)}
      #${PANEL_ID} .agentyc-hud-header,#${PANEL_ID} .agentyc-hud-current,#${PANEL_ID} .agentyc-hud-advanced,#${PANEL_ID} .agentyc-hud-feed{display:grid;gap:8px}
      #${PANEL_ID} .agentyc-hud-header{grid-template-columns:minmax(0,1fr) auto;align-items:start;margin-bottom:10px}
      #${PANEL_ID} .agentyc-hud-title{display:grid;gap:4px;min-width:0}
      #${PANEL_ID} .agentyc-hud-status-line{display:flex;align-items:center;gap:8px;min-width:0}
      #${PANEL_ID} .agentyc-hud-status-dot{width:10px;height:10px;border-radius:999px;background:#38bdf8;box-shadow:0 0 0 4px rgba(56,189,248,.18)}
      #${PANEL_ID} .agentyc-hud-status-dot[data-kind="tool_done"]{background:#34d399;box-shadow:0 0 0 4px rgba(52,211,153,.18)}
      #${PANEL_ID} .agentyc-hud-status-dot[data-kind="tool_start"]{background:#fbbf24;box-shadow:0 0 0 4px rgba(251,191,36,.18)}
      #${PANEL_ID} .agentyc-hud-status-dot[data-kind="tool_error"]{background:#f87171;box-shadow:0 0 0 4px rgba(248,113,113,.18)}
      #${PANEL_ID} .agentyc-hud-status-dot[data-kind="intent"]{background:#a78bfa;box-shadow:0 0 0 4px rgba(167,139,250,.2)}
      #${PANEL_ID} .agentyc-hud-name{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#93c5fd;font-weight:700}
      #${PANEL_ID} .agentyc-hud-status{color:rgba(226,232,240,.82);font-size:12px;min-width:0}
      #${PANEL_ID} .agentyc-hud-actions{display:flex;gap:6px;pointer-events:auto}
      #${PANEL_ID} button{appearance:none;border:1px solid rgba(148,163,184,.3);background:rgba(15,23,42,.82);color:#f8fafc;font:inherit;padding:6px 10px;border-radius:8px;cursor:pointer}
      #${PANEL_ID} button[data-active="true"]{border-color:rgba(96,165,250,.72);background:rgba(30,41,59,.95)}
      #${PANEL_ID} .agentyc-hud-menu{position:absolute;top:44px;right:0;display:none;pointer-events:auto}
      #${PANEL_ID} .agentyc-hud-menu[data-open="true"]{display:block}
      #${PANEL_ID} .agentyc-hud-menu button{width:100%;text-align:left;white-space:nowrap}
      #${PANEL_ID} .agentyc-hud-current{padding:10px;background:linear-gradient(180deg,rgba(96,165,250,.12),rgba(255,255,255,.04));border:1px solid rgba(96,165,250,.24)}
      #${PANEL_ID} .agentyc-hud-label{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:rgba(191,219,254,.75)}
      #${PANEL_ID} .agentyc-hud-current-text{font-size:14px;font-weight:700;color:#fff}
      #${PANEL_ID} .agentyc-hud-advanced{margin-top:10px;padding:10px;border:1px solid rgba(148,163,184,.24);background:rgba(15,23,42,.72);display:none}
      #${PANEL_ID} .agentyc-hud-advanced[data-open="true"]{display:grid}
      #${PANEL_ID} .agentyc-hud-advanced-text{color:rgba(226,232,240,.88);font-family:ui-monospace,SFMono-Regular,monospace;white-space:pre-wrap;word-break:break-word}
      #${PANEL_ID} .agentyc-hud-feed{margin-top:10px}
      #${PANEL_ID} .agentyc-hud-row{display:grid;grid-template-columns:12px minmax(0,1fr) auto;gap:8px;align-items:start;padding:8px 10px;background:rgba(255,255,255,.035);border:1px solid rgba(148,163,184,.14)}
      #${PANEL_ID} .agentyc-hud-row-dot{width:8px;height:8px;border-radius:999px;background:#38bdf8;margin-top:5px}
      #${PANEL_ID} .agentyc-hud-row[data-kind="tool_done"] .agentyc-hud-row-dot{background:#34d399}
      #${PANEL_ID} .agentyc-hud-row[data-kind="tool_start"] .agentyc-hud-row-dot{background:#fbbf24}
      #${PANEL_ID} .agentyc-hud-row[data-kind="tool_error"] .agentyc-hud-row-dot{background:#f87171}
      #${PANEL_ID} .agentyc-hud-row[data-kind="intent"] .agentyc-hud-row-dot{background:#a78bfa}
      #${PANEL_ID} .agentyc-hud-row-text{overflow:hidden;text-overflow:ellipsis}
      #${PANEL_ID} .agentyc-hud-row-meta{color:rgba(148,163,184,.88);white-space:nowrap}
    `;
    root.appendChild(style);
    return true;
  }

  function ensurePanel() {
    if (!ensureStyle()) {
      return null;
    }
    let panel = document.getElementById(PANEL_ID);
    if (panel) {
      return panel;
    }
    panel = createEl('section');
    panel.id = PANEL_ID;
    panel.dataset.sessionId = HUD_CONFIG.sessionId;
    panel.setAttribute('aria-live', 'polite');
    panel.setAttribute('role', 'status');

    const header = createEl('header', 'agentyc-hud-header');
    const title = createEl('div', 'agentyc-hud-title');
    const statusLine = createEl('div', 'agentyc-hud-status-line');
    const statusDot = createEl('div', 'agentyc-hud-status-dot');
    statusDot.dataset.kind = state.statusKind;
    statusLine.appendChild(statusDot);
    statusLine.appendChild(createEl('strong', 'agentyc-hud-name', 'AGENTYC HUD'));
    title.appendChild(statusLine);
    title.appendChild(createEl('div', 'agentyc-hud-status', state.status));
    header.appendChild(title);

    const actions = createEl('div', 'agentyc-hud-actions');
    const detailsButton = createEl('button', '', 'DETAILS');
    detailsButton.type = 'button';
    detailsButton.dataset.role = 'details-toggle';
    detailsButton.dataset.active = String(state.advanced);
    detailsButton.addEventListener('click', (event) => {
      event.stopPropagation();
      state.advanced = !state.advanced;
      state.reportOpen = false;
      saveState();
      render();
    });
    actions.appendChild(detailsButton);

    const reportWrap = createEl('div');
    reportWrap.style.position = 'relative';
    const reportButton = createEl('button', '', 'REPORT');
    reportButton.type = 'button';
    reportButton.addEventListener('click', (event) => {
      event.stopPropagation();
      state.reportOpen = !state.reportOpen;
      saveState();
      render();
    });
    const menu = createEl('div', 'agentyc-hud-menu');
    const openReport = createEl('button', '', 'Open browser report');
    openReport.type = 'button';
    openReport.addEventListener('click', (event) => {
      event.stopPropagation();
      state.reportOpen = false;
      saveState();
      render();
      openReportWindow();
    });
    menu.appendChild(openReport);
    reportWrap.appendChild(reportButton);
    reportWrap.appendChild(menu);
    actions.appendChild(reportWrap);
    header.appendChild(actions);
    panel.appendChild(header);

    const current = createEl('section', 'agentyc-hud-current');
    current.appendChild(createEl('span', 'agentyc-hud-label', 'Current step'));
    current.appendChild(createEl('div', 'agentyc-hud-current-text', state.current));
    panel.appendChild(current);

    const advanced = createEl('section', 'agentyc-hud-advanced');
    advanced.dataset.open = String(state.advanced);
    advanced.appendChild(createEl('span', 'agentyc-hud-label', 'Tool details'));
    advanced.appendChild(createEl('div', 'agentyc-hud-advanced-text'));
    panel.appendChild(advanced);

    const feed = createEl('div', 'agentyc-hud-feed');
    panel.appendChild(feed);

    const root = documentRoot();
    if (!root) {
      return null;
    }
    root.appendChild(panel);
    document.addEventListener('click', () => {
      if (!state.reportOpen) {
        return;
      }
      state.reportOpen = false;
      saveState();
      render();
    });
    render();
    return panel;
  }

  function renderRows(feed) {
    feed.replaceChildren();
    if (!state.rows.length) {
      const row = createEl('div', 'agentyc-hud-row');
      row.appendChild(createEl('div', 'agentyc-hud-row-dot'));
      row.appendChild(createEl('div', 'agentyc-hud-row-text', 'No browser activity yet.'));
      row.appendChild(createEl('div', 'agentyc-hud-row-meta', 'live'));
      feed.appendChild(row);
      return;
    }
    for (const row of state.rows) {
      const rowEl = createEl('div', 'agentyc-hud-row');
      rowEl.dataset.kind = row.kind;
      rowEl.appendChild(createEl('div', 'agentyc-hud-row-dot'));
      rowEl.appendChild(createEl('div', 'agentyc-hud-row-text', row.label));
      rowEl.appendChild(createEl('div', 'agentyc-hud-row-meta', row.durationLabel || relativeTime(row.timestamp)));
      feed.appendChild(rowEl);
    }
  }

  function render() {
    const panel = ensurePanel();
    if (!panel) {
      return;
    }
    panel.dataset.kind = state.statusKind || 'browser_event';
    panel.querySelector('.agentyc-hud-status').textContent = state.status;
    panel.querySelector('.agentyc-hud-status-dot').dataset.kind = state.statusKind || 'browser_event';
    panel.querySelector('.agentyc-hud-current-text').textContent = state.current;
    const detailsButton = panel.querySelector('[data-role="details-toggle"]');
    detailsButton.dataset.active = String(state.advanced);
    const menu = panel.querySelector('.agentyc-hud-menu');
    menu.dataset.open = String(state.reportOpen);
    const advanced = panel.querySelector('.agentyc-hud-advanced');
    advanced.dataset.open = String(state.advanced);
    const advancedLines = [
      `Tool: ${state.lastTool || 'No browser tool yet'}`,
      `Args: ${state.lastArgs || 'none'}`,
      `Session: ${state.lastSession || HUD_CONFIG.sessionId}`,
      `Last duration: ${state.lastDuration || 'n/a'}`,
    ];
    if (state.lastError) {
      advancedLines.push(`Last error: ${state.lastError}`);
    }
    panel.querySelector('.agentyc-hud-advanced-text').textContent = advancedLines.join('\n');
    renderRows(panel.querySelector('.agentyc-hud-feed'));
    saveState();
  }

  function applyEntry(entry) {
    state.current = entry.label;
    state.statusKind = entry.kind;
    state.status = statusFromKind(entry.kind);
    if (entry.tool) {
      state.lastTool = entry.tool;
      state.lastArgs = entry.argsSummary || 'none';
      state.lastSession = entry.session || HUD_CONFIG.sessionId;
      state.lastDuration = entry.durationLabel || 'n/a';
      state.lastError = entry.error || '';
    } else if (entry.error) {
      state.lastError = entry.error;
    }
    state.rows = [
      { kind: entry.kind, label: entry.label, timestamp: entry.timestamp, durationLabel: entry.durationLabel },
      ...state.rows,
    ].slice(0, MAX_ROWS);
    render();
  }

  const onHudLog = (event) => {
    const entry = normalizeEntry(event.detail || {});
    if (shouldSkipEntry(entry)) {
      return;
    }
    applyEntry(entry);
  };

  function bootHud() {
    if (window.__agentycHudBooted === HUD_CONFIG.sessionId) {
      render();
      return;
    }
    if (!ensurePanel()) {
      if (window.__agentycHudPending !== HUD_CONFIG.sessionId) {
        window.__agentycHudPending = HUD_CONFIG.sessionId;
        document.addEventListener('DOMContentLoaded', bootHud, { once: true });
      }
      return;
    }
    window.__agentycHudPending = null;
    window.addEventListener('agentyc-log', onHudLog);
    window.__agentycHudBooted = HUD_CONFIG.sessionId;
    render();
  }

  bootHud();
})();
"""

__all__ = ['DEMO_PANEL_SCRIPT']
