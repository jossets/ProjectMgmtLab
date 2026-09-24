(function () {
  function currentPageLabel() {
    const path = location.pathname;
    if (/^\/sessions\/[A-Z0-9]{7}\/cours\/?$/.test(path)) return 'Cours';
    if (/^\/sessions\/[A-Z0-9]{7}\/qcm\//.test(path)) return 'Résultats QCM';
    if (/^\/gantt\//.test(path)) return 'Gantt';
    if (/^\/whiteboard\//.test(path)) return 'Tableau blanc';
    if (/^\/kanban\//.test(path)) return 'Kanban';
    if (/^\/sessions\/[A-Z0-9]{7}\/?$/.test(path)) return 'Session';
    if (path === '/sessions') return 'Liste des sessions';
    if (path === '/accounts') return 'Comptes';
    if (path === '/profile') return 'Profil';
    if (path === '/join' || path.startsWith('/join/')) return "Rejoindre une session";
    if (path === '/login') return 'Connexion';
    if (path === '/') return 'Accueil';
    return 'Autre page';
  }

  // ---- heartbeat: every logged-in visitor reports where they are ----
  // (harmless no-op server-side for anonymous visitors on open-link tools)
  function sendPing() {
    fetch('/api/presence/ping', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page: currentPageLabel(), visible: document.visibilityState === 'visible' }),
      keepalive: true,
    }).catch(() => {});
  }

  sendPing();
  document.addEventListener('visibilitychange', sendPing);
  setInterval(sendPing, 15000);
  window.addEventListener('pagehide', () => {
    if (navigator.sendBeacon) navigator.sendBeacon('/api/presence/gone');
  });

  // ---- admin panel: who's currently on the site, and where ----
  const toggleBtn = document.getElementById('presence-toggle-btn');
  if (!toggleBtn) return;

  const panel = document.createElement('div');
  panel.className = 'presence-panel';
  panel.style.display = 'none';
  document.body.appendChild(panel);

  let pollHandle = null;

  function renderPanel(students) {
    panel.textContent = '';
    const header = document.createElement('div');
    header.className = 'presence-panel-header';
    const title = document.createElement('h3');
    title.textContent = 'Élèves';
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'icon-btn';
    closeBtn.textContent = '✕';
    closeBtn.addEventListener('click', hidePanel);
    header.appendChild(title);
    header.appendChild(closeBtn);
    panel.appendChild(header);

    if (students.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'presence-empty';
      empty.textContent = 'Aucun élève.';
      panel.appendChild(empty);
      return;
    }

    const list = document.createElement('ul');
    list.className = 'presence-list';
    students.forEach((s) => {
      const li = document.createElement('li');
      li.className = 'presence-item ' + (s.online && s.visible ? 'presence-active' : s.online ? 'presence-idle' : 'presence-offline');

      const dot = document.createElement('span');
      dot.className = 'presence-dot';
      const name = document.createElement('span');
      name.className = 'presence-name';
      name.textContent = s.username;
      const pageEl = document.createElement('span');
      pageEl.className = 'presence-page';
      if (!s.online) pageEl.textContent = 'Hors ligne';
      else if (!s.visible) pageEl.textContent = `${s.page} (arrière-plan)`;
      else pageEl.textContent = s.page;

      li.appendChild(dot);
      li.appendChild(name);
      li.appendChild(pageEl);
      list.appendChild(li);
    });
    panel.appendChild(list);
  }

  async function refreshPanel() {
    try {
      const res = await fetch('/api/presence/students');
      if (!res.ok) return;
      const data = await res.json();
      renderPanel(data.students || []);
    } catch (err) {
      // transient failure — next poll retries
    }
  }

  function showPanel() {
    panel.style.display = 'block';
    refreshPanel();
    pollHandle = setInterval(refreshPanel, 5000);
  }

  function hidePanel() {
    panel.style.display = 'none';
    clearInterval(pollHandle);
    pollHandle = null;
  }

  toggleBtn.addEventListener('click', () => {
    if (panel.style.display === 'none') showPanel();
    else hidePanel();
  });
})();
