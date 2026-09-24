// Shared "Historique" side panel, wired up by each tool's own script
// (gantt.js / whiteboard.js / kanban.js) via window.initHistoryPanel.
(function () {
  function fmtDate(iso) {
    const d = new Date(iso);
    return d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  function initHistoryPanel({ buttonId, apiUrl, onRestore }) {
    const btn = document.getElementById(buttonId);
    if (!btn) return;

    const panel = document.createElement('div');
    panel.className = 'history-panel';
    panel.style.display = 'none';

    const header = document.createElement('div');
    header.className = 'history-panel-header';
    const title = document.createElement('h3');
    title.textContent = 'Historique';
    const refreshBtn = document.createElement('button');
    refreshBtn.type = 'button';
    refreshBtn.className = 'icon-btn';
    refreshBtn.title = 'Rafraîchir';
    refreshBtn.textContent = '⟳';
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'icon-btn';
    closeBtn.title = 'Fermer';
    closeBtn.textContent = '✕';
    header.appendChild(title);
    header.appendChild(refreshBtn);
    header.appendChild(closeBtn);

    const body = document.createElement('div');
    body.className = 'history-panel-body';

    panel.appendChild(header);
    panel.appendChild(body);
    document.body.appendChild(panel);

    function renderEmpty(text) {
      body.textContent = '';
      const p = document.createElement('p');
      p.className = 'history-empty';
      p.textContent = text;
      body.appendChild(p);
    }

    async function load() {
      renderEmpty('Chargement...');
      try {
        const res = await fetch(apiUrl);
        if (!res.ok) throw new Error('request failed');
        const data = await res.json();
        const entries = data.entries || [];
        if (entries.length === 0) {
          renderEmpty('Aucune activité pour le moment.');
          return;
        }
        const ul = document.createElement('ul');
        ul.className = 'history-list';
        entries.forEach((entry) => {
          const li = document.createElement('li');
          li.className = 'history-entry';

          const meta = document.createElement('div');
          meta.className = 'history-entry-meta';
          const actor = document.createElement('span');
          actor.className = 'history-entry-actor';
          actor.textContent = entry.actor_label;
          const time = document.createElement('span');
          time.className = 'history-entry-time';
          time.textContent = fmtDate(entry.created_at);
          meta.appendChild(actor);
          meta.appendChild(time);

          const summary = document.createElement('div');
          summary.className = 'history-entry-summary';
          summary.textContent = entry.summary;

          li.appendChild(meta);
          li.appendChild(summary);

          if (entry.can_restore && onRestore) {
            const restoreBtn = document.createElement('button');
            restoreBtn.type = 'button';
            restoreBtn.className = 'history-restore-btn';
            restoreBtn.textContent = '↺ Restaurer';
            restoreBtn.addEventListener('click', () => {
              restoreBtn.disabled = true;
              restoreBtn.textContent = 'Restauré';
              onRestore(entry);
            });
            li.appendChild(restoreBtn);
          }

          ul.appendChild(li);
        });
        body.textContent = '';
        body.appendChild(ul);
      } catch (err) {
        renderEmpty("Impossible de charger l'historique.");
      }
    }

    btn.addEventListener('click', () => {
      const isHidden = panel.style.display === 'none';
      panel.style.display = isHidden ? 'flex' : 'none';
      if (isHidden) load();
    });
    closeBtn.addEventListener('click', () => {
      panel.style.display = 'none';
    });
    refreshBtn.addEventListener('click', load);
  }

  window.initHistoryPanel = initHistoryPanel;
})();
