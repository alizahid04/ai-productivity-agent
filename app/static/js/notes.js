window.Notes = (() => {
  async function refresh(query) {
    try {
      const q = query ? `?q=${encodeURIComponent(query)}` : '';
      const res = await App.api(`/api/notes${q}`);
      render(res.notes);
    } catch (e) {
      App.toast(e.message, 'error');
    }
  }

  function render(notes) {
    const grid = document.getElementById('noteGrid');
    if (!notes.length) {
      grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;">
        <i class="fa-regular fa-note-sticky"></i>
        No notes yet. Save one here, or ask the agent to save one for you.
      </div>`;
      return;
    }
    grid.innerHTML = notes
      .map(
        (n) => `
      <div class="glass note-card">
        <div class="cat">${App.escapeHtml(n.category)}</div>
        <h4>${App.escapeHtml(n.title)}</h4>
        <p>${App.escapeHtml(n.content)}</p>
        <div class="task-actions" style="margin-top:8px;">
          <button class="icon-btn" data-delete="${n.id}" title="Delete"><i class="fa-solid fa-trash"></i></button>
        </div>
      </div>`
      )
      .join('');

    grid.querySelectorAll('[data-delete]').forEach((btn) =>
      btn.addEventListener('click', async () => {
        try {
          await App.api(`/api/notes/${btn.dataset.delete}`, { method: 'DELETE' });
          App.toast('Note deleted.');
          refresh();
        } catch (e) {
          App.toast(e.message, 'error');
        }
      })
    );
  }

  function openCreateModal() {
    App.openModal(`
      <h3><i class="fa-solid fa-plus"></i> New note</h3>
      <div class="form-row"><label>Title</label><input type="text" id="n_title"></div>
      <div class="form-row"><label>Category</label><input type="text" id="n_category" value="general"></div>
      <div class="form-row"><label>Content</label><textarea id="n_content" rows="5"></textarea></div>
      <div class="modal-actions">
        <button class="btn btn-ghost" id="cancelNote">Cancel</button>
        <button class="btn btn-primary" id="confirmNote">Save note</button>
      </div>`);
    document.getElementById('cancelNote').addEventListener('click', App.closeModal);
    document.getElementById('confirmNote').addEventListener('click', async () => {
      const title = document.getElementById('n_title').value.trim();
      const content = document.getElementById('n_content').value.trim();
      if (!title || !content) { App.toast('Title and content are required.', 'error'); return; }
      try {
        await App.api('/api/notes', {
          method: 'POST',
          body: JSON.stringify({ title, content, category: document.getElementById('n_category').value || 'general' }),
        });
        App.toast('Note saved.');
        App.closeModal();
        refresh();
      } catch (e) {
        App.toast(e.message, 'error');
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('newNoteBtn').addEventListener('click', openCreateModal);
    let debounce;
    document.getElementById('noteSearch').addEventListener('input', (e) => {
      clearTimeout(debounce);
      debounce = setTimeout(() => refresh(e.target.value), 250);
    });
  });

  return { refresh };
})();
