(function () {
  const root = document.querySelector('.kanban-page');
  const boardId = root.dataset.kanbanId;
  const nameInput = document.getElementById('kanban-name');
  const statusEl = document.getElementById('kanban-status');
  const addColumnBtn = document.getElementById('kanban-add-column-btn');
  const boardEl = document.getElementById('kanban-board');

  const DRAG_THRESHOLD = 4;

  let ws = null;
  let pendingColumnRef = null;
  let pendingCardRef = null;

  // columnId -> { id, title, order_index, el, bodyEl, titleInput }
  const columns = new Map();
  // cardId -> { id, column_id, order_index, text, color, el, textEl }
  const cards = new Map();

  function sendOp(payload) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  }

  function showTransientStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.style.color = isError ? '#c92a2a' : '';
    setTimeout(() => {
      if (statusEl.textContent === text) {
        statusEl.textContent = '';
        statusEl.style.color = '';
      }
    }, 3000);
  }

  // .textContent ignores <br>/block boundaries entirely (multi-line edits
  // would come back as one concatenated line); .innerText respects the
  // rendered layout and turns line breaks back into '\n'. Contenteditable
  // often leaves one stray trailing <br>, so trim a single trailing newline.
  function getCardText(el) {
    return el.innerText.replace(/\n$/, '');
  }

  // build actual <br> elements for line breaks instead of relying on
  // white-space:pre-wrap to render literal '\n' characters — keeps
  // multi-line cards working even if that CSS isn't in effect for any
  // reason. Text is inserted via createTextNode, never HTML, so it stays
  // safe against injection.
  function setCardText(el, text) {
    el.textContent = '';
    const lines = (text || '').split('\n');
    lines.forEach((line, i) => {
      if (i > 0) el.appendChild(document.createElement('br'));
      if (line) el.appendChild(document.createTextNode(line));
    });
  }

  // ---- card rendering ----
  function createCardEl(card) {
    const div = document.createElement('div');
    div.className = 'kanban-card';
    div.dataset.cardId = String(card.id);
    div.style.background = card.color;

    const handle = document.createElement('div');
    handle.className = 'kanban-card-handle';
    handle.title = 'Glisser pour déplacer';
    handle.textContent = '⠿';
    div.appendChild(handle);

    const text = document.createElement('div');
    text.className = 'kanban-card-text';
    text.contentEditable = 'true';
    setCardText(text, card.text);
    div.appendChild(text);

    const toolbar = document.createElement('div');
    toolbar.className = 'kanban-card-toolbar';

    const colorInput = document.createElement('input');
    colorInput.type = 'color';
    colorInput.title = 'Couleur';
    colorInput.value = card.color;
    toolbar.appendChild(colorInput);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'icon-btn kanban-card-delete';
    delBtn.title = 'Supprimer la carte';
    delBtn.textContent = '✕';
    toolbar.appendChild(delBtn);

    div.appendChild(toolbar);

    text.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        // make plain Enter behave exactly like Shift+Enter (insert a line
        // break) instead of letting the browser start a new block element,
        // whose text would then get concatenated with no separator on save
        e.preventDefault();
        document.execCommand('insertLineBreak');
      }
    });
    text.addEventListener('blur', () => {
      const c = cards.get(card.id);
      const value = getCardText(text);
      if (!c || c.text === value) return;
      c.text = value;
      sendOp({ op: 'update_card', id: card.id, text: c.text, color: c.color });
    });
    colorInput.addEventListener('input', () => {
      div.style.background = colorInput.value;
    });
    colorInput.addEventListener('change', () => {
      const c = cards.get(card.id);
      if (!c) return;
      c.color = colorInput.value;
      sendOp({ op: 'update_card', id: card.id, text: c.text, color: c.color });
    });
    colorInput.addEventListener('mousedown', (e) => e.stopPropagation());
    delBtn.addEventListener('mousedown', (e) => e.stopPropagation());
    delBtn.addEventListener('click', () => {
      if (confirm('Supprimer cette carte ?')) {
        sendOp({ op: 'delete_card', id: card.id });
      }
    });
    handle.addEventListener('mousedown', (e) => startCardDragCandidate(e, card.id));

    return { el: div, textEl: text };
  }

  function upsertCard(data) {
    let entry = cards.get(data.id);
    if (!entry) {
      const built = createCardEl(data);
      entry = { ...data, el: built.el, textEl: built.textEl };
      cards.set(data.id, entry);
    } else {
      entry.column_id = data.column_id;
      entry.order_index = data.order_index;
      entry.color = data.color;
      entry.el.style.background = data.color;
      if (document.activeElement !== entry.textEl && entry.text !== data.text) {
        setCardText(entry.textEl, data.text);
      }
      entry.text = data.text;
    }
    const col = columns.get(data.column_id);
    if (col) {
      placeCardInColumn(entry, col);
    }
    return entry;
  }

  function placeCardInColumn(cardEntry, col) {
    const siblings = Array.from(col.bodyEl.querySelectorAll('.kanban-card')).filter(
      (el) => Number(el.dataset.cardId) !== cardEntry.id
    );
    const targetIndex = Math.max(0, Math.min(cardEntry.order_index, siblings.length));
    const ref = siblings[targetIndex] || null;
    col.bodyEl.insertBefore(cardEntry.el, ref);
  }

  function removeCard(id) {
    const entry = cards.get(id);
    if (!entry) return;
    entry.el.remove();
    cards.delete(id);
  }

  // ---- column rendering ----
  function createColumnEl(column) {
    const div = document.createElement('div');
    div.className = 'kanban-column';
    div.dataset.columnId = String(column.id);

    const header = document.createElement('div');
    header.className = 'kanban-column-header';

    const handle = document.createElement('div');
    handle.className = 'kanban-column-handle';
    handle.title = 'Glisser pour déplacer la colonne';
    handle.textContent = '⠿';
    header.appendChild(handle);

    const titleInput = document.createElement('input');
    titleInput.type = 'text';
    titleInput.className = 'kanban-column-title';
    titleInput.value = column.title;
    header.appendChild(titleInput);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'icon-btn';
    delBtn.title = 'Supprimer la colonne';
    delBtn.textContent = '✕';
    header.appendChild(delBtn);

    div.appendChild(header);

    const body = document.createElement('div');
    body.className = 'kanban-column-body';
    div.appendChild(body);

    const addCardBtn = document.createElement('button');
    addCardBtn.type = 'button';
    addCardBtn.className = 'kanban-add-card-btn';
    addCardBtn.textContent = '+ Carte';
    div.appendChild(addCardBtn);

    titleInput.addEventListener('change', () => {
      sendOp({ op: 'rename_column', id: column.id, title: titleInput.value });
    });
    handle.addEventListener('mousedown', (e) => startColumnDragCandidate(e, column.id));
    delBtn.addEventListener('click', () => {
      if (confirm('Supprimer cette colonne et toutes ses cartes ?')) {
        sendOp({ op: 'delete_column', id: column.id });
      }
    });
    addCardBtn.addEventListener('click', () => {
      const ref = 'c' + Math.random().toString(36).slice(2);
      pendingCardRef = ref;
      sendOp({ op: 'create_card', column_id: column.id, text: '', color: '#fff3bf', client_ref: ref });
    });

    return { el: div, bodyEl: body, titleInput };
  }

  function upsertColumn(data) {
    let entry = columns.get(data.id);
    if (!entry) {
      const built = createColumnEl(data);
      entry = { ...data, el: built.el, bodyEl: built.bodyEl, titleInput: built.titleInput };
      columns.set(data.id, entry);
    } else {
      entry.order_index = data.order_index;
      if (document.activeElement !== entry.titleInput) entry.titleInput.value = data.title;
      entry.title = data.title;
    }
    placeColumn(entry);
    return entry;
  }

  function placeColumn(colEntry) {
    const siblings = Array.from(boardEl.querySelectorAll('.kanban-column')).filter(
      (el) => Number(el.dataset.columnId) !== colEntry.id
    );
    const targetIndex = Math.max(0, Math.min(colEntry.order_index, siblings.length));
    const ref = siblings[targetIndex] || null;
    boardEl.insertBefore(colEntry.el, ref);
  }

  function removeColumn(id) {
    const entry = columns.get(id);
    if (!entry) return;
    Array.from(cards.values())
      .filter((c) => c.column_id === id)
      .forEach((c) => removeCard(c.id));
    entry.el.remove();
    columns.delete(id);
  }

  function reorderColumnsFrom(order) {
    order.forEach((id, i) => {
      const entry = columns.get(id);
      if (entry) entry.order_index = i;
    });
    order.forEach((id) => {
      const entry = columns.get(id);
      if (entry) placeColumn(entry);
    });
  }

  function reorderCardsFrom(columnsMap) {
    Object.entries(columnsMap).forEach(([columnId, cardIds]) => {
      const col = columns.get(Number(columnId));
      cardIds.forEach((id, i) => {
        const entry = cards.get(id);
        if (!entry) return;
        entry.column_id = Number(columnId);
        entry.order_index = i;
      });
      if (col) {
        cardIds.forEach((id) => {
          const entry = cards.get(id);
          if (entry) placeCardInColumn(entry, col);
        });
      }
    });
  }

  // ---- card drag & drop ----
  let cardDragCandidate = null;
  let cardDragState = null;

  function startCardDragCandidate(e, cardId) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    cardDragCandidate = { cardId, startX: e.clientX, startY: e.clientY };
  }

  function promoteCardDrag(e) {
    const entry = cards.get(cardDragCandidate.cardId);
    cardDragCandidate = null;
    if (!entry) return;
    const rect = entry.el.getBoundingClientRect();

    const placeholder = document.createElement('div');
    placeholder.className = 'kanban-card-placeholder';
    placeholder.style.height = rect.height + 'px';
    entry.el.parentElement.insertBefore(placeholder, entry.el);

    entry.el.classList.add('dragging');
    entry.el.style.position = 'fixed';
    entry.el.style.width = rect.width + 'px';
    entry.el.style.left = rect.left + 'px';
    entry.el.style.top = rect.top + 'px';
    document.body.appendChild(entry.el);

    cardDragState = {
      cardId: entry.id,
      el: entry.el,
      placeholder,
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top,
    };
  }

  function updateCardDrag(e) {
    const { el, offsetX, offsetY, placeholder } = cardDragState;
    el.style.left = e.clientX - offsetX + 'px';
    el.style.top = e.clientY - offsetY + 'px';

    el.style.pointerEvents = 'none';
    const target = document.elementFromPoint(e.clientX, e.clientY);
    el.style.pointerEvents = '';

    const body = target ? target.closest('.kanban-column-body') : null;
    if (!body) return;

    const siblingCards = Array.from(body.querySelectorAll('.kanban-card:not(.dragging)'));
    let inserted = false;
    for (const sib of siblingCards) {
      const sibRect = sib.getBoundingClientRect();
      if (e.clientY < sibRect.top + sibRect.height / 2) {
        body.insertBefore(placeholder, sib);
        inserted = true;
        break;
      }
    }
    if (!inserted) body.appendChild(placeholder);
  }

  function finishCardDrag() {
    const { cardId, el, placeholder } = cardDragState;
    const body = placeholder.parentElement;
    const columnEl = body.closest('.kanban-column');
    const columnId = Number(columnEl.dataset.columnId);
    const index = Array.from(body.children).indexOf(placeholder);

    el.style.position = '';
    el.style.left = '';
    el.style.top = '';
    el.style.width = '';
    el.style.pointerEvents = '';
    el.classList.remove('dragging');
    body.insertBefore(el, placeholder);
    placeholder.remove();

    cardDragState = null;
    sendOp({ op: 'move_card', id: cardId, column_id: columnId, index });
  }

  // ---- column drag & drop ----
  let columnDragCandidate = null;
  let columnDragState = null;

  function startColumnDragCandidate(e, columnId) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    columnDragCandidate = { columnId, startX: e.clientX, startY: e.clientY };
  }

  function promoteColumnDrag(e) {
    const entry = columns.get(columnDragCandidate.columnId);
    columnDragCandidate = null;
    if (!entry) return;
    const rect = entry.el.getBoundingClientRect();

    const placeholder = document.createElement('div');
    placeholder.className = 'kanban-column-placeholder';
    placeholder.style.width = rect.width + 'px';
    placeholder.style.height = rect.height + 'px';
    entry.el.parentElement.insertBefore(placeholder, entry.el);

    entry.el.classList.add('dragging');
    entry.el.style.position = 'fixed';
    entry.el.style.width = rect.width + 'px';
    entry.el.style.height = rect.height + 'px';
    entry.el.style.left = rect.left + 'px';
    entry.el.style.top = rect.top + 'px';
    document.body.appendChild(entry.el);

    columnDragState = {
      columnId: entry.id,
      el: entry.el,
      placeholder,
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top,
    };
  }

  function updateColumnDrag(e) {
    const { el, offsetX, offsetY, placeholder } = columnDragState;
    el.style.left = e.clientX - offsetX + 'px';
    el.style.top = e.clientY - offsetY + 'px';

    const siblingColumns = Array.from(boardEl.querySelectorAll('.kanban-column:not(.dragging)'));
    let inserted = false;
    for (const sib of siblingColumns) {
      const sibRect = sib.getBoundingClientRect();
      if (e.clientX < sibRect.left + sibRect.width / 2) {
        boardEl.insertBefore(placeholder, sib);
        inserted = true;
        break;
      }
    }
    if (!inserted) boardEl.appendChild(placeholder);
  }

  function finishColumnDrag() {
    const { columnId, el, placeholder } = columnDragState;
    const index = Array.from(boardEl.children).indexOf(placeholder);

    el.style.position = '';
    el.style.left = '';
    el.style.top = '';
    el.style.width = '';
    el.style.height = '';
    el.classList.remove('dragging');
    boardEl.insertBefore(el, placeholder);
    placeholder.remove();

    columnDragState = null;
    sendOp({ op: 'move_column', id: columnId, index });
  }

  document.addEventListener('mousemove', (e) => {
    if (cardDragCandidate && !cardDragState) {
      const dx = e.clientX - cardDragCandidate.startX;
      const dy = e.clientY - cardDragCandidate.startY;
      if (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD) {
        promoteCardDrag(e);
      }
    }
    if (cardDragState) updateCardDrag(e);

    if (columnDragCandidate && !columnDragState) {
      const dx = e.clientX - columnDragCandidate.startX;
      const dy = e.clientY - columnDragCandidate.startY;
      if (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD) {
        promoteColumnDrag(e);
      }
    }
    if (columnDragState) updateColumnDrag(e);
  });
  document.addEventListener('mouseup', () => {
    if (cardDragState) finishCardDrag();
    cardDragCandidate = null;

    if (columnDragState) finishColumnDrag();
    columnDragCandidate = null;
  });

  // ---- websocket ----
  function connectWs() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/kanban/${boardId}`);

    ws.addEventListener('open', () => {
      statusEl.textContent = 'Connecté';
      setTimeout(() => {
        if (statusEl.textContent === 'Connecté') statusEl.textContent = '';
      }, 1500);
    });
    ws.addEventListener('close', () => {
      statusEl.textContent = 'Déconnecté — reconnexion...';
      setTimeout(connectWs, 2000);
    });
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.op === 'column_created') {
        upsertColumn(msg.column);
        if (msg.client_ref && msg.client_ref === pendingColumnRef) {
          pendingColumnRef = null;
          const entry = columns.get(msg.column.id);
          if (entry) entry.titleInput.focus();
        }
      } else if (msg.op === 'column_renamed') {
        const entry = columns.get(msg.id);
        if (entry) {
          entry.title = msg.title;
          if (document.activeElement !== entry.titleInput) entry.titleInput.value = msg.title;
        }
      } else if (msg.op === 'column_deleted') {
        removeColumn(msg.id);
      } else if (msg.op === 'columns_reordered') {
        reorderColumnsFrom(msg.order);
      } else if (msg.op === 'card_created') {
        upsertCard(msg.card);
        if (msg.client_ref && msg.client_ref === pendingCardRef) {
          pendingCardRef = null;
          const entry = cards.get(msg.card.id);
          if (entry) entry.textEl.focus();
        }
      } else if (msg.op === 'card_updated') {
        upsertCard(msg.card);
      } else if (msg.op === 'card_moved') {
        reorderCardsFrom(msg.columns);
      } else if (msg.op === 'card_deleted') {
        removeCard(msg.id);
      } else if (msg.op === 'error') {
        console.error('kanban error:', msg.detail);
        showTransientStatus(msg.detail, true);
      }
    });
  }

  async function load() {
    const res = await fetch(`/api/kanban/${boardId}`);
    const data = await res.json();
    data.columns.forEach((col) => {
      upsertColumn(col);
      col.cards.forEach(upsertCard);
    });
    connectWs();
  }

  addColumnBtn.addEventListener('click', () => {
    const ref = 'c' + Math.random().toString(36).slice(2);
    pendingColumnRef = ref;
    sendOp({ op: 'create_column', title: 'Nouvelle colonne', client_ref: ref });
  });

  nameInput.addEventListener('change', () => {
    fetch(`/api/kanban/${boardId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: nameInput.value }),
    });
  });

  if (window.initHistoryPanel) {
    window.initHistoryPanel({ buttonId: 'history-btn', apiUrl: `/api/kanban/${boardId}/history` });
  }

  load();
})();
