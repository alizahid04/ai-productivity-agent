window.Tasks = (() => {
  let currentFilter = '';
  let cache = [];

  async function refresh() {
    try {
      const q = currentFilter ? `?status=${currentFilter}` : '';
      const res = await App.api(`/api/tasks${q}`);
      cache = res.tasks;
      render(cache);
    } catch (e) {
      App.toast(e.message, 'error');
    }
  }

  function render(tasks) {
    const grid = document.getElementById('taskGrid');
    if (!tasks.length) {
      grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
        <i class="fa-regular fa-clipboard"></i>
        No tasks yet. Create one, or ask the agent in Chat.
      </div>`;
      return;
    }
    grid.innerHTML = tasks
      .map(
        (t) => `
      <div class="glass task-card">
        <div class="task-card-top">
          <div class="task-title">${App.escapeHtml(t.title)}</div>
          <div class="task-actions">
            ${t.status !== 'completed' ? `<button class="icon-btn" data-complete="${t.id}" title="Complete"><i class="fa-solid fa-check"></i></button>` : ''}
            <button class="icon-btn" data-delete="${t.id}" title="Delete"><i class="fa-solid fa-trash"></i></button>
          </div>
        </div>
        ${t.description ? `<div class="task-desc">${App.escapeHtml(t.description)}</div>` : ''}
        <div class="task-meta">
          <span class="badge badge-priority-${t.priority}">${t.priority}</span>
          <span class="badge badge-status-${t.status}">${t.status.replace('_', ' ')}</span>
          ${t.assignee ? `<span class="badge" style="background:var(--surface-strong);color:var(--text-dim)"><i class="fa-regular fa-user"></i> ${App.escapeHtml(t.assignee)}</span>` : ''}
          ${(t.tags || []).includes('reminder') ? `<span class="badge" style="background:rgba(124,92,252,0.14);color:var(--accent-1)"><i class="fa-regular fa-bell"></i> reminder</span>` : ''}
          ${t.due_date ? `<span class="badge" style="background:var(--surface-strong);color:var(--text-dim)"><i class="fa-regular fa-clock"></i> ${new Date(t.due_date).toLocaleDateString()}</span>` : ''}
        </div>
      </div>`
      )
      .join('');

    grid.querySelectorAll('[data-complete]').forEach((btn) =>
      btn.addEventListener('click', () => completeTask(btn.dataset.complete))
    );
    grid.querySelectorAll('[data-delete]').forEach((btn) =>
      btn.addEventListener('click', () => confirmDelete(btn.dataset.delete))
    );
  }

  async function completeTask(id) {
    try {
      await App.api(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify({ status: 'completed' }) });
      App.toast('Task completed.');
      refresh();
      if (window.Dashboard) window.Dashboard.refresh();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  }

  function confirmDelete(id) {
    App.openModal(`
      <h3><i class="fa-solid fa-triangle-exclamation" style="color:var(--status-error)"></i> Delete task?</h3>
      <p style="font-size:13px;color:var(--text-dim);">This can't be undone.</p>
      <div class="modal-actions">
        <button class="btn btn-ghost" id="cancelDelete">Cancel</button>
        <button class="btn btn-danger" id="confirmDelete">Delete</button>
      </div>`);
    document.getElementById('cancelDelete').addEventListener('click', App.closeModal);
    document.getElementById('confirmDelete').addEventListener('click', async () => {
      try {
        await App.api(`/api/tasks/${id}`, { method: 'DELETE' });
        App.toast('Task deleted.');
        App.closeModal();
        refresh();
        if (window.Dashboard) window.Dashboard.refresh();
      } catch (e) {
        App.toast(e.message, 'error');
      }
    });
  }

  function openCreateModal() {
    App.openModal(`
      <h3><i class="fa-solid fa-plus"></i> New task</h3>
      <div class="form-row"><label>Title</label><input type="text" id="f_title"></div>
      <div class="form-row"><label>Description</label><textarea id="f_desc"></textarea></div>
      <div class="form-row"><label>Priority</label>
        <select id="f_priority">
          <option value="low">Low</option>
          <option value="medium" selected>Medium</option>
          <option value="high">High</option>
          <option value="urgent">Urgent</option>
        </select>
      </div>
      <div class="form-row"><label>Due date</label><input type="date" id="f_due"></div>
      <div class="modal-actions">
        <button class="btn btn-ghost" id="cancelCreate">Cancel</button>
        <button class="btn btn-primary" id="confirmCreate">Create task</button>
      </div>`);
    document.getElementById('cancelCreate').addEventListener('click', App.closeModal);
    document.getElementById('confirmCreate').addEventListener('click', async () => {
      const title = document.getElementById('f_title').value.trim();
      if (!title) { App.toast('Title is required.', 'error'); return; }
      const payload = {
        title,
        description: document.getElementById('f_desc').value || null,
        priority: document.getElementById('f_priority').value,
        due_date: document.getElementById('f_due').value ? new Date(document.getElementById('f_due').value).toISOString() : null,
      };
      try {
        await App.api('/api/tasks', { method: 'POST', body: JSON.stringify(payload) });
        App.toast('Task created.');
        App.closeModal();
        refresh();
        if (window.Dashboard) window.Dashboard.refresh();
      } catch (e) {
        App.toast(e.message, 'error');
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('newTaskBtn').addEventListener('click', openCreateModal);
    document.querySelectorAll('#taskFilters .filter-pill').forEach((pill) => {
      pill.addEventListener('click', () => {
        document.querySelectorAll('#taskFilters .filter-pill').forEach((p) => p.classList.remove('active'));
        pill.classList.add('active');
        currentFilter = pill.dataset.status;
        refresh();
      });
    });
  });

  return { refresh, getCache: () => cache };
})();
