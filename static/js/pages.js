(function () {
  const root = document.querySelector('.pages-page');
  const boardId = root.dataset.pagesBoardId;
  const nameInput = document.getElementById('pages-name');
  const statusEl = document.getElementById('pages-status');
  const addRootBtn = document.getElementById('pages-add-root-btn');
  const treeEl = document.getElementById('pages-tree');
  const contentEl = document.getElementById('pages-content');
  const imageFileInput = document.getElementById('pages-image-file-input');

  const DEFAULT_FONT_SIZE = 16;
  const MAX_TEXT_PARAGRAPHS = 200;
  const MAX_TEXT_RUNS_PER_PARAGRAPH = 100;
  const MAX_TABLE_ROWS = 50;
  const MAX_TABLE_COLS = 20;
  const CODE_LANGUAGES = ['text', 'python', 'javascript', 'html', 'css', 'sql', 'bash', 'json', 'autre'];

  let ws = null;
  let selectedPageId = null;
  let editingBlockId = null;
  let pendingImagePageId = null;
  const collapsedIds = new Set();

  // page id -> { id, parent_id, order_index, title }
  const pages = new Map();
  // block id -> { id, page_id, order_index, type, data }
  const blocks = new Map();
  // block id -> its rendered wrapper element (.pagesContentEl set on it for
  // text blocks) — lets block_created/updated/moved/deleted patch the DOM
  // in place instead of a full renderContent(), so one viewer's in-progress
  // edit on block A is never discarded by an unrelated change to block B
  const blockRenderRefs = new Map();
  let blocksWrapEl = null;
  let blocksEmptyEl = null;
  let titleInputEl = null;
  // one shared drag state for whichever image block's resize handle is
  // currently held — avoids attaching a new window-level mousemove/mouseup
  // listener per image block (those would never get cleaned up)
  let imageResizeState = null;

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

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function siblingsOf(parentId, excludeId) {
    return Array.from(pages.values())
      .filter((p) => p.parent_id === parentId && p.id !== excludeId)
      .sort((a, b) => a.order_index - b.order_index);
  }

  function hasChildren(id) {
    return Array.from(pages.values()).some((p) => p.parent_id === id);
  }

  function blocksOfPage(pageId) {
    return Array.from(blocks.values())
      .filter((b) => b.page_id === pageId)
      .sort((a, b) => a.order_index - b.order_index);
  }

  // ---- tree mutations (every one is just a move_page/create_page/etc. op —
  // the server is the only source of truth; the tree re-renders once its
  // broadcast comes back, same optimistic-free pattern as kanban columns) ----

  function createPage(parentId) {
    sendOp({ op: 'create_page', parent_id: parentId, title: 'Nouvelle page' });
  }

  function moveSibling(page, delta) {
    const siblings = siblingsOf(page.parent_id, null);
    const i = siblings.findIndex((p) => p.id === page.id);
    const j = i + delta;
    if (i === -1 || j < 0 || j >= siblings.length) return;
    sendOp({ op: 'move_page', id: page.id, parent_id: page.parent_id, index: j });
  }

  function outdentPage(page) {
    if (page.parent_id === null) return;
    const parent = pages.get(page.parent_id);
    const grandParentId = parent ? parent.parent_id : null;
    const destSiblings = siblingsOf(grandParentId, page.id);
    const parentIndex = parent ? destSiblings.findIndex((p) => p.id === parent.id) : -1;
    const index = parentIndex + 1; // right after its former parent
    sendOp({ op: 'move_page', id: page.id, parent_id: grandParentId, index });
  }

  function indentPage(page) {
    const siblings = siblingsOf(page.parent_id, null);
    const i = siblings.findIndex((p) => p.id === page.id);
    if (i <= 0) return; // no previous sibling to become a child of
    const newParent = siblings[i - 1];
    const destSiblings = siblingsOf(newParent.id, page.id);
    sendOp({ op: 'move_page', id: page.id, parent_id: newParent.id, index: destSiblings.length });
  }

  function deletePage(page) {
    if (hasChildren(page.id)) {
      showTransientStatus('Détachez ou supprimez ses sous-pages avant de la supprimer', true);
      return;
    }
    if (!confirm(`Supprimer la page « ${page.title} » ?`)) return;
    sendOp({ op: 'delete_page', id: page.id });
  }

  // ---- tree rendering ----

  function selectPage(id) {
    selectedPageId = id;
    editingBlockId = null;
    renderTree();
    renderContent();
  }

  function buildList(parentId) {
    const ul = document.createElement('ul');
    ul.className = 'pages-tree-list';
    siblingsOf(parentId, null).forEach((p) => {
      const li = document.createElement('li');
      li.className = 'pages-tree-item';

      const row = document.createElement('div');
      row.className = 'pages-tree-row';
      if (p.id === selectedPageId) row.classList.add('selected');

      const childrenExist = hasChildren(p.id);
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'pages-tree-toggle';
      toggle.textContent = childrenExist ? (collapsedIds.has(p.id) ? '▸' : '▾') : '';
      toggle.disabled = !childrenExist;
      toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        if (collapsedIds.has(p.id)) collapsedIds.delete(p.id);
        else collapsedIds.add(p.id);
        renderTree();
      });
      row.appendChild(toggle);

      const titleBtn = document.createElement('button');
      titleBtn.type = 'button';
      titleBtn.className = 'pages-tree-title';
      titleBtn.textContent = p.title;
      titleBtn.addEventListener('click', () => selectPage(p.id));
      row.appendChild(titleBtn);

      const actions = document.createElement('div');
      actions.className = 'pages-tree-actions';
      const addAction = (symbol, title, onClick, disabled) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'icon-btn pages-tree-action';
        btn.title = title;
        btn.textContent = symbol;
        btn.disabled = !!disabled;
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          onClick();
        });
        actions.appendChild(btn);
      };
      addAction('+', 'Ajouter une sous-page', () => createPage(p.id));
      addAction('↑', 'Monter', () => moveSibling(p, -1));
      addAction('↓', 'Descendre', () => moveSibling(p, 1));
      addAction('⇤', 'Désindenter', () => outdentPage(p), p.parent_id === null);
      addAction('⇥', 'Indenter sous la page précédente', () => indentPage(p));
      addAction('🗑', 'Supprimer', () => deletePage(p));
      row.appendChild(actions);

      li.appendChild(row);
      if (childrenExist && !collapsedIds.has(p.id)) li.appendChild(buildList(p.id));
      ul.appendChild(li);
    });
    return ul;
  }

  function renderTree() {
    treeEl.textContent = '';
    treeEl.appendChild(buildList(null));
    if (pages.size === 0) {
      const empty = document.createElement('p');
      empty.className = 'pages-empty';
      empty.textContent = "Aucune page pour l'instant.";
      treeEl.appendChild(empty);
    }
  }

  // ---- rich text (text block) — paragraphs/runs model, ported and
  // trimmed from whiteboard.js's text element editor: same contentEditable
  // DOM <-> JSON serialization, minus the canvas-only concerns (color,
  // dragging, reactions) a block in a vertical stack doesn't need ----

  function blankRun() {
    return { text: '', bold: false, italic: false, underline: false, strikethrough: false, font_size: DEFAULT_FONT_SIZE };
  }
  function blankParagraph() {
    return { bullet: false, heading: 0, runs: [blankRun()] };
  }

  function normalizeTextData(data) {
    if (data && data.paragraphs) return data;
    return { paragraphs: [blankParagraph()] };
  }

  function renderTextParagraphs(content, rawData) {
    const data = normalizeTextData(rawData);
    content.innerHTML = '';
    let currentList = null;
    data.paragraphs.forEach((para) => {
      const tag = para.heading ? `h${para.heading}` : para.bullet ? 'li' : 'div';
      const lineEl = document.createElement(tag);
      const runs = para.runs.length ? para.runs : [blankRun()];
      runs.forEach((run) => {
        const span = document.createElement('span');
        span.textContent = run.text || '';
        span.style.fontWeight = run.bold ? '700' : '400';
        span.style.fontStyle = run.italic ? 'italic' : 'normal';
        span.style.fontSize = (run.font_size || DEFAULT_FONT_SIZE) + 'px';
        const decorations = [];
        if (run.underline) decorations.push('underline');
        if (run.strikethrough) decorations.push('line-through');
        span.style.textDecoration = decorations.length ? decorations.join(' ') : 'none';
        lineEl.appendChild(span);
      });
      if (para.bullet && !para.heading) {
        if (!currentList) {
          currentList = document.createElement('ul');
          content.appendChild(currentList);
        }
        currentList.appendChild(lineEl);
      } else {
        currentList = null;
        content.appendChild(lineEl);
      }
    });
  }

  const HEADING_LEVEL_BY_TAG = { H1: 1, H2: 2, H3: 3, H4: 4 };
  const LINE_TAGS = new Set(['DIV', 'P', 'LI', 'H1', 'H2', 'H3', 'H4']);

  function serializeTextParagraphs(content) {
    const paragraphs = [];
    let current = { bullet: false, heading: 0, runs: [] };

    function pushParagraph() {
      if (current.runs.length === 0) current.runs.push(blankRun());
      paragraphs.push(current);
      current = { bullet: false, heading: 0, runs: [] };
    }

    function walk(node, style) {
      if (node.nodeType === Node.TEXT_NODE) {
        if (node.textContent) current.runs.push({ text: node.textContent, ...style });
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;

      const tag = node.tagName;
      if (tag === 'BR') {
        pushParagraph();
        return;
      }
      if (tag === 'LI') {
        if (current.runs.length > 0 || current.bullet) pushParagraph();
        current.bullet = true;
        Array.from(node.childNodes).forEach((child) => walk(child, style));
        pushParagraph();
        return;
      }
      if (tag === 'UL' || tag === 'OL') {
        Array.from(node.childNodes).forEach((child) => walk(child, style));
        return;
      }
      if (tag === 'DIV' || tag === 'P' || HEADING_LEVEL_BY_TAG[tag]) {
        if (current.runs.length > 0) pushParagraph();
        current.heading = HEADING_LEVEL_BY_TAG[tag] || 0;
        Array.from(node.childNodes).forEach((child) => walk(child, style));
        pushParagraph();
        return;
      }

      const next = { ...style };
      if (tag === 'B' || tag === 'STRONG') next.bold = true;
      if (tag === 'I' || tag === 'EM') next.italic = true;
      if (tag === 'U') next.underline = true;
      if (tag === 'S' || tag === 'STRIKE' || tag === 'DEL') next.strikethrough = true;
      if (tag === 'SPAN' && node.style.fontSize) {
        const px = parseInt(node.style.fontSize, 10);
        if (!Number.isNaN(px)) next.font_size = clamp(px, 8, 96);
      }
      const decoration = node.style && (node.style.textDecorationLine || node.style.textDecoration);
      if (decoration) {
        if (decoration.includes('underline')) next.underline = true;
        if (decoration.includes('line-through')) next.strikethrough = true;
      }
      Array.from(node.childNodes).forEach((child) => walk(child, next));
    }

    Array.from(content.childNodes).forEach((child) =>
      walk(child, { bold: false, italic: false, underline: false, strikethrough: false, font_size: DEFAULT_FONT_SIZE })
    );
    pushParagraph();

    const merged = paragraphs
      .map((para) => {
        const runs = [];
        para.runs.forEach((run) => {
          const prev = runs[runs.length - 1];
          if (
            prev &&
            prev.bold === run.bold &&
            prev.italic === run.italic &&
            prev.underline === run.underline &&
            prev.strikethrough === run.strikethrough &&
            prev.font_size === run.font_size
          ) {
            prev.text += run.text;
          } else {
            runs.push({ ...run });
          }
        });
        return { bullet: para.bullet, heading: para.heading || 0, runs: runs.length ? runs : [blankRun()] };
      })
      .slice(0, MAX_TEXT_PARAGRAPHS);
    while (
      merged.length > 1 &&
      !merged[merged.length - 1].bullet &&
      merged[merged.length - 1].runs.length === 1 &&
      merged[merged.length - 1].runs[0].text === ''
    ) {
      merged.pop();
    }
    merged.forEach((para) => {
      if (para.runs.length > MAX_TEXT_RUNS_PER_PARAGRAPH) para.runs.length = MAX_TEXT_RUNS_PER_PARAGRAPH;
    });
    return merged;
  }

  function applyFontSizeToSelection(container, px) {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
    document.execCommand('fontSize', false, '7');
    const spans = Array.from(container.querySelectorAll('font[size="7"]')).map((fontEl) => {
      const span = document.createElement('span');
      span.style.fontSize = px + 'px';
      while (fontEl.firstChild) span.appendChild(fontEl.firstChild);
      fontEl.replaceWith(span);
      return span;
    });
    sel.removeAllRanges();
    if (spans.length > 0) {
      const newRange = document.createRange();
      newRange.setStartBefore(spans[0]);
      newRange.setEndAfter(spans[spans.length - 1]);
      sel.addRange(newRange);
    }
  }

  // ---- block mutations ----

  function createTextBlock(pageId) {
    sendOp({ op: 'create_block', page_id: pageId, block: { type: 'text', data: {} } });
  }

  function createTableBlock(pageId) {
    sendOp({ op: 'create_block', page_id: pageId, block: { type: 'table', data: {} } });
  }

  function createCodeBlock(pageId) {
    sendOp({ op: 'create_block', page_id: pageId, block: { type: 'code', data: {} } });
  }

  function createLinkBlock(pageId) {
    sendOp({ op: 'create_block', page_id: pageId, block: { type: 'link', data: {} } });
  }

  function promptImageBlock(pageId) {
    pendingImagePageId = pageId;
    imageFileInput.click();
  }

  async function uploadAndCreateImageBlock(pageId, file) {
    const formData = new FormData();
    formData.append('file', file);
    let res;
    try {
      res = await fetch(`/api/pages/${boardId}/upload-image`, { method: 'POST', body: formData });
    } catch (err) {
      showTransientStatus("Échec de l'envoi de l'image", true);
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      showTransientStatus(body.detail || 'Image refusée', true);
      return;
    }
    const { url } = await res.json();
    sendOp({ op: 'create_block', page_id: pageId, block: { type: 'image', data: { src: url, caption: '' } } });
  }

  imageFileInput.addEventListener('change', () => {
    const file = imageFileInput.files[0];
    const pageId = pendingImagePageId;
    imageFileInput.value = '';
    pendingImagePageId = null;
    if (file && pageId !== null) uploadAndCreateImageBlock(pageId, file);
  });

  // image resize drag — one shared listener pair for every image block's
  // handle (see imageResizeState)
  window.addEventListener('mousemove', (e) => {
    if (!imageResizeState) return;
    const { img, startX, startWidth } = imageResizeState;
    img.style.width = clamp(Math.round(startWidth + (e.clientX - startX)), 50, 2000) + 'px';
  });
  window.addEventListener('mouseup', () => {
    if (!imageResizeState) return;
    const { img, blockId } = imageResizeState;
    imageResizeState = null;
    const current = blocks.get(blockId);
    if (!current) return;
    const width = parseInt(img.style.width, 10) || null;
    sendOp({
      op: 'update_block',
      id: blockId,
      block: { type: 'image', data: { src: current.data.src, caption: current.data.caption || '', width } },
    });
  });

  function moveBlock(block, delta) {
    const siblings = blocksOfPage(block.page_id);
    const i = siblings.findIndex((b) => b.id === block.id);
    const j = i + delta;
    if (i === -1 || j < 0 || j >= siblings.length) return;
    sendOp({ op: 'move_block', id: block.id, index: j });
  }

  function deleteBlock(block) {
    if (!confirm('Supprimer ce bloc ?')) return;
    sendOp({ op: 'delete_block', id: block.id });
  }

  function commitTextBlock(block, contentEl, overrides) {
    // `framed` is a block-level flag stored alongside `paragraphs`, not
    // inside them — every commit must carry its current value forward
    // (update_block replaces the whole data object) or it would silently
    // reset to false the next time anything else on the block is edited
    const current = blocks.get(block.id);
    const framed = overrides && 'framed' in overrides ? overrides.framed : !!(current && current.data.framed);
    const paragraphs = serializeTextParagraphs(contentEl);
    sendOp({ op: 'update_block', id: block.id, block: { type: 'text', data: { paragraphs, framed } } });
  }

  // ---- block rendering ----

  function createTextBlockEl(block) {
    const wrap = document.createElement('div');
    wrap.className = 'pages-block';
    wrap.dataset.blockId = String(block.id);

    const toolbar = document.createElement('div');
    toolbar.className = 'pages-block-toolbar';

    const headingSelect = document.createElement('select');
    headingSelect.className = 'pages-tb-input pages-tb-heading';
    headingSelect.title = 'Format du paragraphe';
    [
      ['0', 'Normal'],
      ['1', 'Titre 1'],
      ['2', 'Titre 2'],
      ['3', 'Titre 3'],
      ['4', 'Titre 4'],
    ].forEach(([value, label]) => {
      const opt = document.createElement('option');
      opt.value = value;
      opt.textContent = label;
      headingSelect.appendChild(opt);
    });

    const fontSize = document.createElement('input');
    fontSize.type = 'number';
    fontSize.min = '8';
    fontSize.max = '96';
    fontSize.className = 'pages-tb-input';
    fontSize.placeholder = String(DEFAULT_FONT_SIZE);
    fontSize.title = 'Taille du texte sélectionné';

    const makeToggle = (label, title, extraClass) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `pages-tb-toggle ${extraClass || ''}`.trim();
      btn.textContent = label;
      btn.title = title;
      return btn;
    };
    const boldBtn = makeToggle('G', 'Gras');
    const italicBtn = makeToggle('I', 'Italique', 'pages-tb-italic');
    const underlineBtn = makeToggle('S', 'Souligné', 'pages-tb-underline');
    const strikeBtn = makeToggle('B', 'Barré', 'pages-tb-strikethrough');

    const frameBtn = makeToggle('▭', 'Encadrer le bloc');
    frameBtn.classList.toggle('active', !!block.data.framed);

    const upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.className = 'pages-tb-action';
    upBtn.textContent = '↑';
    upBtn.title = 'Monter';
    const downBtn = document.createElement('button');
    downBtn.type = 'button';
    downBtn.className = 'pages-tb-action';
    downBtn.textContent = '↓';
    downBtn.title = 'Descendre';
    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'pages-tb-action';
    delBtn.textContent = '🗑';
    delBtn.title = 'Supprimer';

    toolbar.append(headingSelect, fontSize, boldBtn, italicBtn, underlineBtn, strikeBtn, frameBtn, upBtn, downBtn, delBtn);
    wrap.appendChild(toolbar);

    const contentEl = document.createElement('div');
    contentEl.className = 'pages-block-content';
    contentEl.contentEditable = 'true';
    renderTextParagraphs(contentEl, block.data);
    wrap.appendChild(contentEl);
    wrap.pagesContentEl = contentEl;
    wrap.pagesFrameBtn = frameBtn;
    wrap.classList.toggle('pages-block-framed', !!block.data.framed);

    let savedSelection = null;
    const saveSelection = () => {
      const sel = window.getSelection();
      if (sel && sel.rangeCount > 0 && contentEl.contains(sel.anchorNode)) {
        savedSelection = sel.getRangeAt(0).cloneRange();
      }
    };
    contentEl.addEventListener('keyup', saveSelection);
    contentEl.addEventListener('mouseup', saveSelection);
    contentEl.addEventListener('focus', () => {
      editingBlockId = block.id;
      wrap.classList.add('editing');
    });
    contentEl.addEventListener('blur', () => {
      wrap.classList.remove('editing');
      if (editingBlockId === block.id) editingBlockId = null;
      const current = blocks.get(block.id);
      if (current) commitTextBlock(current, contentEl);
    });

    // bold/italic/underline/strikethrough act on the current text
    // selection, so their mousedown must preventDefault (not just
    // stopPropagation) or clicking the button would itself blur contentEl
    // first and clear the selection — same fix as whiteboard.js's toolbar
    [boldBtn, italicBtn, underlineBtn, strikeBtn].forEach((btn) =>
      btn.addEventListener('mousedown', (e) => {
        e.preventDefault();
        e.stopPropagation();
      })
    );
    boldBtn.addEventListener('click', () => {
      document.execCommand('bold');
      commitTextBlock(block, contentEl);
    });
    italicBtn.addEventListener('click', () => {
      document.execCommand('italic');
      commitTextBlock(block, contentEl);
    });
    underlineBtn.addEventListener('click', () => {
      document.execCommand('underline');
      commitTextBlock(block, contentEl);
    });
    strikeBtn.addEventListener('click', () => {
      document.execCommand('strikeThrough');
      commitTextBlock(block, contentEl);
    });

    // the font-size number input legitimately needs its own focus to be
    // typed into, so (unlike the toggles above) it does blur contentEl —
    // restore the saved selection into it before applying, same pattern as
    // whiteboard.js's tbFontSize handler
    fontSize.addEventListener('mousedown', (e) => e.stopPropagation());
    fontSize.addEventListener('change', () => {
      if (!savedSelection) return;
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(savedSelection);
      contentEl.focus({ preventScroll: true });
      applyFontSizeToSelection(contentEl, clamp(parseInt(fontSize.value, 10) || DEFAULT_FONT_SIZE, 8, 96));
      savedSelection = sel.rangeCount > 0 ? sel.getRangeAt(0).cloneRange() : null;
      commitTextBlock(block, contentEl);
    });

    // formatBlock relies on the live selection landing exactly inside the
    // target line, and restoring a selection into a just-refocused
    // contenteditable is exactly the kind of timing this app has already
    // hit flaky-selection bugs with — so instead of execCommand, find the
    // line element ourselves (from the saved selection, falling back to
    // the block's first line if nothing was ever selected yet) and swap
    // its tag directly. Pure DOM manipulation, no execCommand involved.
    function findLineElement(node) {
      let el = node && (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement);
      while (el && el !== contentEl && !LINE_TAGS.has(el.tagName)) el = el.parentElement;
      // contentEl itself is a <div> (one of LINE_TAGS) — without this
      // explicit check, a selection landing directly on contentEl (e.g. a
      // click in its padding rather than precisely on a line's text) would
      // match the loop's own tag check and get returned as if it were a
      // line, and callers would then detach contentEl itself from the DOM
      if (!el || el === contentEl) return null;
      return LINE_TAGS.has(el.tagName) ? el : null;
    }

    function applyHeadingToLine(lineEl, level) {
      const newEl = document.createElement(level > 0 ? `h${level}` : 'div');
      while (lineEl.firstChild) newEl.appendChild(lineEl.firstChild);
      lineEl.replaceWith(newEl);

      contentEl.focus({ preventScroll: true });
      const range = document.createRange();
      range.selectNodeContents(newEl);
      range.collapse(false);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      savedSelection = range.cloneRange();
      commitTextBlock(block, contentEl);
    }

    headingSelect.addEventListener('mousedown', (e) => e.stopPropagation());
    headingSelect.addEventListener('change', () => {
      const level = parseInt(headingSelect.value, 10);
      const lineEl =
        (savedSelection && findLineElement(savedSelection.startContainer)) ||
        contentEl.querySelector('div, p, li, h1, h2, h3, h4');
      if (!lineEl) return;
      applyHeadingToLine(lineEl, level);
    });

    // framing is a block-level toggle, not a text-selection formatting
    // action, so (like bold/italic/etc.) it must not steal contentEl's focus
    frameBtn.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
    });
    frameBtn.addEventListener('click', () => {
      const current = blocks.get(block.id);
      const framed = !(current && current.data.framed);
      wrap.classList.toggle('pages-block-framed', framed);
      frameBtn.classList.toggle('active', framed);
      commitTextBlock(block, contentEl, { framed });
    });

    upBtn.addEventListener('click', () => moveBlock(block, -1));
    downBtn.addEventListener('click', () => moveBlock(block, 1));
    delBtn.addEventListener('click', () => deleteBlock(block));

    return wrap;
  }

  function appendMoveDeleteButtons(toolbar, block) {
    const upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.className = 'pages-tb-action';
    upBtn.textContent = '↑';
    upBtn.title = 'Monter';
    const downBtn = document.createElement('button');
    downBtn.type = 'button';
    downBtn.className = 'pages-tb-action';
    downBtn.textContent = '↓';
    downBtn.title = 'Descendre';
    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'pages-tb-action';
    delBtn.textContent = '🗑';
    delBtn.title = 'Supprimer';
    upBtn.addEventListener('click', () => moveBlock(block, -1));
    downBtn.addEventListener('click', () => moveBlock(block, 1));
    delBtn.addEventListener('click', () => deleteBlock(block));
    toolbar.append(upBtn, downBtn, delBtn);
  }

  // ---- table block ("grille") — ported and trimmed from whiteboard.js's
  // table element the same way the text block ported its rich-text editor ----

  function blankTableCell() {
    return { text: '', bold: false, italic: false };
  }

  function getCellText(td) {
    return td.innerText.replace(/\n$/, '');
  }

  function setCellText(td, text) {
    td.textContent = '';
    const lines = (text || '').split('\n');
    lines.forEach((line, i) => {
      if (i > 0) td.appendChild(document.createElement('br'));
      if (line) td.appendChild(document.createTextNode(line));
    });
  }

  function renderTableCells(table, rows, fontSize) {
    const size = (fontSize || 14) + 'px';
    table.innerHTML = '';
    (rows || []).forEach((row, r) => {
      const tr = document.createElement('tr');
      row.forEach((cell, c) => {
        const td = document.createElement('td');
        td.contentEditable = 'true';
        td.dataset.row = String(r);
        td.dataset.col = String(c);
        setCellText(td, cell.text);
        td.style.fontSize = size;
        td.style.fontWeight = cell.bold ? '700' : '400';
        td.style.fontStyle = cell.italic ? 'italic' : 'normal';
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
  }

  function createTableBlockEl(block) {
    const wrap = document.createElement('div');
    wrap.className = 'pages-block';
    wrap.dataset.blockId = String(block.id);

    const toolbar = document.createElement('div');
    toolbar.className = 'pages-block-toolbar';
    let focusedCell = null;

    function mutateRows(mutator) {
      const current = blocks.get(block.id);
      if (!current) return;
      const rows = current.data.rows.map((row) => row.map((cell) => ({ ...cell })));
      if (mutator(rows) === false) return;
      sendOp({ op: 'update_block', id: block.id, block: { type: 'table', data: { rows, font_size: current.data.font_size } } });
    }

    const addToolbarAction = (label, title, onClick) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pages-tb-action';
      btn.textContent = label;
      btn.title = title;
      btn.addEventListener('click', onClick);
      toolbar.appendChild(btn);
    };
    addToolbarAction('+Ligne', 'Ajouter une ligne', () =>
      mutateRows((rows) => {
        if (rows.length >= MAX_TABLE_ROWS) return false;
        const insertAt = focusedCell ? focusedCell.row + 1 : rows.length;
        rows.splice(insertAt, 0, Array.from({ length: rows[0] ? rows[0].length : 2 }, blankTableCell));
      })
    );
    addToolbarAction('+Col', 'Ajouter une colonne', () =>
      mutateRows((rows) => {
        if (rows[0] && rows[0].length >= MAX_TABLE_COLS) return false;
        const insertAt = focusedCell ? focusedCell.col + 1 : rows[0] ? rows[0].length : 0;
        rows.forEach((row) => row.splice(insertAt, 0, blankTableCell()));
      })
    );
    addToolbarAction('-Ligne', 'Supprimer la ligne', () =>
      mutateRows((rows) => {
        if (rows.length <= 1) return false;
        const removeAt = focusedCell ? Math.min(focusedCell.row, rows.length - 1) : rows.length - 1;
        rows.splice(removeAt, 1);
      })
    );
    addToolbarAction('-Col', 'Supprimer la colonne', () =>
      mutateRows((rows) => {
        if (!rows[0] || rows[0].length <= 1) return false;
        const removeAt = focusedCell ? Math.min(focusedCell.col, rows[0].length - 1) : rows[0].length - 1;
        rows.forEach((row) => row.splice(removeAt, 1));
      })
    );
    appendMoveDeleteButtons(toolbar, block);
    wrap.appendChild(toolbar);

    const table = document.createElement('table');
    table.className = 'pages-table';
    renderTableCells(table, block.data.rows, block.data.font_size);
    wrap.appendChild(table);
    wrap.pagesTableEl = table;

    table.addEventListener('focusin', (e) => {
      const td = e.target.closest('td');
      if (!td) return;
      editingBlockId = block.id;
      wrap.classList.add('editing');
      focusedCell = { row: Number(td.dataset.row), col: Number(td.dataset.col) };
    });
    table.addEventListener('focusout', (e) => {
      const td = e.target.closest('td');
      if (!td) return;
      wrap.classList.remove('editing');
      if (editingBlockId === block.id) editingBlockId = null;
      const current = blocks.get(block.id);
      if (!current) return;
      const r = Number(td.dataset.row);
      const c = Number(td.dataset.col);
      const newText = getCellText(td);
      const rows = current.data.rows.map((row) => row.map((cell) => ({ ...cell })));
      if (rows[r] && rows[r][c] && rows[r][c].text !== newText) {
        rows[r][c].text = newText;
        sendOp({ op: 'update_block', id: block.id, block: { type: 'table', data: { rows, font_size: current.data.font_size } } });
      }
    });
    table.addEventListener('keydown', (e) => {
      if (!e.target.closest('td')) return;
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        document.execCommand('insertLineBreak');
      }
    });

    return wrap;
  }

  // ---- image block ----

  function createImageBlockEl(block) {
    const wrap = document.createElement('div');
    wrap.className = 'pages-block';
    wrap.dataset.blockId = String(block.id);

    const toolbar = document.createElement('div');
    toolbar.className = 'pages-block-toolbar';
    appendMoveDeleteButtons(toolbar, block);
    wrap.appendChild(toolbar);

    const imgWrap = document.createElement('div');
    imgWrap.className = 'pages-image-wrap';

    const img = document.createElement('img');
    img.className = 'pages-block-image';
    img.src = block.data.src;
    img.alt = block.data.caption || '';
    if (block.data.width) img.style.width = block.data.width + 'px';
    imgWrap.appendChild(img);

    const resizeHandle = document.createElement('div');
    resizeHandle.className = 'pages-image-resize-handle';
    resizeHandle.title = 'Glisser pour redimensionner';
    resizeHandle.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      imageResizeState = { img, blockId: block.id, startX: e.clientX, startWidth: img.getBoundingClientRect().width };
    });
    imgWrap.appendChild(resizeHandle);

    wrap.appendChild(imgWrap);
    wrap.pagesImgEl = img;

    const caption = document.createElement('input');
    caption.type = 'text';
    caption.className = 'pages-image-caption';
    caption.placeholder = 'Légende (optionnelle)';
    caption.maxLength = 300;
    caption.value = block.data.caption || '';
    caption.addEventListener('focus', () => {
      editingBlockId = block.id;
      wrap.classList.add('editing');
    });
    caption.addEventListener('blur', () => {
      wrap.classList.remove('editing');
      if (editingBlockId === block.id) editingBlockId = null;
      const current = blocks.get(block.id);
      if (!current) return;
      sendOp({
        op: 'update_block',
        id: block.id,
        block: { type: 'image', data: { src: current.data.src, caption: caption.value, width: current.data.width || null } },
      });
    });
    wrap.appendChild(caption);
    wrap.pagesCaptionEl = caption;

    return wrap;
  }

  // ---- code block ----

  function createCodeBlockEl(block) {
    const wrap = document.createElement('div');
    wrap.className = 'pages-block';
    wrap.dataset.blockId = String(block.id);

    const toolbar = document.createElement('div');
    toolbar.className = 'pages-block-toolbar';
    const langSelect = document.createElement('select');
    langSelect.className = 'pages-tb-input';
    CODE_LANGUAGES.forEach((lang) => {
      const opt = document.createElement('option');
      opt.value = lang;
      opt.textContent = lang;
      langSelect.appendChild(opt);
    });
    langSelect.value = block.data.language || 'text';
    toolbar.appendChild(langSelect);
    appendMoveDeleteButtons(toolbar, block);
    wrap.appendChild(toolbar);

    const textarea = document.createElement('textarea');
    textarea.className = 'pages-code-textarea';
    textarea.maxLength = 20000;
    textarea.spellcheck = false;
    textarea.value = block.data.code || '';
    wrap.appendChild(textarea);
    wrap.pagesCodeEl = textarea;
    wrap.pagesLangEl = langSelect;

    function commit() {
      sendOp({ op: 'update_block', id: block.id, block: { type: 'code', data: { code: textarea.value, language: langSelect.value } } });
    }
    textarea.addEventListener('focus', () => {
      editingBlockId = block.id;
      wrap.classList.add('editing');
    });
    textarea.addEventListener('blur', () => {
      wrap.classList.remove('editing');
      if (editingBlockId === block.id) editingBlockId = null;
      commit();
    });
    langSelect.addEventListener('change', commit);

    return wrap;
  }

  // ---- link block — url/title/description are always typed by hand,
  // never fetched server-side (see docs/specs.md decision) ----

  // one shared "which link block's edit popup is open" state, so opening
  // one closes any other and a click anywhere else closes it too — same
  // single-shared-listener reasoning as imageResizeState above
  let openLinkPopup = null;

  document.addEventListener('mousedown', (e) => {
    if (!openLinkPopup) return;
    if (openLinkPopup.contains(e.target) || openLinkPopup.pagesEditBtn.contains(e.target)) return;
    openLinkPopup.style.display = 'none';
    openLinkPopup = null;
  });

  function createLinkBlockEl(block) {
    const wrap = document.createElement('div');
    wrap.className = 'pages-block';
    wrap.dataset.blockId = String(block.id);

    const toolbar = document.createElement('div');
    toolbar.className = 'pages-block-toolbar';
    appendMoveDeleteButtons(toolbar, block);
    wrap.appendChild(toolbar);

    // collapsed view: just the url (never the title — the user only wants
    // to see where it points to at a glance) and an icon that reveals the
    // editable fields in a popup
    const row = document.createElement('div');
    row.className = 'pages-link-row';

    const preview = document.createElement('a');
    preview.className = 'pages-link-preview';
    preview.target = '_blank';
    preview.rel = 'noopener noreferrer';

    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'pages-link-edit-btn';
    editBtn.title = 'Modifier le lien';
    editBtn.textContent = '✎';

    row.append(preview, editBtn);
    wrap.appendChild(row);

    const popup = document.createElement('div');
    popup.className = 'pages-link-popup';
    popup.style.display = 'none';
    popup.pagesEditBtn = editBtn;

    const urlInput = document.createElement('input');
    urlInput.type = 'text';
    urlInput.className = 'pages-link-input';
    urlInput.placeholder = 'https://...';
    urlInput.maxLength = 2000;
    urlInput.value = block.data.url || '';

    const titleInput = document.createElement('input');
    titleInput.type = 'text';
    titleInput.className = 'pages-link-input';
    titleInput.placeholder = 'Titre';
    titleInput.maxLength = 300;
    titleInput.value = block.data.title || '';

    const descInput = document.createElement('input');
    descInput.type = 'text';
    descInput.className = 'pages-link-input';
    descInput.placeholder = 'Description';
    descInput.maxLength = 500;
    descInput.value = block.data.description || '';

    popup.append(urlInput, titleInput, descInput);
    wrap.appendChild(popup);

    function updatePreview(data) {
      if (data.url) {
        preview.href = data.url;
        preview.textContent = data.url;
        preview.classList.remove('pages-link-empty');
      } else {
        preview.removeAttribute('href');
        preview.textContent = 'Lien non défini';
        preview.classList.add('pages-link-empty');
      }
    }
    updatePreview(block.data);

    function commit() {
      const data = { url: urlInput.value, title: titleInput.value, description: descInput.value };
      sendOp({ op: 'update_block', id: block.id, block: { type: 'link', data } });
      updatePreview(data);
    }

    editBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (openLinkPopup && openLinkPopup !== popup) openLinkPopup.style.display = 'none';
      const willOpen = popup.style.display === 'none';
      popup.style.display = willOpen ? 'flex' : 'none';
      openLinkPopup = willOpen ? popup : null;
      if (willOpen) urlInput.focus();
    });

    [urlInput, titleInput, descInput].forEach((input) => {
      input.addEventListener('focus', () => {
        editingBlockId = block.id;
        wrap.classList.add('editing');
      });
      input.addEventListener('blur', () => {
        wrap.classList.remove('editing');
        if (editingBlockId === block.id) editingBlockId = null;
        commit();
      });
    });

    wrap.pagesLinkUrlEl = urlInput;
    wrap.pagesLinkTitleEl = titleInput;
    wrap.pagesLinkDescEl = descInput;
    wrap.pagesLinkPreviewEl = preview;

    return wrap;
  }

  function createBlockEl(block) {
    let wrap;
    if (block.type === 'text') {
      wrap = createTextBlockEl(block);
    } else if (block.type === 'image') {
      wrap = createImageBlockEl(block);
    } else if (block.type === 'table') {
      wrap = createTableBlockEl(block);
    } else if (block.type === 'code') {
      wrap = createCodeBlockEl(block);
    } else if (block.type === 'link') {
      wrap = createLinkBlockEl(block);
    } else {
      wrap = document.createElement('div');
      wrap.className = 'pages-block';
      wrap.textContent = `Type de bloc non pris en charge : ${block.type}`;
    }

    // left-side drag handle, added here rather than in each create*BlockEl
    // so every block type gets it uniformly; it's position:absolute (see
    // CSS) so it never conflicts with the block's own internal layout
    const handle = document.createElement('div');
    handle.className = 'pages-block-handle';
    handle.title = 'Glisser pour déplacer';
    handle.textContent = '⠿';
    handle.addEventListener('mousedown', (e) => startBlockDragCandidate(e, block.id));
    wrap.prepend(handle);

    blockRenderRefs.set(block.id, wrap);
    return wrap;
  }

  // ---- drag-and-drop reordering of blocks within the same page — same
  // candidate/threshold/placeholder pattern as kanban.js's card drag ----
  const BLOCK_DRAG_THRESHOLD = 4;
  let blockDragCandidate = null;
  let blockDragState = null;

  function startBlockDragCandidate(e, blockId) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    blockDragCandidate = { blockId, startX: e.clientX, startY: e.clientY };
  }

  function promoteBlockDrag(e) {
    const { blockId } = blockDragCandidate;
    const el = blockRenderRefs.get(blockId);
    blockDragCandidate = null;
    if (!el || !blocksWrapEl) return;
    const rect = el.getBoundingClientRect();

    const placeholder = document.createElement('div');
    placeholder.className = 'pages-block-placeholder';
    placeholder.style.height = rect.height + 'px';
    el.parentElement.insertBefore(placeholder, el);

    el.classList.add('dragging');
    el.style.position = 'fixed';
    el.style.width = rect.width + 'px';
    el.style.left = rect.left + 'px';
    el.style.top = rect.top + 'px';
    document.body.appendChild(el);

    blockDragState = { blockId, el, placeholder, offsetX: e.clientX - rect.left, offsetY: e.clientY - rect.top };
  }

  function updateBlockDrag(e) {
    const { el, offsetX, offsetY, placeholder } = blockDragState;
    el.style.left = e.clientX - offsetX + 'px';
    el.style.top = e.clientY - offsetY + 'px';
    if (!blocksWrapEl) return;

    const siblings = Array.from(blocksWrapEl.children).filter((c) => c !== placeholder && c.classList.contains('pages-block'));
    let inserted = false;
    for (const sib of siblings) {
      const sibRect = sib.getBoundingClientRect();
      if (e.clientY < sibRect.top + sibRect.height / 2) {
        blocksWrapEl.insertBefore(placeholder, sib);
        inserted = true;
        break;
      }
    }
    if (!inserted) blocksWrapEl.appendChild(placeholder);
  }

  function finishBlockDrag() {
    const { blockId, el, placeholder } = blockDragState;
    el.style.position = '';
    el.style.left = '';
    el.style.top = '';
    el.style.width = '';
    el.classList.remove('dragging');
    if (blocksWrapEl && placeholder.parentElement === blocksWrapEl) {
      const index = Array.from(blocksWrapEl.children).indexOf(placeholder);
      blocksWrapEl.insertBefore(el, placeholder);
      placeholder.remove();
      blockDragState = null;
      sendOp({ op: 'move_block', id: blockId, index });
    } else {
      // the page changed mid-drag (or the block's page got deleted) —
      // just drop it back where it was rather than send a stale move
      placeholder.replaceWith(el);
      blockDragState = null;
    }
  }

  document.addEventListener('mousemove', (e) => {
    if (blockDragCandidate && !blockDragState) {
      const dx = e.clientX - blockDragCandidate.startX;
      const dy = e.clientY - blockDragCandidate.startY;
      if (Math.abs(dx) > BLOCK_DRAG_THRESHOLD || Math.abs(dy) > BLOCK_DRAG_THRESHOLD) promoteBlockDrag(e);
    }
    if (blockDragState) updateBlockDrag(e);
  });
  document.addEventListener('mouseup', () => {
    if (blockDragState) finishBlockDrag();
    blockDragCandidate = null;
  });

  function renderContent() {
    contentEl.textContent = '';
    blockRenderRefs.clear();
    blocksWrapEl = null;
    blocksEmptyEl = null;
    titleInputEl = null;
    openLinkPopup = null;
    const page = pages.get(selectedPageId);
    if (!page) {
      const p = document.createElement('p');
      p.className = 'pages-empty';
      p.textContent = "Sélectionnez une page dans l'arbre, ou créez-en une.";
      contentEl.appendChild(p);
      return;
    }

    const titleInput = document.createElement('input');
    titleInput.type = 'text';
    titleInput.className = 'pages-title-input';
    titleInput.value = page.title;
    titleInput.maxLength = 200;
    titleInput.addEventListener('change', () => {
      sendOp({ op: 'rename_page', id: page.id, title: titleInput.value });
    });
    contentEl.appendChild(titleInput);
    titleInputEl = titleInput;

    const blocksWrap = document.createElement('div');
    blocksWrap.className = 'pages-blocks';
    blocksWrapEl = blocksWrap;
    const pageBlocks = blocksOfPage(page.id);
    if (pageBlocks.length === 0) {
      const placeholder = document.createElement('p');
      placeholder.className = 'pages-empty';
      placeholder.textContent = 'Aucun bloc pour le moment.';
      blocksWrap.appendChild(placeholder);
      blocksEmptyEl = placeholder;
    } else {
      pageBlocks.forEach((b) => blocksWrap.appendChild(createBlockEl(b)));
    }
    contentEl.appendChild(blocksWrap);

    const addButtons = document.createElement('div');
    addButtons.className = 'pages-add-block-row';
    const addBlockBtn = (label, onClick) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pages-add-block-btn';
      btn.textContent = label;
      btn.addEventListener('click', onClick);
      addButtons.appendChild(btn);
    };
    addBlockBtn('+ Texte', () => createTextBlock(page.id));
    addBlockBtn('+ Image', () => promptImageBlock(page.id));
    addBlockBtn('+ Grille', () => createTableBlock(page.id));
    addBlockBtn('+ Lien', () => createLinkBlock(page.id));
    addBlockBtn('+ Code', () => createCodeBlock(page.id));
    contentEl.appendChild(addButtons);
  }

  // ---- surgical DOM patches for remote block changes — see the
  // blockRenderRefs comment above for why these exist instead of just
  // calling renderContent() on every ws message ----

  function addBlockToDom(block) {
    if (block.page_id !== selectedPageId || !blocksWrapEl || blockRenderRefs.has(block.id)) return;
    if (blocksEmptyEl) {
      blocksEmptyEl.remove();
      blocksEmptyEl = null;
    }
    const el = createBlockEl(block);
    const siblings = blocksOfPage(block.page_id); // already includes this block, sorted
    const idx = siblings.findIndex((b) => b.id === block.id);
    const nextSibling = siblings[idx + 1];
    const nextEl = nextSibling ? blockRenderRefs.get(nextSibling.id) : null;
    if (nextEl) blocksWrapEl.insertBefore(el, nextEl);
    else blocksWrapEl.appendChild(el);
  }

  function updateBlockContentDom(block) {
    if (block.page_id !== selectedPageId) return;
    const wrap = blockRenderRefs.get(block.id);
    if (!wrap) return;
    if (block.type === 'text' && wrap.pagesContentEl) {
      renderTextParagraphs(wrap.pagesContentEl, block.data);
      wrap.classList.toggle('pages-block-framed', !!block.data.framed);
      if (wrap.pagesFrameBtn) wrap.pagesFrameBtn.classList.toggle('active', !!block.data.framed);
    } else if (block.type === 'table' && wrap.pagesTableEl) {
      renderTableCells(wrap.pagesTableEl, block.data.rows, block.data.font_size);
    } else if (block.type === 'image' && wrap.pagesImgEl) {
      wrap.pagesImgEl.src = block.data.src;
      if (wrap.pagesCaptionEl) wrap.pagesCaptionEl.value = block.data.caption || '';
      // don't fight a resize drag in progress on this exact image
      if (!imageResizeState || imageResizeState.img !== wrap.pagesImgEl) {
        wrap.pagesImgEl.style.width = block.data.width ? block.data.width + 'px' : '';
      }
    } else if (block.type === 'code' && wrap.pagesCodeEl) {
      wrap.pagesCodeEl.value = block.data.code || '';
      if (wrap.pagesLangEl) wrap.pagesLangEl.value = block.data.language || 'text';
    } else if (block.type === 'link' && wrap.pagesLinkUrlEl) {
      wrap.pagesLinkUrlEl.value = block.data.url || '';
      wrap.pagesLinkTitleEl.value = block.data.title || '';
      wrap.pagesLinkDescEl.value = block.data.description || '';
      if (block.data.url) {
        wrap.pagesLinkPreviewEl.href = block.data.url;
        wrap.pagesLinkPreviewEl.textContent = block.data.url;
        wrap.pagesLinkPreviewEl.classList.remove('pages-link-empty');
      } else {
        wrap.pagesLinkPreviewEl.removeAttribute('href');
        wrap.pagesLinkPreviewEl.textContent = 'Lien non défini';
        wrap.pagesLinkPreviewEl.classList.add('pages-link-empty');
      }
    }
  }

  function reorderBlocksDom(pageId, order) {
    if (pageId !== selectedPageId || !blocksWrapEl) return;
    order.forEach((id) => {
      const el = blockRenderRefs.get(id);
      if (el) blocksWrapEl.appendChild(el); // appendChild on an attached node just moves it
    });
  }

  function removeBlockFromDom(id, pageId, order) {
    const wrap = blockRenderRefs.get(id);
    if (wrap) {
      wrap.remove();
      if (openLinkPopup && wrap.contains(openLinkPopup)) openLinkPopup = null;
    }
    blockRenderRefs.delete(id);
    if (pageId !== selectedPageId || !blocksWrapEl) return;
    reorderBlocksDom(pageId, order);
    if (blocksWrapEl.children.length === 0 && !blocksEmptyEl) {
      const placeholder = document.createElement('p');
      placeholder.className = 'pages-empty';
      placeholder.textContent = 'Aucun bloc pour le moment.';
      blocksWrapEl.appendChild(placeholder);
      blocksEmptyEl = placeholder;
    }
  }

  function connectWs() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/pages/${boardId}`);

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
      if (msg.op === 'page_created') {
        pages.set(msg.page.id, msg.page);
        renderTree();
        if (selectedPageId === null) selectPage(msg.page.id);
      } else if (msg.op === 'page_renamed') {
        const p = pages.get(msg.id);
        if (p) {
          p.title = msg.title;
          renderTree();
          // patch the title input in place rather than a full renderContent()
          // — that would also tear down and rebuild every block on the
          // page, discarding anyone else's in-progress edit on one of them
          if (selectedPageId === msg.id && titleInputEl && document.activeElement !== titleInputEl) {
            titleInputEl.value = msg.title;
          }
        }
      } else if (msg.op === 'page_moved') {
        const p = pages.get(msg.id);
        if (p) p.parent_id = msg.parent_id;
        (msg.affected || []).forEach((group) => {
          group.order.forEach((id, i) => {
            const pg = pages.get(id);
            if (pg) pg.order_index = i;
          });
        });
        renderTree();
      } else if (msg.op === 'page_deleted') {
        // deleting a page cascades to its whole subtree server-side —
        // deleted_ids covers every page that went with it, not just the
        // one the user clicked delete on
        const deletedIds = new Set(msg.deleted_ids || [msg.id]);
        deletedIds.forEach((id) => {
          pages.delete(id);
          Array.from(blocks.values()).forEach((b) => {
            if (b.page_id === id) blocks.delete(b.id);
          });
        });
        (msg.order || []).forEach((id, i) => {
          const pg = pages.get(id);
          if (pg) pg.order_index = i;
        });
        if (deletedIds.has(selectedPageId)) selectPage(null);
        else renderTree();
      } else if (msg.op === 'subtree_restored') {
        (msg.pages || []).forEach((p) => pages.set(p.id, p));
        (msg.blocks || []).forEach((b) => blocks.set(b.id, b));
        renderTree();
      } else if (msg.op === 'block_created') {
        blocks.set(msg.block.id, msg.block);
        addBlockToDom(msg.block);
      } else if (msg.op === 'block_updated') {
        blocks.set(msg.block.id, msg.block);
        // the editor that just blurred to produce this already shows the
        // right content locally — only patch everyone else's view of it
        if (editingBlockId !== msg.block.id) updateBlockContentDom(msg.block);
      } else if (msg.op === 'block_moved') {
        (msg.order || []).forEach((id, i) => {
          const b = blocks.get(id);
          if (b) b.order_index = i;
        });
        reorderBlocksDom(msg.page_id, msg.order || []);
      } else if (msg.op === 'block_deleted') {
        blocks.delete(msg.id);
        (msg.order || []).forEach((id, i) => {
          const b = blocks.get(id);
          if (b) b.order_index = i;
        });
        removeBlockFromDom(msg.id, msg.page_id, msg.order || []);
      } else if (msg.op === 'error') {
        showTransientStatus(msg.detail, true);
      }
    });
  }

  async function load() {
    const res = await fetch(`/api/pages/${boardId}`);
    const data = await res.json();
    data.pages.forEach((p) => pages.set(p.id, p));
    data.blocks.forEach((b) => blocks.set(b.id, b));
    renderTree();
    renderContent();
    connectWs();
  }

  addRootBtn.addEventListener('click', () => createPage(null));

  nameInput.addEventListener('change', () => {
    fetch(`/api/pages/${boardId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: nameInput.value }),
    });
  });

  if (window.initHistoryPanel) {
    window.initHistoryPanel({
      buttonId: 'history-btn',
      apiUrl: `/api/pages/${boardId}/history`,
      onRestore: (entry) => sendOp({ op: 'restore', log_id: entry.id }),
    });
  }

  load();
})();
