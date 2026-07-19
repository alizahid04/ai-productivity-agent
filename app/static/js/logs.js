window.Logs = (() => {
  async function refresh() {
    try {
      const res = await App.api('/api/logs?limit=100');
      render(res.logs);
    } catch (e) {
      App.toast(e.message, 'error');
    }
  }

  function render(logs) {
    const body = document.getElementById('logsTableBody');
    if (!logs.length) {
      body.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--text-faint);padding:30px;">No runs yet — try the Chat page.</td></tr>`;
      return;
    }
    body.innerHTML = logs
      .map((l) => {
        const tools = (l.tools_used || []).join(', ') || '—';
        return `
        <tr>
          <td class="log-id">${l.run_id.slice(0, 8)}</td>
          <td style="max-width:260px;">${App.escapeHtml((l.request || '').slice(0, 90))}</td>
          <td style="font-family:var(--font-mono);font-size:11.5px;">${App.escapeHtml(tools)}</td>
          <td><span class="log-status-badge status-${l.approval_status === 'pending' ? 'waiting_for_approval' : l.errors ? 'error' : 'completed'}">${l.errors ? 'error' : l.approval_status}</span></td>
          <td>${l.duration != null ? l.duration.toFixed(2) + 's' : '—'}</td>
          <td>${App.timeAgo(l.started_at)}</td>
        </tr>`;
      })
      .join('');
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('refreshLogsBtn').addEventListener('click', refresh);
  });

  return { refresh };
})();
