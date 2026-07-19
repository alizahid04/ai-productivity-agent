/* =========================================================================
   Chat: drives POST /api/chat and /api/chat/approval, renders the pipeline
   trail + agent pulse, message bubbles, tool chips, and the approval card.
   ========================================================================= */

(() => {
  let sessionId = crypto.randomUUID();
  const messagesEl = document.getElementById('chatMessages');
  const inputEl = document.getElementById('chatInput');
  const sendBtn = document.getElementById('sendBtn');
  const toolLogEl = document.getElementById('chatToolLog');
  const approvalWrap = document.getElementById('approvalPanelWrap');
  const approvalHost = document.getElementById('approvalCardHost');

  function addMessage(role, text, toolCalls) {
    const wrap = document.createElement('div');
    wrap.className = `msg ${role}`;
    const avatar = role === 'user' ? 'You' : role === 'system' ? '!' : 'AI';
    let toolsHtml = '';
    if (toolCalls && toolCalls.length) {
      toolsHtml = toolCalls
        .map((tc) => `<div class="tool-chip"><i class="fa-solid fa-wrench"></i> ${App.escapeHtml(tc.tool)}</div>`)
        .join('');
    }
    wrap.innerHTML = `
      <div class="msg-avatar">${avatar}</div>
      <div>
        <div class="msg-bubble">${App.escapeHtml(text)}</div>
        ${toolsHtml}
      </div>`;
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function addSkeleton() {
    const wrap = document.createElement('div');
    wrap.className = 'msg assistant';
    wrap.id = 'skeletonMsg';
    wrap.innerHTML = `
      <div class="msg-avatar">AI</div>
      <div class="msg-bubble" style="display:flex;flex-direction:column;gap:6px;width:180px;">
        <div class="skeleton-line" style="width:90%"></div>
        <div class="skeleton-line" style="width:70%"></div>
      </div>`;
    messagesEl.appendChild(wrap);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
  function removeSkeleton() {
    const el = document.getElementById('skeletonMsg');
    if (el) el.remove();
  }

  function updateStatus(status) {
    App.setPulse(document.getElementById('chatPulse'), document.getElementById('chatStatusLabel'), status);
    App.setGlobalStatus(status);
    App.renderPipeline(
      document.getElementById('chatPipelineTrail'),
      document.getElementById('chatPipelineLabels'),
      status
    );
  }

  function renderToolLog(toolCalls) {
    if (!toolCalls || !toolCalls.length) return;
    toolCalls.forEach((tc) => {
      const line = document.createElement('div');
      const ok = tc.result && tc.result.success;
      line.innerHTML = `<i class="fa-solid ${ok ? 'fa-check' : 'fa-xmark'}" style="color:${ok ? 'var(--status-completed)' : 'var(--status-error)'}"></i> ${App.escapeHtml(tc.tool)} · ${tc.duration_seconds}s`;
      toolLogEl.prepend(line);
    });
  }

  // The agent's own chat reply is the model paraphrasing what happened —
  // when a tool actually failed, show the real error text too, so a vague
  // apology from the model never hides the underlying cause.
  function surfaceToolFailures(toolCalls) {
    if (!toolCalls || !toolCalls.length) return;
    toolCalls
      .filter((tc) => tc.result && tc.result.success === false)
      .forEach((tc) => {
        addMessage('system', `"${tc.tool}" failed: ${tc.result.error || 'unknown error'}`);
      });
  }

  function renderApproval(pending, runId) {
    if (!pending) {
      approvalWrap.style.display = 'none';
      approvalHost.innerHTML = '';
      return;
    }
    approvalWrap.style.display = 'block';
    const actions = pending.actions || [];
    const cardsHtml = actions
      .map(
        (a) => `
      <div class="approval-card" style="margin-bottom:8px;">
        <div class="approval-title"><i class="fa-solid fa-triangle-exclamation"></i> ${App.escapeHtml(a.tool_name)}</div>
        <div>${App.escapeHtml(a.expected_effect)}</div>
        <div class="approval-args">${App.escapeHtml(JSON.stringify(a.arguments, null, 2))}</div>
      </div>`
      )
      .join('');
    approvalHost.innerHTML = `
      ${actions.length > 1 ? `<div style="font-size:12px;color:var(--text-dim);margin-bottom:8px;">${actions.length} actions pending — approving applies to all of them.</div>` : ''}
      ${cardsHtml}
      <div class="approval-actions">
        <button class="btn btn-primary btn-sm" data-decision="approve"><i class="fa-solid fa-check"></i> Approve ${actions.length > 1 ? `all ${actions.length}` : ''}</button>
        <button class="btn btn-danger btn-sm" data-decision="reject"><i class="fa-solid fa-xmark"></i> Reject ${actions.length > 1 ? 'all' : ''}</button>
      </div>`;
    approvalHost.querySelectorAll('[data-decision]').forEach((btn) => {
      btn.addEventListener('click', () => resolveApproval(runId, pending.approval_id, btn.dataset.decision));
    });
  }

  async function resolveApproval(runId, approvalId, decision) {
    updateStatus('executing_tool');
    try {
      const res = await App.api('/api/chat/approval', {
        method: 'POST',
        body: JSON.stringify({ session_id: sessionId, run_id: runId, approval_id: approvalId, decision }),
      });
      renderApproval(res.pending_approval, res.run_id);
      renderToolLog(res.tool_calls);
      surfaceToolFailures(res.tool_calls);
      if (res.message) addMessage('assistant', res.message, res.tool_calls);
      updateStatus(res.status);
      App.toast(decision === 'approve' ? 'Action approved and executed.' : 'Action rejected.', decision === 'approve' ? 'success' : 'error');
    } catch (e) {
      App.toast(e.message, 'error');
      updateStatus('error');
    }
  }

  async function sendMessage(text) {
    if (!text.trim()) return;
    addMessage('user', text);
    inputEl.value = '';
    sendBtn.disabled = true;
    addSkeleton();
    updateStatus('thinking');

    try {
      const res = await App.api('/api/chat', {
        method: 'POST',
        body: JSON.stringify({ message: text, session_id: sessionId }),
      });
      removeSkeleton();
      updateStatus(res.status);
      renderToolLog(res.tool_calls);
      renderApproval(res.pending_approval, res.run_id);
      surfaceToolFailures(res.tool_calls);
      if (res.status === 'error') {
        addMessage('system', res.message || 'Something went wrong.');
      } else if (res.message) {
        addMessage('assistant', res.message, res.tool_calls);
      } else if (res.status === 'waiting_for_approval') {
        const actions = (res.pending_approval && res.pending_approval.actions) || [];
        const names = actions.map((a) => a.tool_name).join(', ');
        addMessage(
          'assistant',
          actions.length > 1
            ? `I need your approval before I run ${actions.length} actions (${names}). See the panel on the right.`
            : `I need your approval before I run "${names}". See the panel on the right.`
        );
      }
      if (window.Tasks) window.Tasks.refresh();
      if (window.Notes) window.Notes.refresh();
      if (window.Dashboard) window.Dashboard.refresh();
    } catch (e) {
      removeSkeleton();
      addMessage('system', e.message);
      updateStatus('error');
      App.toast(e.message, 'error');
    } finally {
      sendBtn.disabled = false;
    }
  }

  sendBtn.addEventListener('click', () => sendMessage(inputEl.value));
  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(inputEl.value);
    }
  });
  inputEl.addEventListener('input', () => {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px';
  });

  document.querySelectorAll('[data-prompt]').forEach((btn) => {
    btn.addEventListener('click', () => {
      App.showView('chat');
      sendMessage(btn.dataset.prompt);
    });
  });

  document.getElementById('newSessionBtn').addEventListener('click', () => {
    sessionId = crypto.randomUUID();
    messagesEl.innerHTML = '';
    toolLogEl.innerHTML = '';
    renderApproval(null);
    updateStatus(null);
    App.toast('Started a new session.', 'success');
  });

  updateStatus(null);
  addMessage('assistant', "Hi! I'm your productivity agent. Ask me to create tasks, save notes, extract meeting actions, plan your day, or draft a follow-up email.");
})();
