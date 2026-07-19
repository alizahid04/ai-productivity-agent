window.Dashboard = (() => {
  let statusChart, priorityChart;

  function chartColors() {
    const style = getComputedStyle(document.documentElement);
    return {
      text: style.getPropertyValue('--text-dim').trim(),
      grid: style.getPropertyValue('--border').trim(),
    };
  }

  async function refresh() {
    try {
      const [summary, tasksRes, logsRes] = await Promise.all([
        App.api('/api/tasks/analytics/summary'),
        App.api('/api/tasks'),
        App.api('/api/logs?limit=8'),
      ]);
      renderStats(summary, tasksRes.tasks);
      renderCharts(summary);
      renderOverdue(tasksRes.tasks);
      renderRecentRuns(logsRes.logs);
      App.renderPipeline(
        document.getElementById('dashPipelineTrail'),
        document.getElementById('dashPipelineLabels'),
        logsRes.logs[0] ? (logsRes.logs[0].errors ? 'error' : 'completed') : null
      );
    } catch (e) {
      App.toast(e.message, 'error');
    }
  }

  function renderStats(summary, tasks) {
    document.getElementById('statTotal').textContent = summary.total;
    document.getElementById('statCompleted').textContent = summary.by_status.completed || 0;
    document.getElementById('statRate').textContent = `${summary.completion_rate}%`;
    const overdue = tasks.filter(
      (t) => t.due_date && new Date(t.due_date) < new Date() && !['completed', 'cancelled'].includes(t.status)
    );
    document.getElementById('statOverdue').textContent = overdue.length;
  }

  function renderCharts(summary) {
    const colors = chartColors();
    const statusCtx = document.getElementById('statusChart');
    const priorityCtx = document.getElementById('priorityChart');

    const statusLabels = Object.keys(summary.by_status);
    const statusData = Object.values(summary.by_status);
    const statusColorMap = {
      pending: '#a3aac2',
      in_progress: '#22d3ee',
      completed: '#34d399',
      cancelled: '#f43f5e',
    };

    if (statusChart) statusChart.destroy();
    statusChart = new Chart(statusCtx, {
      type: 'doughnut',
      data: {
        labels: statusLabels,
        datasets: [{ data: statusData, backgroundColor: statusLabels.map((l) => statusColorMap[l] || '#7c5cfc'), borderWidth: 0 }],
      },
      options: {
        plugins: { legend: { position: 'bottom', labels: { color: colors.text, boxWidth: 10, font: { size: 11 } } } },
        cutout: '65%',
      },
    });

    const priorityLabels = Object.keys(summary.by_priority);
    const priorityData = Object.values(summary.by_priority);
    const priorityColorMap = { low: '#6b7290', medium: '#f5a524', high: '#fb923c', urgent: '#f43f5e' };

    if (priorityChart) priorityChart.destroy();
    priorityChart = new Chart(priorityCtx, {
      type: 'bar',
      data: {
        labels: priorityLabels,
        datasets: [{ data: priorityData, backgroundColor: priorityLabels.map((l) => priorityColorMap[l] || '#7c5cfc'), borderRadius: 6 }],
      },
      options: {
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: colors.text }, grid: { display: false } },
          y: { ticks: { color: colors.text, precision: 0 }, grid: { color: colors.grid } },
        },
      },
    });
  }

  function renderOverdue(tasks) {
    const overdue = tasks.filter(
      (t) => t.due_date && new Date(t.due_date) < new Date() && !['completed', 'cancelled'].includes(t.status)
    );
    const el = document.getElementById('overdueList');
    if (!overdue.length) {
      el.innerHTML = `<div style="color:var(--text-faint);">Nothing overdue. 🎉</div>`;
      return;
    }
    el.innerHTML = overdue
      .slice(0, 6)
      .map(
        (t) => `<div style="display:flex;justify-content:space-between;">
        <span>${App.escapeHtml(t.title)}</span>
        <span class="badge badge-priority-${t.priority}">${t.priority}</span>
      </div>`
      )
      .join('');
  }

  function renderRecentRuns(logs) {
    const el = document.getElementById('recentRunsList');
    if (!logs.length) {
      el.innerHTML = `<div style="color:var(--text-faint);">No agent runs yet.</div>`;
      return;
    }
    el.innerHTML = logs
      .map(
        (l) => `<div style="display:flex;justify-content:space-between;gap:10px;">
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:70%;">${App.escapeHtml(l.request)}</span>
        <span style="color:var(--text-faint);font-family:var(--font-mono);font-size:11px;">${App.timeAgo(l.started_at)}</span>
      </div>`
      )
      .join('');
  }

  return { refresh };
})();
