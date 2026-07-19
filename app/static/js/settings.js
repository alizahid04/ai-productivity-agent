window.SettingsPanel = (() => {
  let current = null;

  async function refresh() {
    try {
      current = await App.api('/api/settings/llm');
      render();
    } catch (e) {
      App.toast(e.message, 'error');
    }
  }

  function render() {
    if (!current) return;

    document.querySelectorAll('#providerToggle [data-provider]').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.provider === current.provider);
    });

    const hfRow = document.getElementById('hfModelRow');
    const select = document.getElementById('hfModelSelect');
    if (current.provider === 'huggingface') {
      hfRow.style.display = 'flex';
      select.innerHTML = current.hf_models
        .map((m) => `<option value="${App.escapeHtml(m)}" ${m === current.active_hf_model ? 'selected' : ''}>${App.escapeHtml(m)}</option>`)
        .join('');
    } else {
      hfRow.style.display = 'none';
    }

    const note = document.getElementById('providerStatusNote');
    const configured = current.provider === 'huggingface' ? current.huggingface_configured : current.gemini_configured;
    if (!configured) {
      note.innerHTML = `<span style="color:var(--status-error)"><i class="fa-solid fa-triangle-exclamation"></i> ${current.provider === 'huggingface' ? 'HF_API_KEY' : 'GEMINI_API_KEY'} is not set in .env — chat will show an error until it's added and the app is restarted.</span>`;
    } else if (current.provider === 'gemini') {
      note.textContent = `Using Gemini model: ${current.gemini_model}`;
    } else {
      note.textContent = `${current.hf_models.length} Hugging Face model(s) configured.`;
    }
  }

  async function setProvider(provider) {
    try {
      current = await App.api('/api/settings/llm', {
        method: 'POST',
        body: JSON.stringify({ provider }),
      });
      render();
      App.toast(`Switched to ${provider === 'huggingface' ? 'Hugging Face' : 'Gemini'}.`);
    } catch (e) {
      App.toast(e.message, 'error');
    }
  }

  async function setHfModel(model) {
    try {
      current = await App.api('/api/settings/llm', {
        method: 'POST',
        body: JSON.stringify({ hf_model: model }),
      });
      render();
      App.toast(`Active model: ${model}`);
    } catch (e) {
      App.toast(e.message, 'error');
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('#providerToggle [data-provider]').forEach((btn) => {
      btn.addEventListener('click', () => setProvider(btn.dataset.provider));
    });
    document.getElementById('hfModelSelect').addEventListener('change', (e) => setHfModel(e.target.value));
  });

  return { refresh };
})();
