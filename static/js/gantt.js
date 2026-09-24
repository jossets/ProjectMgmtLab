(function () {
  const root = document.querySelector('.gantt-page');
  const ganttId = root.dataset.ganttId;
  const table = document.getElementById('gantt-table');
  const scrollEl = document.getElementById('gantt-scroll');
  const nameInput = document.getElementById('gantt-name');
  const addBtn = document.getElementById('add-task-btn');
  const saveStatus = document.getElementById('save-status');
  const zoomOutBtn = document.getElementById('zoom-out-btn');
  const zoomInBtn = document.getElementById('zoom-in-btn');
  const zoomFitBtn = document.getElementById('zoom-fit-btn');
  const zoomResetBtn = document.getElementById('zoom-reset-btn');
  const zoomLevelEl = document.getElementById('zoom-level');
  const exportPngBtn = document.getElementById('export-png-btn');

  const DAY_PX_BASE = 28;
  const MIN_DAYS = 14;
  const ZOOM_MIN = 0.15;
  const ZOOM_MAX = 6;
  const EXPORT_WIDTH = 1920;
  const LEVEL_COLORS = [
    { bg: '#c7d2fe', fill: '#3b5bdb' }, // niveau 1 — bleu
    { bg: '#99e9f2', fill: '#15aabf' }, // niveau 2 — bleu cyan
    { bg: '#b2f2bb', fill: '#37b24d' }, // niveau 3 — vert pomme
    { bg: '#ced4da', fill: '#495057' }, // niveau 4 — gris
  ];

  let tasks = [];
  let saveTimer = null;
  let dragRowFrom = null;
  let barDrag = null;
  let renderQueued = false;
  let zoom = 1;
  let dayPx = DAY_PX_BASE;

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function levelColors(level) {
    const idx = clamp(level || 0, 0, LEVEL_COLORS.length - 1);
    return LEVEL_COLORS[idx];
  }

  function parseDate(s) {
    return s ? new Date(s + 'T00:00:00') : null;
  }
  function addDays(d, n) {
    const r = new Date(d);
    r.setDate(r.getDate() + n);
    return r;
  }
  function diffDays(a, b) {
    return Math.round((b - a) / 86400000);
  }
  function fmtDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }
  function isoToEu(iso) {
    const d = parseDate(iso);
    if (!d) return '';
    const day = String(d.getDate()).padStart(2, '0');
    const month = String(d.getMonth() + 1).padStart(2, '0');
    return `${day}/${month}/${d.getFullYear()}`;
  }
  function euToIso(str) {
    const s = (str || '').trim();
    if (!s) return null;
    const m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (!m) return undefined;
    const day = parseInt(m[1], 10);
    const month = parseInt(m[2], 10);
    const year = parseInt(m[3], 10);
    const d = new Date(year, month - 1, day);
    if (d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day) return undefined;
    return fmtDate(d);
  }
  function requestRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      render();
    });
  }

  async function load() {
    const res = await fetch(`/api/gantt/${ganttId}`);
    const data = await res.json();
    tasks = data.tasks.map((t) => ({ ...t }));
    render();
  }

  function computeRange() {
    const starts = tasks.map((t) => t.start_date).filter(Boolean).map(parseDate);
    const ends = tasks.map((t) => t.end_date).filter(Boolean).map(parseDate);
    let min = starts.length ? new Date(Math.min(...starts)) : new Date();
    let max = ends.length ? new Date(Math.max(...ends)) : addDays(min, MIN_DAYS);
    if (diffDays(min, max) < MIN_DAYS) max = addDays(min, MIN_DAYS);
    min = addDays(min, -2);
    max = addDays(max, 2);
    return { min, max, days: diffDays(min, max) };
  }

  function renderTicks(min, days) {
    let html = '';
    for (let d = 0; d <= days; d += 7) {
      const date = addDays(min, d);
      html += `<div class="timeline-tick" style="left:${d * dayPx}px;">${date.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })}</div>`;
    }
    return html;
  }

  function renderBar(bar, fill, durationLabel, t, rangeMin) {
    if (!t.start_date || !t.end_date) {
      bar.style.display = 'none';
      durationLabel.style.display = 'none';
      return;
    }
    bar.style.display = 'block';
    durationLabel.style.display = 'block';
    const colors = levelColors(t.indent_level);
    bar.style.background = colors.bg;
    fill.style.background = colors.fill;
    const s = parseDate(t.start_date);
    const e = parseDate(t.end_date);
    const durationDays = diffDays(s, e) + 1;
    const left = diffDays(rangeMin, s) * dayPx;
    const width = Math.max(1, durationDays) * dayPx - 4;
    const barWidth = Math.max(6, width);
    bar.style.left = left + 'px';
    bar.style.width = barWidth + 'px';
    fill.style.width = Math.max(0, Math.min(100, t.progress_pct)) + '%';
    durationLabel.textContent = durationDays === 1 ? '1 jour' : `${durationDays} jours`;
  }

  function startBarDrag(e, t, edge) {
    e.preventDefault();
    e.stopPropagation();
    barDrag = {
      task: t,
      edge,
      startX: e.clientX,
      origStart: t.start_date,
      origEnd: t.end_date,
    };
    document.body.style.userSelect = 'none';
  }

  document.addEventListener('mousemove', (e) => {
    if (!barDrag) return;
    const deltaPx = e.clientX - barDrag.startX;
    const deltaDays = Math.round(deltaPx / dayPx);
    const t = barDrag.task;
    if (barDrag.edge === 'start') {
      let newStart = addDays(parseDate(barDrag.origStart), deltaDays);
      const end = parseDate(t.end_date);
      if (end && newStart > end) newStart = end;
      t.start_date = fmtDate(newStart);
    } else if (barDrag.edge === 'end') {
      let newEnd = addDays(parseDate(barDrag.origEnd), deltaDays);
      const start = parseDate(t.start_date);
      if (start && newEnd < start) newEnd = start;
      t.end_date = fmtDate(newEnd);
    } else {
      t.start_date = fmtDate(addDays(parseDate(barDrag.origStart), deltaDays));
      t.end_date = fmtDate(addDays(parseDate(barDrag.origEnd), deltaDays));
    }
    requestRender();
  });

  document.addEventListener('mouseup', () => {
    if (!barDrag) return;
    barDrag = null;
    document.body.style.userSelect = '';
    scheduleSave();
  });

  function makeBtn(label, title, onClick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'icon-btn';
    b.textContent = label;
    b.title = title;
    b.addEventListener('click', onClick);
    return b;
  }

  function render() {
    dayPx = DAY_PX_BASE * zoom;
    zoomLevelEl.textContent = Math.round(zoom * 100) + '%';
    const { min, days } = computeRange();
    table.innerHTML = '';

    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    headRow.innerHTML = `
      <th class="col-drag"></th>
      <th class="col-name">Tâche</th>
      <th class="col-start">Début</th>
      <th class="col-end">Fin</th>
      <th class="col-progress">%</th>
      <th class="col-actions">Actions</th>
      <th style="width:${days * dayPx}px; padding:0;">
        <div class="timeline-header" style="width:${days * dayPx}px;">${renderTicks(min, days)}</div>
      </th>
    `;
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    tasks.forEach((t, i) => {
      const tr = document.createElement('tr');
      tr.dataset.index = String(i);
      tr.addEventListener('dragover', (e) => {
        if (dragRowFrom === null) return;
        e.preventDefault();
        tr.classList.add('drag-over-row');
      });
      tr.addEventListener('dragleave', () => {
        tr.classList.remove('drag-over-row');
      });
      tr.addEventListener('drop', (e) => {
        e.preventDefault();
        tr.classList.remove('drag-over-row');
        const from = dragRowFrom;
        const to = Number(tr.dataset.index);
        dragRowFrom = null;
        if (from === null || from === to) return;
        const [moved] = tasks.splice(from, 1);
        tasks.splice(to, 0, moved);
        scheduleSave();
        render();
      });

      const tdDrag = document.createElement('td');
      tdDrag.className = 'col-drag';
      const handle = document.createElement('span');
      handle.className = 'drag-handle';
      handle.draggable = true;
      handle.title = 'Glisser pour réordonner';
      handle.textContent = '⠿';
      handle.addEventListener('dragstart', (e) => {
        dragRowFrom = i;
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', String(i));
      });
      handle.addEventListener('dragend', () => {
        dragRowFrom = null;
        table.querySelectorAll('.drag-over-row').forEach((el) => el.classList.remove('drag-over-row'));
      });
      tdDrag.appendChild(handle);

      const tdName = document.createElement('td');
      tdName.className = 'col-name';
      const spacer = document.createElement('span');
      spacer.className = 'indent-spacer';
      spacer.style.width = t.indent_level * 18 + 'px';
      const nameEl = document.createElement('input');
      nameEl.type = 'text';
      nameEl.className = 'task-name-input';
      nameEl.value = t.name || '';
      nameEl.placeholder = 'Nom de la tâche';
      nameEl.addEventListener('input', () => {
        t.name = nameEl.value;
        scheduleSave();
      });
      tdName.appendChild(spacer);
      tdName.appendChild(nameEl);

      const tdStart = document.createElement('td');
      tdStart.className = 'col-start';
      const startEl = document.createElement('input');
      startEl.type = 'text';
      startEl.className = 'date-input';
      startEl.placeholder = 'jj/mm/aaaa';
      startEl.value = isoToEu(t.start_date);
      startEl.addEventListener('change', () => {
        const iso = euToIso(startEl.value);
        if (iso === undefined) {
          startEl.classList.add('input-invalid');
          return;
        }
        startEl.classList.remove('input-invalid');
        t.start_date = iso;
        scheduleSave();
        render();
      });
      tdStart.appendChild(startEl);

      const tdEnd = document.createElement('td');
      tdEnd.className = 'col-end';
      const endEl = document.createElement('input');
      endEl.type = 'text';
      endEl.className = 'date-input';
      endEl.placeholder = 'jj/mm/aaaa';
      endEl.value = isoToEu(t.end_date);
      endEl.addEventListener('change', () => {
        const iso = euToIso(endEl.value);
        if (iso === undefined) {
          endEl.classList.add('input-invalid');
          return;
        }
        endEl.classList.remove('input-invalid');
        t.end_date = iso;
        scheduleSave();
        render();
      });
      tdEnd.appendChild(endEl);

      const tdProgress = document.createElement('td');
      tdProgress.className = 'col-progress';
      const progEl = document.createElement('input');
      progEl.type = 'number';
      progEl.min = 0;
      progEl.max = 100;
      progEl.value = t.progress_pct;
      progEl.addEventListener('input', () => {
        let v = parseInt(progEl.value, 10);
        if (isNaN(v)) v = 0;
        v = Math.max(0, Math.min(100, v));
        t.progress_pct = v;
        scheduleSave();
        renderBar(bar, fill, durationLabel, t, min);
      });
      tdProgress.appendChild(progEl);

      const tdActions = document.createElement('td');
      tdActions.className = 'col-actions';
      tdActions.appendChild(
        makeBtn('⇤', 'Désindenter', () => {
          if (t.indent_level > 0) {
            t.indent_level--;
            scheduleSave();
            render();
          }
        })
      );
      tdActions.appendChild(
        makeBtn('⇥', 'Indenter', () => {
          t.indent_level++;
          scheduleSave();
          render();
        })
      );
      tdActions.appendChild(
        makeBtn('✕', 'Supprimer', () => {
          if (confirm(`Supprimer la tâche « ${t.name || 'sans nom'} » ?`)) {
            tasks.splice(i, 1);
            scheduleSave();
            render();
          }
        })
      );

      const tdTimeline = document.createElement('td');
      tdTimeline.className = 'timeline-cell';
      tdTimeline.style.width = days * dayPx + 'px';
      const bar = document.createElement('div');
      bar.className = 'timeline-bar';
      bar.title = 'Glisser pour déplacer la tâche';
      bar.addEventListener('mousedown', (e) => startBarDrag(e, t, 'move'));
      const fill = document.createElement('div');
      fill.className = 'timeline-bar-fill';
      const handleLeft = document.createElement('div');
      handleLeft.className = 'bar-handle bar-handle-left';
      handleLeft.title = 'Glisser pour changer la date de début';
      handleLeft.addEventListener('mousedown', (e) => startBarDrag(e, t, 'start'));
      const handleRight = document.createElement('div');
      handleRight.className = 'bar-handle bar-handle-right';
      handleRight.title = 'Glisser pour changer la date de fin';
      handleRight.addEventListener('mousedown', (e) => startBarDrag(e, t, 'end'));
      const durationLabel = document.createElement('span');
      durationLabel.className = 'duration-label';
      bar.appendChild(fill);
      bar.appendChild(durationLabel);
      bar.appendChild(handleLeft);
      bar.appendChild(handleRight);
      tdTimeline.appendChild(bar);
      renderBar(bar, fill, durationLabel, t, min);

      tr.appendChild(tdDrag);
      tr.appendChild(tdName);
      tr.appendChild(tdStart);
      tr.appendChild(tdEnd);
      tr.appendChild(tdProgress);
      tr.appendChild(tdActions);
      tr.appendChild(tdTimeline);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
  }

  function scheduleSave() {
    saveStatus.textContent = '💾';
    saveStatus.title = 'Modifications non enregistrées...';
    saveStatus.className = 'save-status pending';
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 500);
  }

  async function save() {
    const payload = tasks.map((t, i) => ({
      id: t.id || null,
      client_ref: t.id ? null : t._ref,
      order_index: i,
      indent_level: t.indent_level,
      name: t.name || '',
      start_date: t.start_date || null,
      end_date: t.end_date || null,
      progress_pct: t.progress_pct || 0,
    }));
    const res = await fetch(`/api/gantt/${ganttId}/tasks`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      const data = await res.json();
      // the server assigns real ids to tasks created since the last save —
      // match them back up by client_ref (stable regardless of any reorder/
      // delete that happened locally while the request was in flight)
      (data.created || []).forEach(({ client_ref, id }) => {
        const t = tasks.find((task) => !task.id && task._ref === client_ref);
        if (t) t.id = id;
      });
    }
    saveStatus.textContent = '💾';
    saveStatus.title = 'Enregistré';
    saveStatus.className = 'save-status saved';
    setTimeout(() => {
      if (saveStatus.className === 'save-status saved') {
        saveStatus.textContent = '';
        saveStatus.title = '';
        saveStatus.className = 'save-status';
      }
    }, 1500);
  }

  addBtn.addEventListener('click', () => {
    tasks.push({
      _ref: 'r' + Math.random().toString(36).slice(2) + Date.now().toString(36),
      order_index: tasks.length,
      indent_level: 0,
      name: '',
      start_date: null,
      end_date: null,
      progress_pct: 0,
    });
    scheduleSave();
    render();
  });

  function setZoom(z) {
    zoom = clamp(z, ZOOM_MIN, ZOOM_MAX);
    render();
  }

  function stickyColumnsWidth() {
    let width = 0;
    table.querySelectorAll(
      'thead th.col-drag, thead th.col-name, thead th.col-start, thead th.col-end, thead th.col-progress, thead th.col-actions'
    ).forEach((el) => {
      width += el.offsetWidth;
    });
    return width;
  }

  zoomInBtn.addEventListener('click', () => setZoom(zoom * 1.25));
  zoomOutBtn.addEventListener('click', () => setZoom(zoom / 1.25));
  zoomResetBtn.addEventListener('click', () => setZoom(1));
  zoomFitBtn.addEventListener('click', () => {
    const { days } = computeRange();
    const available = scrollEl.clientWidth - stickyColumnsWidth();
    if (days > 0 && available > 20) {
      setZoom(available / (days * DAY_PX_BASE));
    }
  });

  function truncateText(ctx, text, maxWidth) {
    if (maxWidth <= 0) return '';
    if (ctx.measureText(text).width <= maxWidth) return text;
    let t = text;
    while (t.length > 0 && ctx.measureText(t + '…').width > maxWidth) {
      t = t.slice(0, -1);
    }
    return t.length > 0 ? t + '…' : '';
  }

  function exportPng() {
    const { min, days } = computeRange();
    const margin = 24;
    const titleH = 44;
    const headerH = 32;
    const rowH = 30;
    const nameW = 300;
    const dateW = 100;
    const progW = 60;
    const leftW = nameW + dateW * 2 + progW;
    const usableW = EXPORT_WIDTH - margin * 2;
    const timelineW = Math.max(200, usableW - leftW);
    const exportDayPx = days > 0 ? timelineW / days : timelineW;
    const rowCount = Math.max(1, tasks.length);
    const height = margin * 2 + titleH + headerH + rowCount * rowH;

    const canvas = document.createElement('canvas');
    canvas.width = EXPORT_WIDTH;
    canvas.height = height;
    const ctx = canvas.getContext('2d');

    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const left = margin;
    let y = margin;

    ctx.fillStyle = '#1f2430';
    ctx.font = 'bold 20px sans-serif';
    ctx.textBaseline = 'top';
    ctx.fillText(nameInput.value || 'Gantt', left, y);
    y += titleH;

    const colX = {
      name: left,
      start: left + nameW,
      end: left + nameW + dateW,
      progress: left + nameW + dateW * 2,
      timeline: left + leftW,
    };
    const tableTop = y;
    const tableHeight = headerH + tasks.length * rowH;

    ctx.fillStyle = '#f1f3f8';
    ctx.fillRect(left, y, usableW, headerH);
    ctx.fillStyle = '#495057';
    ctx.font = 'bold 12px sans-serif';
    ctx.textBaseline = 'middle';
    ctx.fillText('Tâche', colX.name + 6, y + headerH / 2);
    ctx.fillText('Début', colX.start + 6, y + headerH / 2);
    ctx.fillText('Fin', colX.end + 6, y + headerH / 2);
    ctx.fillText('%', colX.progress + 6, y + headerH / 2);

    ctx.strokeStyle = '#dde1e8';
    ctx.fillStyle = '#868e96';
    ctx.font = '10px sans-serif';
    for (let d = 0; d <= days; d += 7) {
      const x = colX.timeline + d * exportDayPx;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x, y + tableHeight);
      ctx.stroke();
      const date = addDays(min, d);
      ctx.fillStyle = '#868e96';
      ctx.fillText(date.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' }), x + 3, y + headerH / 2);
    }
    y += headerH;

    tasks.forEach((t, i) => {
      const rowY = y + i * rowH;
      if (i % 2 === 1) {
        ctx.fillStyle = '#f8f9fa';
        ctx.fillRect(left, rowY, usableW, rowH);
      }
      ctx.fillStyle = '#1f2430';
      ctx.font = '12px sans-serif';
      ctx.textBaseline = 'middle';
      const indentPx = (t.indent_level || 0) * 14;
      const nameText = truncateText(ctx, t.name || '', nameW - indentPx - 12);
      ctx.fillText(nameText, colX.name + 6 + indentPx, rowY + rowH / 2);
      ctx.fillText(isoToEu(t.start_date) || '—', colX.start + 6, rowY + rowH / 2);
      ctx.fillText(isoToEu(t.end_date) || '—', colX.end + 6, rowY + rowH / 2);
      ctx.fillText(`${t.progress_pct || 0}%`, colX.progress + 6, rowY + rowH / 2);

      if (t.start_date && t.end_date) {
        const s = parseDate(t.start_date);
        const e = parseDate(t.end_date);
        const durationDays = diffDays(s, e) + 1;
        const barLeft = colX.timeline + diffDays(min, s) * exportDayPx;
        const barWidth = Math.max(4, durationDays * exportDayPx - 3);
        const barTop = rowY + 4;
        const barH = rowH - 8;
        const colors = levelColors(t.indent_level);

        ctx.fillStyle = colors.bg;
        ctx.fillRect(barLeft, barTop, barWidth, barH);
        ctx.fillStyle = colors.fill;
        ctx.fillRect(barLeft, barTop, (barWidth * clamp(t.progress_pct || 0, 0, 100)) / 100, barH);

        const label = durationDays === 1 ? '1 jour' : `${durationDays} jours`;
        ctx.font = '10px sans-serif';
        const chipW = Math.min(ctx.measureText(label).width + 8, Math.max(barWidth - 6, 0));
        if (chipW > 4) {
          ctx.fillStyle = 'rgba(31, 36, 48, 0.55)';
          ctx.fillRect(barLeft + 3, rowY + rowH / 2 - 7, chipW, 14);
          ctx.fillStyle = '#ffffff';
          ctx.fillText(truncateText(ctx, label, chipW - 6), barLeft + 7, rowY + rowH / 2);
        }
      }
    });

    ctx.strokeStyle = '#dde1e8';
    ctx.strokeRect(left, tableTop, usableW, tableHeight);

    canvas.toBlob((blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${(nameInput.value || 'gantt').trim().replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }, 'image/png');
  }

  exportPngBtn.addEventListener('click', exportPng);

  scrollEl.addEventListener(
    'wheel',
    (e) => {
      if (!e.target.closest('.timeline-cell, .timeline-header')) return;
      e.preventDefault();

      const rect = scrollEl.getBoundingClientRect();
      const cursorContentX = e.clientX - rect.left + scrollEl.scrollLeft - stickyColumnsWidth();

      const oldZoom = zoom;
      const newZoom = clamp(zoom * Math.pow(1.0015, -e.deltaY), ZOOM_MIN, ZOOM_MAX);
      if (newZoom === oldZoom) return;
      const ratio = newZoom / oldZoom;

      zoom = newZoom;
      render();
      scrollEl.scrollLeft += cursorContentX * (ratio - 1);
    },
    { passive: false }
  );

  nameInput.addEventListener('change', () => {
    fetch(`/api/gantt/${ganttId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: nameInput.value }),
    });
  });

  if (window.initHistoryPanel) {
    window.initHistoryPanel({ buttonId: 'history-btn', apiUrl: `/api/gantt/${ganttId}/history` });
  }

  load();
})();
