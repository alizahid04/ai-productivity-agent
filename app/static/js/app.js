/* =========================================================================
   Shared app shell: navigation, API helper, toasts, modals, agent pulse.
   ========================================================================= */

const App = (() => {
  const PIPELINE_STAGES = [
    { key: 'thinking', label: 'Intent' },
    { key: 'selecting_tool', label: 'Tool select' },
    { key: 'waiting_for_approval', label: 'Approval' },
    { key: 'executing_tool', label: 'Execute' },
    { key: 'validating_result', label: 'Validate' },
    { key: 'completed', label: 'Response' },
  ];

  function api(path, opts = {}) {
    return fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...opts,
    }).then(async (res) => {
      let body = null;
      try { body = await res.json(); } catch (_) { /* no body */ }
      if (!res.ok) {
        const detail = (body && body.detail) || `Request failed (${res.status})`;
        throw new Error(detail);
      }
      return body;
    });
  }

  function toast(message, type = 'success') {
    const stack = document.getElementById('toastStack');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    const icon = type === 'success' ? 'fa-circle-check' : 'fa-circle-exclamation';
    el.innerHTML = `<i class="fa-solid ${icon}"></i><span>${escapeHtml(message)}</span>`;
    stack.appendChild(el);
    setTimeout(() => {
      el.style.transition = 'opacity .25s ease';
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 250);
    }, 3200);
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
  }

  function timeAgo(iso) {
    if (!iso) return '';
    const diff = (Date.now() - new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).getTime()) / 1000;
    if (diff < 60) return `${Math.max(1, Math.round(diff))}s ago`;
    if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
    return `${Math.round(diff / 86400)}d ago`;
  }

  function openModal(html) {
    const host = document.getElementById('modalHost');
    host.innerHTML = `<div class="modal-overlay" id="__overlay"><div class="glass modal-card">${html}</div></div>`;
    document.getElementById('__overlay').addEventListener('click', (e) => {
      if (e.target.id === '__overlay') closeModal();
    });
  }
  function closeModal() {
    document.getElementById('modalHost').innerHTML = '';
  }

  function renderPipeline(trailEl, labelsEl, status) {
    trailEl.innerHTML = '';
    labelsEl.innerHTML = '';
    const activeIdx = PIPELINE_STAGES.findIndex((s) => s.key === status);
    const isError = status === 'error';

    PIPELINE_STAGES.forEach((stage, idx) => {
      const node = document.createElement('div');
      node.className = 'pipeline-node';
      if (isError && idx <= Math.max(activeIdx, 0)) {
        node.classList.add('error');
      } else if (activeIdx === -1) {
        // idle
      } else if (idx < activeIdx || (idx === activeIdx && status === 'completed')) {
        node.classList.add('done');
      } else if (idx === activeIdx) {
        node.classList.add('active');
      }
      trailEl.appendChild(node);

      const label = document.createElement('span');
      label.textContent = stage.label;
      labelsEl.appendChild(label);
    });
  }

  function setPulse(pulseEl, labelEl, status) {
    const clean = (status || 'idle').replace(/_/g, ' ');
    pulseEl.className = 'agent-pulse';
    if (status) {
      pulseEl.classList.add(status);
      if (!['completed', 'error'].includes(status)) pulseEl.classList.add('live');
    }
    if (labelEl) labelEl.textContent = clean;
  }

  function setGlobalStatus(status) {
    setPulse(document.getElementById('globalPulse'), document.getElementById('globalStatusLabel'), status);
  }

  // ---- Navigation ----
  function initNav() {
    document.querySelectorAll('.nav-item[data-view]').forEach((btn) => {
      btn.addEventListener('click', () => showView(btn.dataset.view));
    });
    document.querySelectorAll('[data-view-link]').forEach((btn) => {
      btn.addEventListener('click', () => showView(btn.dataset.viewLink));
    });
  }

  function showView(name) {
    document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
    document.querySelectorAll('.nav-item[data-view]').forEach((b) => b.classList.remove('active'));
    document.getElementById(`view-${name}`).classList.add('active');
    const navBtn = document.querySelector(`.nav-item[data-view="${name}"]`);
    if (navBtn) navBtn.classList.add('active');

    if (name === 'dashboard' && window.Dashboard) window.Dashboard.refresh();
    if (name === 'tasks' && window.Tasks) window.Tasks.refresh();
    if (name === 'notes' && window.Notes) window.Notes.refresh();
    if (name === 'logs' && window.Logs) window.Logs.refresh();
    if (name === 'settings') {
      refreshSettings();
      if (window.SettingsPanel) window.SettingsPanel.refresh();
    }
  }

  async function refreshSettings() {
    const el = document.getElementById('settingsHealth');
    try {
      const health = await api('/api/health');
      el.innerHTML = Object.entries(health)
        .map(([k, v]) => `<div><strong style="color:var(--text)">${escapeHtml(k)}</strong>: ${escapeHtml(v)}</div>`)
        .join('');
    } catch (e) {
      el.textContent = 'Could not load health info.';
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    initNav();
    refreshSettings();
    if (window.Dashboard) window.Dashboard.refresh();
  });

  return { api, toast, escapeHtml, timeAgo, openModal, closeModal, renderPipeline, setPulse, setGlobalStatus, showView, PIPELINE_STAGES };
})();
