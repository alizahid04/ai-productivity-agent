(function () {
  const root = document.documentElement;
  const toggle = document.getElementById('themeToggle');
  const stored = window.__theme || 'dark';
  root.setAttribute('data-theme', stored);
  updateToggleLabel(stored);

  toggle.addEventListener('click', () => {
    const current = root.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    window.__theme = next;
    updateToggleLabel(next);
  });

  function updateToggleLabel(mode) {
    const icon = toggle.querySelector('i');
    const label = toggle.querySelector('span');
    if (mode === 'dark') {
      icon.className = 'fa-solid fa-moon';
      label.textContent = 'Dark';
    } else {
      icon.className = 'fa-solid fa-sun';
      label.textContent = 'Light';
    }
  }
})();
