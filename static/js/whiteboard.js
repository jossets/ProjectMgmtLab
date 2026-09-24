(function () {
  const root = document.querySelector('.whiteboard-page');
  const boardId = root.dataset.whiteboardId;
  const viewport = document.getElementById('wb-viewport');
  const canvas = document.getElementById('wb-canvas');
  const nameInput = document.getElementById('whiteboard-name');
  const statusEl = document.getElementById('wb-status');
  const addTextBtn = document.getElementById('wb-add-text-btn');
  const addImageBtn = document.getElementById('wb-add-image-btn');
  const imageFileInput = document.getElementById('wb-image-file-input');
  const addTableBtn = document.getElementById('wb-add-table-btn');
  const zoomFitBtn = document.getElementById('wb-zoom-fit-btn');
  const pencilBtn = document.getElementById('wb-tool-pencil-btn');
  const lineBtn = document.getElementById('wb-tool-line-btn');
  const pencilColorInput = document.getElementById('wb-pencil-color');
  const pencilWidthInput = document.getElementById('wb-pencil-width');

  const SVG_NS = 'http://www.w3.org/2000/svg';
  const ZOOM_MIN = 0.2;
  const ZOOM_MAX = 3;
  const DRAG_THRESHOLD = 4;
  const MAX_LINE_POINTS = 2000;
  const MAX_IMAGE_DIM = 400;
  const MAX_TABLE_ROWS = 50;
  const MAX_TABLE_COLS = 20;
  const DEFAULT_FONT_SIZE = 16;
  const MAX_TEXT_PARAGRAPHS = 200;
  const MAX_TEXT_RUNS_PER_PARAGRAPH = 100;

  // anonymous per-browser id used to cap reaction votes at one per icon per
  // voter (no accounts yet — this is a pragmatic stand-in, not real
  // identity: clearing localStorage or using another browser resets it)
  function getVoterId() {
    const KEY = 'projectmgr_voter_id';
    let id = localStorage.getItem(KEY);
    if (id && /^[0-9a-f]{32}$/.test(id)) return id;
    id = window.crypto && crypto.randomUUID
      ? crypto.randomUUID().replace(/-/g, '')
      : Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
    localStorage.setItem(KEY, id);
    return id;
  }
  const VOTER_ID = getVoterId();

  function blankRun() {
    return {
      text: '',
      bold: false,
      italic: false,
      underline: false,
      strikethrough: false,
      font_size: DEFAULT_FONT_SIZE,
    };
  }
  function blankParagraph() {
    return { bullet: false, runs: [blankRun()] };
  }

  const TEXT_DEFAULTS = {
    paragraphs: [blankParagraph()],
    color: '#1f2430',
    bg_color: '#ffffff',
    border_color: '#adb5bd',
    reactions: {},
  };

  const REACTION_ICONS = { heart: '❤️', thumbsup: '👍', thumbsdown: '👎' };
  const REACTION_LABELS = { heart: 'Coeur', thumbsup: 'Pouce haut', thumbsdown: 'Pouce bas' };
  const REACTION_ORDER = ['heart', 'thumbsup', 'thumbsdown'];

  let zoom = 1;
  let panX = 40;
  let panY = 40;
  const elements = new Map(); // id -> { div, content, state }
  let ws = null;
  let panning = null;
  let dragCandidate = null;
  let dragState = null;
  let resizeState = null;
  let selectedIds = new Set();
  let selectedId = null; // convenience alias: set only when exactly one element is selected
  let editingId = null;
  let pendingFocusRef = null;
  let pendingFocusEdit = false;
  let clipboard = []; // copied element states, for Ctrl+C / Ctrl+V
  let pasteCount = 0; // cascades repeated pastes diagonally, like Figma/PowerPoint
  let pendingPasteRefs = null; // Set of client_refs from the paste currently in flight
  let pendingPasteIds = null; // ids resolved so far for that paste
  let activeTool = 'select'; // 'select' | 'pencil' | 'line'
  let drawState = null;
  let marqueeState = null;
  let lineToolState = null; // click-click straight-line placement (distinct from drawState's held-drag polyline)
  let endpointDragState = null; // dragging one end of any existing line

  // scratch layer used only for the live preview of a stroke being drawn;
  // finished lines get their own positioned <svg> wrapper (see makeLineElementDiv)
  // so they can participate in normal CSS z-index stacking like other blocks.
  const previewSvg = document.createElementNS(SVG_NS, 'svg');
  previewSvg.setAttribute('class', 'wb-preview-svg');
  previewSvg.setAttribute('width', 4000);
  previewSvg.setAttribute('height', 3000);
  canvas.appendChild(previewSvg);

  function clamp(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  function viewportCenterInCanvas() {
    const rect = viewport.getBoundingClientRect();
    return { x: (rect.width / 2 - panX) / zoom, y: (rect.height / 2 - panY) / zoom };
  }

  // ---- minimap bars: where the current view sits within everything drawn ----
  const minimapH = document.createElement('div');
  minimapH.className = 'wb-minimap wb-minimap-h';
  const minimapHThumb = document.createElement('div');
  minimapHThumb.className = 'wb-minimap-thumb';
  minimapH.appendChild(minimapHThumb);

  const minimapV = document.createElement('div');
  minimapV.className = 'wb-minimap wb-minimap-v';
  const minimapVThumb = document.createElement('div');
  minimapVThumb.className = 'wb-minimap-thumb';
  minimapV.appendChild(minimapVThumb);

  viewport.appendChild(minimapH);
  viewport.appendChild(minimapV);

  function applyTransform() {
    canvas.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
    updateMinimap();
  }
  applyTransform();

  // "everything drawn, plus wherever the view currently is" — so panning
  // off into empty space still shows the thumb sliding away from the
  // content instead of the tracked extent just vanishing
  function computeMinimapBounds(rect) {
    const viewMinX = -panX / zoom;
    const viewMinY = -panY / zoom;
    const viewMaxX = viewMinX + rect.width / zoom;
    const viewMaxY = viewMinY + rect.height / zoom;

    let minX = viewMinX;
    let minY = viewMinY;
    let maxX = viewMaxX;
    let maxY = viewMaxY;
    elements.forEach(({ state }) => {
      minX = Math.min(minX, state.x);
      minY = Math.min(minY, state.y);
      maxX = Math.max(maxX, state.x + (state.width || 0));
      maxY = Math.max(maxY, state.y + (state.height || 0));
    });
    const padX = Math.max(20, (maxX - minX) * 0.04);
    const padY = Math.max(20, (maxY - minY) * 0.04);
    minX -= padX;
    maxX += padX;
    minY -= padY;
    maxY += padY;

    return {
      minX,
      minY,
      totalW: Math.max(1, maxX - minX),
      totalH: Math.max(1, maxY - minY),
      viewMinX,
      viewMinY,
      viewMaxX,
      viewMaxY,
    };
  }

  function updateMinimap() {
    const rect = viewport.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const b = computeMinimapBounds(rect);

    const hLeft = clamp(((b.viewMinX - b.minX) / b.totalW) * 100, 0, 100);
    const hWidth = clamp(((b.viewMaxX - b.viewMinX) / b.totalW) * 100, 6, 100 - hLeft);
    minimapHThumb.style.left = hLeft + '%';
    minimapHThumb.style.width = hWidth + '%';

    const vTop = clamp(((b.viewMinY - b.minY) / b.totalH) * 100, 0, 100);
    const vHeight = clamp(((b.viewMaxY - b.viewMinY) / b.totalH) * 100, 6, 100 - vTop);
    minimapVThumb.style.top = vTop + '%';
    minimapVThumb.style.height = vHeight + '%';
  }

  // ---- minimap drag/click-to-scroll — clicking anywhere on a bar centers
  // the view on that point along its axis; dragging keeps following the
  // cursor, same as dragging a scrollbar thumb ----
  let minimapDrag = null; // { axis: 'x' | 'y' }

  function applyMinimapDrag(axis, e) {
    const rect = viewport.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const bounds = computeMinimapBounds(rect);
    const barEl = axis === 'x' ? minimapH : minimapV;
    const barRect = barEl.getBoundingClientRect();
    if (axis === 'x') {
      const frac = barRect.width > 0 ? clamp((e.clientX - barRect.left) / barRect.width, 0, 1) : 0;
      const viewWidth = bounds.viewMaxX - bounds.viewMinX;
      const worldCenter = bounds.minX + frac * bounds.totalW;
      panX = -(worldCenter - viewWidth / 2) * zoom;
    } else {
      const frac = barRect.height > 0 ? clamp((e.clientY - barRect.top) / barRect.height, 0, 1) : 0;
      const viewHeight = bounds.viewMaxY - bounds.viewMinY;
      const worldCenter = bounds.minY + frac * bounds.totalH;
      panY = -(worldCenter - viewHeight / 2) * zoom;
    }
    applyTransform();
  }

  function startMinimapDrag(axis, e) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    minimapDrag = { axis };
    (axis === 'x' ? minimapH : minimapV).classList.add('dragging');
    applyMinimapDrag(axis, e);
  }

  minimapH.addEventListener('mousedown', (e) => startMinimapDrag('x', e));
  minimapV.addEventListener('mousedown', (e) => startMinimapDrag('y', e));

  function fitViewToContent() {
    if (elements.size === 0) return;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    elements.forEach(({ state }) => {
      minX = Math.min(minX, state.x);
      minY = Math.min(minY, state.y);
      maxX = Math.max(maxX, state.x + (state.width || 0));
      maxY = Math.max(maxY, state.y + (state.height || 0));
    });
    const contentW = Math.max(1, maxX - minX);
    const contentH = Math.max(1, maxY - minY);
    const rect = viewport.getBoundingClientRect();
    const margin = 60;
    const availW = Math.max(50, rect.width - margin * 2);
    const availH = Math.max(50, rect.height - margin * 2);
    zoom = clamp(Math.min(availW / contentW, availH / contentH), ZOOM_MIN, ZOOM_MAX);
    panX = (rect.width - contentW * zoom) / 2 - minX * zoom;
    panY = (rect.height - contentH * zoom) / 2 - minY * zoom;
    applyTransform();
  }
  zoomFitBtn.addEventListener('click', fitViewToContent);

  // ---- pan (right-button drag) / marquee select (left-button drag) / pencil tool ----
  const marqueeBox = document.createElement('div');
  marqueeBox.className = 'wb-marquee';
  document.body.appendChild(marqueeBox);

  function canvasRectFromScreen(x1, y1, x2, y2) {
    const rect = viewport.getBoundingClientRect();
    const p1x = (Math.min(x1, x2) - rect.left - panX) / zoom;
    const p1y = (Math.min(y1, y2) - rect.top - panY) / zoom;
    const p2x = (Math.max(x1, x2) - rect.left - panX) / zoom;
    const p2y = (Math.max(y1, y2) - rect.top - panY) / zoom;
    return { x: p1x, y: p1y, width: p2x - p1x, height: p2y - p1y };
  }

  function rectsIntersect(a, b) {
    return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
  }

  function elementsInRect(rect) {
    const ids = new Set();
    elements.forEach((entry, id) => {
      const s = entry.state;
      if (rectsIntersect(rect, { x: s.x, y: s.y, width: s.width, height: s.height })) {
        ids.add(id);
      }
    });
    return ids;
  }

  viewport.addEventListener('mousedown', (e) => {
    if (activeTool === 'pencil') {
      if (e.button === 0) startDrawing(e);
      return;
    }
    if (activeTool === 'line') {
      if (e.button === 0) handleLineToolClick(e);
      else if (e.button === 2) cancelLineTool();
      return;
    }
    if (e.target !== viewport && e.target !== canvas) return;

    if (e.button === 2 || e.button === 1) {
      // middle-click behaves exactly like right-click drag (pan the whole
      // board) — preventDefault stops the browser's native middle-click
      // autoscroll mode from kicking in instead
      e.preventDefault();
      panning = { startX: e.clientX, startY: e.clientY, origPanX: panX, origPanY: panY };
      viewport.classList.add('panning');
      return;
    }
    if (e.button !== 0) return;

    marqueeState = {
      startX: e.clientX,
      startY: e.clientY,
      active: false,
      base: e.shiftKey ? new Set(selectedIds) : new Set(),
    };
  });

  viewport.addEventListener('contextmenu', (e) => {
    if (e.target === viewport || e.target === canvas) e.preventDefault();
  });

  function setActiveTool(tool) {
    activeTool = activeTool === tool ? 'select' : tool;
    pencilBtn.classList.toggle('active', activeTool === 'pencil');
    lineBtn.classList.toggle('active', activeTool === 'line');
    viewport.classList.toggle('drawing', activeTool === 'pencil' || activeTool === 'line');
    cancelLineTool();
    deselectAll();
  }
  pencilBtn.addEventListener('click', () => setActiveTool('pencil'));
  lineBtn.addEventListener('click', () => setActiveTool('line'));

  // if a line is selected, the pencil controls edit it directly instead of only setting the tool default
  function selectedLineId() {
    if (selectedId === null) return null;
    const entry = elements.get(selectedId);
    return entry && entry.state.type === 'line' ? selectedId : null;
  }
  pencilColorInput.addEventListener('input', () => {
    const id = selectedLineId();
    if (id !== null) applyLocalStyle(id, { stroke_color: pencilColorInput.value });
  });
  pencilColorInput.addEventListener('change', () => {
    const id = selectedLineId();
    if (id !== null) commitStyle(id);
  });
  pencilWidthInput.addEventListener('change', () => {
    const id = selectedLineId();
    if (id !== null) {
      applyLocalStyle(id, { stroke_width: clamp(parseFloat(pencilWidthInput.value) || 3, 1, 40) });
      commitStyle(id);
    }
  });
  [pencilColorInput, pencilWidthInput].forEach((el) => el.addEventListener('mousedown', (e) => e.stopPropagation()));

  // ---- zoom (wheel), centered on the cursor ----
  viewport.addEventListener(
    'wheel',
    (e) => {
      e.preventDefault();
      const rect = viewport.getBoundingClientRect();
      const cursorX = e.clientX - rect.left;
      const cursorY = e.clientY - rect.top;
      const contentX = (cursorX - panX) / zoom;
      const contentY = (cursorY - panY) / zoom;

      const newZoom = clamp(zoom * Math.pow(1.0015, -e.deltaY), ZOOM_MIN, ZOOM_MAX);
      panX = cursorX - contentX * newZoom;
      panY = cursorY - contentY * newZoom;
      zoom = newZoom;
      applyTransform();
    },
    { passive: false }
  );

  document.addEventListener('mousemove', (e) => {
    if (minimapDrag) {
      applyMinimapDrag(minimapDrag.axis, e);
    }
    if (panning) {
      panX = panning.origPanX + (e.clientX - panning.startX);
      panY = panning.origPanY + (e.clientY - panning.startY);
      applyTransform();
    }
    if (drawState) {
      addDrawPoint(e);
    }
    if (lineToolState) {
      // pure hover-follow between the two clicks, not a held-button drag
      const point = clientToCanvasPoint(e);
      const end = e.shiftKey ? snapAngle(lineToolState.start, point) : point;
      lineToolState.path.setAttribute('d', pointsToPath([lineToolState.start, end]));
    }
    if (endpointDragState) {
      const dx = (e.clientX - endpointDragState.startX) / zoom;
      const dy = (e.clientY - endpointDragState.startY) / zoom;
      const entry = elements.get(endpointDragState.id);
      if (entry) {
        const points = endpointDragState.origPoints.map((p) => [p[0], p[1]]);
        const idx = endpointDragState.which === 'start' ? 0 : points.length - 1;
        points[idx] = [points[idx][0] + dx, points[idx][1] + dy];
        entry.content.setAttribute('d', pointsToPath(points));
        positionLineEndpoints(entry.div, points);
        endpointDragState.livePoints = points;
      }
    }
    if (marqueeState) {
      const dx = e.clientX - marqueeState.startX;
      const dy = e.clientY - marqueeState.startY;
      if (!marqueeState.active && (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD)) {
        marqueeState.active = true;
        marqueeBox.style.display = 'block';
      }
      if (marqueeState.active) {
        marqueeBox.style.left = Math.min(e.clientX, marqueeState.startX) + 'px';
        marqueeBox.style.top = Math.min(e.clientY, marqueeState.startY) + 'px';
        marqueeBox.style.width = Math.abs(dx) + 'px';
        marqueeBox.style.height = Math.abs(dy) + 'px';

        const rect = canvasRectFromScreen(marqueeState.startX, marqueeState.startY, e.clientX, e.clientY);
        const hitIds = elementsInRect(rect);
        setSelection(new Set([...marqueeState.base, ...hitIds]));
      }
    }
    if (dragCandidate && !dragState) {
      const dx = e.clientX - dragCandidate.startX;
      const dy = e.clientY - dragCandidate.startY;
      if (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD) {
        dragState = dragCandidate;
        dragState.items.forEach((item) => item.div.classList.add('dragging'));
        // re-anchor to the current pointer position: mousemove events are
        // batched, so by the time the threshold is crossed the pointer may
        // already be several pixels further than DRAG_THRESHOLD — using the
        // original mousedown position here would make elements jump to
        // "catch up" with that backlog instead of moving smoothly from here
        dragState.startX = e.clientX;
        dragState.startY = e.clientY;
        dragState.items.forEach((item) => {
          item.origX = item.newX;
          item.origY = item.newY;
        });
      }
    }
    if (dragState) {
      const dx = (e.clientX - dragState.startX) / zoom;
      const dy = (e.clientY - dragState.startY) / zoom;
      dragState.items.forEach((item) => {
        item.newX = item.origX + dx;
        item.newY = item.origY + dy;
        item.div.style.left = item.newX + 'px';
        item.div.style.top = item.newY + 'px';
      });
    }
    if (resizeState) {
      const dx = (e.clientX - resizeState.startX) / zoom;
      const dy = (e.clientY - resizeState.startY) / zoom;
      resizeState.div.style.width = Math.max(40, resizeState.origWidth + dx) + 'px';
      resizeState.div.style.height = Math.max(30, resizeState.origHeight + dy) + 'px';
    }
  });

  document.addEventListener('mouseup', () => {
    if (minimapDrag) {
      minimapH.classList.remove('dragging');
      minimapV.classList.remove('dragging');
      minimapDrag = null;
    }
    if (panning) {
      panning = null;
      viewport.classList.remove('panning');
    }
    if (drawState) {
      finishDrawing();
    }
    if (marqueeState) {
      if (!marqueeState.active) {
        deselectAll();
      }
      marqueeBox.style.display = 'none';
      marqueeState = null;
    }
    if (dragState) {
      dragState.items.forEach((item) => {
        item.div.classList.remove('dragging');
        sendOp({ op: 'update', id: item.id, element: { ...item.rest, x: item.newX, y: item.newY } });
      });
      dragState = null;
    }
    dragCandidate = null;
    if (resizeState) {
      const newWidth = parseFloat(resizeState.div.style.width);
      const newHeight = parseFloat(resizeState.div.style.height);
      sendOp({ op: 'update', id: resizeState.id, element: { ...resizeState.rest, width: newWidth, height: newHeight } });
      resizeState = null;
    }
    if (endpointDragState) {
      const entry = elements.get(endpointDragState.id);
      if (entry && endpointDragState.livePoints) {
        const state = entry.state;
        // points are stored relative to x/y — recompute the bounding box
        // from the moved endpoint, same normalization as finishDrawing()
        const absPoints = endpointDragState.livePoints.map((p) => [p[0] + state.x, p[1] + state.y]);
        const xs = absPoints.map((p) => p[0]);
        const ys = absPoints.map((p) => p[1]);
        const minX = Math.min(...xs);
        const minY = Math.min(...ys);
        const relativePoints = absPoints.map((p) => [p[0] - minX, p[1] - minY]);
        sendOp({
          op: 'update',
          id: endpointDragState.id,
          element: {
            type: 'line',
            x: minX,
            y: minY,
            width: Math.max(1, Math.max(...xs) - minX),
            height: Math.max(1, Math.max(...ys) - minY),
            z_index: state.z_index,
            data: { ...state.data, points: relativePoints },
          },
        });
      }
      endpointDragState = null;
    }
  });

  // ---- z-order ----
  function setZIndex(id, newZ) {
    const entry = elements.get(id);
    if (!entry) return;
    entry.state.z_index = newZ;
    entry.div.style.zIndex = newZ;
    commitStyle(id);
  }

  function bringToFront(id) {
    if (!elements.has(id)) return;
    let maxZ = 0;
    elements.forEach((e) => {
      if (e.state.z_index > maxZ) maxZ = e.state.z_index;
    });
    setZIndex(id, maxZ + 1);
  }

  function sendToBack(id) {
    if (!elements.has(id)) return;
    let minZ = 0;
    elements.forEach((e) => {
      if (e.state.z_index < minZ) minZ = e.state.z_index;
    });
    setZIndex(id, minZ - 1);
  }

  function bringForward(id) {
    const entry = elements.get(id);
    if (!entry) return;
    const currentZ = entry.state.z_index;
    let neighborId = null;
    let neighborZ = null;
    elements.forEach((e, eid) => {
      if (eid === id || e.state.z_index <= currentZ) return;
      if (neighborZ === null || e.state.z_index < neighborZ) {
        neighborZ = e.state.z_index;
        neighborId = eid;
      }
    });
    if (neighborId === null) return;
    setZIndex(id, neighborZ);
    setZIndex(neighborId, currentZ);
  }

  function sendBackward(id) {
    const entry = elements.get(id);
    if (!entry) return;
    const currentZ = entry.state.z_index;
    let neighborId = null;
    let neighborZ = null;
    elements.forEach((e, eid) => {
      if (eid === id || e.state.z_index >= currentZ) return;
      if (neighborZ === null || e.state.z_index > neighborZ) {
        neighborZ = e.state.z_index;
        neighborId = eid;
      }
    });
    if (neighborId === null) return;
    setZIndex(id, neighborZ);
    setZIndex(neighborId, currentZ);
  }

  // ---- right-click context menu ----
  const contextMenu = document.createElement('div');
  contextMenu.className = 'wb-context-menu';

  function addMenuItem(label, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = label;
    btn.addEventListener('click', () => {
      if (contextMenuElementId !== null) onClick(contextMenuElementId);
      hideContextMenu();
    });
    contextMenu.appendChild(btn);
    return btn;
  }

  function addMenuSeparator() {
    const sep = document.createElement('div');
    sep.className = 'wb-context-menu-separator';
    contextMenu.appendChild(sep);
    return sep;
  }

  let contextMenuElementId = null;
  let contextMenuNearestEnd = null; // 'start' | 'end' — which end of a line the right-click landed nearest to

  addMenuItem('Premier plan', bringToFront);
  addMenuItem('Monter', bringForward);
  addMenuItem('Descendre', sendBackward);
  addMenuItem('Arrière-plan', sendToBack);
  addMenuSeparator();
  const lockMenuItem = addMenuItem('Fixer', (id) => sendOp({ op: 'toggle_lock', id }));
  addMenuItem('Dupliquer', duplicateElement);
  addMenuItem('Supprimer', (id) => sendOp({ op: 'delete', id }));
  const reactionSeparator = addMenuSeparator();
  const reactionMenuItems = REACTION_ORDER.map((key) =>
    addMenuItem(`${REACTION_ICONS[key]} ${REACTION_LABELS[key]}`, (id) =>
      sendOp({ op: 'react', id, reaction: key, voter_id: VOTER_ID })
    )
  );
  const arrowSeparator = addMenuSeparator();
  const arrowMenuItem = addMenuItem('➔ Ajouter une flèche', (id) => {
    const entry = elements.get(id);
    if (!entry || !contextMenuNearestEnd) return;
    const field = contextMenuNearestEnd === 'start' ? 'arrow_start' : 'arrow_end';
    applyLocalStyle(id, { [field]: !entry.state.data[field] });
    commitStyle(id);
  });
  document.body.appendChild(contextMenu);

  function showContextMenu(x, y, elementId) {
    contextMenuElementId = elementId;
    const entry = elements.get(elementId);
    const isText = !!entry && entry.state.type === 'text';
    const isLine = !!entry && entry.state.type === 'line';
    lockMenuItem.textContent = entry && entry.state.locked ? 'Détacher' : 'Fixer';
    reactionSeparator.style.display = isText ? '' : 'none';
    const reactions = isText ? normalizeTextData(entry.state.data).reactions : null;
    reactionMenuItems.forEach((btn, i) => {
      btn.style.display = isText ? '' : 'none';
      if (!isText) return;
      const key = REACTION_ORDER[i];
      const voted = reactions[key].includes(VOTER_ID);
      btn.textContent = `${REACTION_ICONS[key]} ${REACTION_LABELS[key]}${voted ? ' ✓ (retirer)' : ''}`;
    });

    arrowSeparator.style.display = isLine ? '' : 'none';
    arrowMenuItem.style.display = isLine ? '' : 'none';
    if (isLine) {
      const canvasPt = clientToCanvasPoint({ clientX: x, clientY: y });
      const localPt = [canvasPt[0] - entry.state.x, canvasPt[1] - entry.state.y];
      const points = entry.state.data.points;
      const start = points[0];
      const end = points[points.length - 1];
      const distStart = Math.hypot(localPt[0] - start[0], localPt[1] - start[1]);
      const distEnd = Math.hypot(localPt[0] - end[0], localPt[1] - end[1]);
      contextMenuNearestEnd = distStart <= distEnd ? 'start' : 'end';
      const field = contextMenuNearestEnd === 'start' ? 'arrow_start' : 'arrow_end';
      arrowMenuItem.textContent = entry.state.data[field] ? '➔ Retirer la flèche' : '➔ Ajouter une flèche';
    } else {
      contextMenuNearestEnd = null;
    }

    contextMenu.style.left = x + 'px';
    contextMenu.style.top = y + 'px';
    contextMenu.style.display = 'block';
  }

  function hideContextMenu() {
    contextMenu.style.display = 'none';
    contextMenuElementId = null;
  }

  function duplicateElement(id) {
    const entry = elements.get(id);
    if (!entry) return;
    const state = entry.state;
    const ref = 'c' + Math.random().toString(36).slice(2);
    pendingFocusRef = ref;
    pendingFocusEdit = false;
    sendOp({
      op: 'create',
      client_ref: ref,
      element: {
        type: state.type,
        x: state.x + 20,
        y: state.y + 20,
        width: state.width,
        height: state.height,
        z_index: state.z_index,
        data: state.type === 'text' ? { ...state.data, reactions: {} } : { ...state.data },
      },
    });
  }

  function copySelection() {
    if (selectedIds.size === 0) return;
    clipboard = [...selectedIds]
      .map((id) => elements.get(id))
      .filter(Boolean)
      .map((entry) => ({ ...entry.state }));
    pasteCount = 0;
  }

  function pasteClipboard() {
    if (clipboard.length === 0) return;
    pasteCount++;
    const offset = 20 * pasteCount;
    const refs = new Set();
    clipboard.forEach((state) => {
      const ref = 'c' + Math.random().toString(36).slice(2);
      refs.add(ref);
      sendOp({
        op: 'create',
        client_ref: ref,
        element: {
          type: state.type,
          x: state.x + offset,
          y: state.y + offset,
          width: state.width,
          height: state.height,
          z_index: state.z_index,
          data: state.type === 'text' ? { ...state.data, reactions: {} } : { ...state.data },
        },
      });
    });
    pendingPasteRefs = refs;
    pendingPasteIds = new Set();
  }

  function deleteSelectedElements() {
    if (selectedIds.size === 0) return;
    const count = selectedIds.size;
    const message = count === 1 ? 'Supprimer cet élément ?' : `Supprimer ces ${count} éléments ?`;
    if (!confirm(message)) return;
    const ids = [...selectedIds];
    deselectAll();
    ids.forEach((id) => sendOp({ op: 'delete', id }));
  }

  document.addEventListener('click', hideContextMenu);
  document.addEventListener('contextmenu', (e) => {
    if (!e.target.closest('.wb-element')) hideContextMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      hideContextMenu();
      if (lineToolState) cancelLineTool();
      else if (editingId !== null) stopEditing();
      else deselectAll();
      return;
    }
    if ((e.key === 'Delete' || e.key === 'Backspace') && selectedIds.size > 0 && editingId === null) {
      // don't hijack Backspace/Delete while it's actually editing text
      // somewhere (a name field, the font-size input, a table cell...) —
      // editingId === null already rules out the whiteboard's own text/
      // table blocks, this covers everything else on the page
      const active = document.activeElement;
      if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return;
      e.preventDefault();
      deleteSelectedElements();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 'c' || e.key === 'C') && editingId === null) {
      // same guard as Delete/Backspace: never hijack a real text copy
      // (typing in the rename field, the font-size input, a table cell...)
      const active = document.activeElement;
      if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return;
      if (selectedIds.size === 0) return;
      e.preventDefault();
      copySelection();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === 'v' || e.key === 'V') && editingId === null) {
      const active = document.activeElement;
      if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return;
      if (clipboard.length === 0) return;
      e.preventDefault();
      pasteClipboard();
    }
  });

  // ---- selection + style toolbar (text blocks) ----
  const toolbar = document.createElement('div');
  toolbar.className = 'wb-toolbar';

  const tbFontSize = document.createElement('input');
  tbFontSize.type = 'number';
  tbFontSize.min = '8';
  tbFontSize.max = '96';
  tbFontSize.className = 'wb-tb-input';
  tbFontSize.title = 'Taille du texte sélectionné';
  tbFontSize.placeholder = String(DEFAULT_FONT_SIZE);

  const tbColor = document.createElement('input');
  tbColor.type = 'color';
  tbColor.title = 'Couleur du texte';

  const tbBold = document.createElement('button');
  tbBold.type = 'button';
  tbBold.textContent = 'G';
  tbBold.title = 'Gras (texte sélectionné)';
  tbBold.className = 'wb-tb-toggle';

  const tbItalic = document.createElement('button');
  tbItalic.type = 'button';
  tbItalic.textContent = 'I';
  tbItalic.title = 'Italique (texte sélectionné)';
  tbItalic.className = 'wb-tb-toggle';

  const tbUnderline = document.createElement('button');
  tbUnderline.type = 'button';
  tbUnderline.textContent = 'S';
  tbUnderline.title = 'Souligné (texte sélectionné)';
  tbUnderline.className = 'wb-tb-toggle wb-tb-underline';

  const tbStrikethrough = document.createElement('button');
  tbStrikethrough.type = 'button';
  tbStrikethrough.textContent = 'B';
  tbStrikethrough.title = 'Barré (texte sélectionné)';
  tbStrikethrough.className = 'wb-tb-toggle wb-tb-strikethrough';

  const tbBullet = document.createElement('button');
  tbBullet.type = 'button';
  tbBullet.textContent = '•';
  tbBullet.title = 'Puce (ligne courante)';
  tbBullet.className = 'wb-tb-toggle';

  const tbBg = document.createElement('input');
  tbBg.type = 'color';
  tbBg.title = 'Couleur de fond';

  const tbBorder = document.createElement('input');
  tbBorder.type = 'color';
  tbBorder.title = 'Couleur de bordure';

  [tbFontSize, tbColor, tbBold, tbItalic, tbUnderline, tbStrikethrough, tbBullet, tbBg, tbBorder].forEach((el) =>
    toolbar.appendChild(el)
  );
  document.body.appendChild(toolbar);

  // save the last non-collapsed text selection made inside an editing text
  // block, since clicking into the font-size number input steals focus
  // (and with it window.getSelection()) before the change event can apply it
  let savedTextSelection = null;

  document.addEventListener('selectionchange', () => {
    if (editingId === null) return;
    const entry = elements.get(editingId);
    if (!entry || entry.state.type !== 'text') return;
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || !entry.content.contains(sel.anchorNode)) return;
    if (!sel.isCollapsed) savedTextSelection = sel.getRangeAt(0).cloneRange();
    tbBold.classList.toggle('active', document.queryCommandState('bold'));
    tbItalic.classList.toggle('active', document.queryCommandState('italic'));
    tbUnderline.classList.toggle('active', document.queryCommandState('underline'));
    tbStrikethrough.classList.toggle('active', document.queryCommandState('strikeThrough'));
  });

  function commitLiveTextEdit(id) {
    const entry = elements.get(id);
    if (!entry || entry.state.type !== 'text') return;
    applyLocalStyle(id, { paragraphs: serializeTextParagraphs(entry.content) });
    commitStyle(id);
  }

  function showToolbarFor(id) {
    const entry = elements.get(id);
    hideToolbar();
    hideTableToolbar();
    if (!entry) return;
    if (entry.state.type === 'text') {
      showTextToolbar(entry);
    } else if (entry.state.type === 'table') {
      showTableToolbar(entry);
    }
  }

  function showTextToolbar(entry) {
    // showToolbarFor() is re-invoked whenever a remote update touches the
    // currently-selected element — including our OWN echoed update after
    // applying a font size. If we always reset here, clicking the number
    // input's spinner would wipe the saved selection/value on every click
    // (round-trips faster than you can click), making the size look stuck
    // at "min". Only reset for a genuinely fresh selection, i.e. when we
    // are not still mid-edit on this same block.
    if (editingId !== selectedId) {
      savedTextSelection = null;
      tbFontSize.value = '';
      tbBold.classList.remove('active');
      tbItalic.classList.remove('active');
      tbUnderline.classList.remove('active');
      tbStrikethrough.classList.remove('active');
    }
    const data = normalizeTextData(entry.state.data);
    tbColor.value = data.color;
    tbBg.value = data.bg_color;
    tbBorder.value = data.border_color;

    const rect = entry.div.getBoundingClientRect();
    toolbar.style.left = rect.left + 'px';
    toolbar.style.top = Math.max(4, rect.top - 44) + 'px';
    toolbar.style.display = 'flex';
  }

  function hideToolbar() {
    toolbar.style.display = 'none';
  }

  // ---- selection toolbar (table blocks) ----
  const tableToolbar = document.createElement('div');
  tableToolbar.className = 'wb-toolbar';

  const tableFontSizeInput = document.createElement('input');
  tableFontSizeInput.type = 'number';
  tableFontSizeInput.min = '8';
  tableFontSizeInput.max = '48';
  tableFontSizeInput.className = 'wb-tb-input';
  tableFontSizeInput.title = 'Taille du texte (tout le tableau)';
  tableToolbar.appendChild(tableFontSizeInput);

  const tableBoldBtn = document.createElement('button');
  tableBoldBtn.type = 'button';
  tableBoldBtn.textContent = 'G';
  tableBoldBtn.title = 'Gras (cellule active)';
  tableBoldBtn.className = 'wb-tb-toggle';
  tableToolbar.appendChild(tableBoldBtn);

  const tableItalicBtn = document.createElement('button');
  tableItalicBtn.type = 'button';
  tableItalicBtn.textContent = 'I';
  tableItalicBtn.title = 'Italique (cellule active)';
  tableItalicBtn.className = 'wb-tb-toggle wb-tb-toggle-italic';
  tableToolbar.appendChild(tableItalicBtn);

  function addTableToolbarButton(label, title, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = label;
    btn.title = title;
    btn.className = 'wb-tb-action';
    btn.addEventListener('mousedown', (e) => e.stopPropagation());
    btn.addEventListener('click', () => {
      if (selectedId !== null) onClick(selectedId);
    });
    tableToolbar.appendChild(btn);
  }

  addTableToolbarButton('+ Ligne', 'Ajouter une ligne', addTableRow);
  addTableToolbarButton('− Ligne', 'Supprimer une ligne', removeTableRow);
  addTableToolbarButton('+ Colonne', 'Ajouter une colonne', addTableColumn);
  addTableToolbarButton('− Colonne', 'Supprimer une colonne', removeTableColumn);
  document.body.appendChild(tableToolbar);

  function updateTableToolbarToggleState(entry) {
    const fc = entry.focusedCell;
    const rawCell = fc && entry.state.data.rows[fc.row] ? entry.state.data.rows[fc.row][fc.col] : null;
    const cell = rawCell ? normalizeCell(rawCell) : null;
    tableBoldBtn.classList.toggle('active', !!(cell && cell.bold));
    tableItalicBtn.classList.toggle('active', !!(cell && cell.italic));
  }

  function showTableToolbar(entry) {
    tableFontSizeInput.value = entry.state.data.font_size;
    updateTableToolbarToggleState(entry);
    const rect = entry.div.getBoundingClientRect();
    tableToolbar.style.left = rect.left + 'px';
    tableToolbar.style.top = Math.max(4, rect.top - 44) + 'px';
    tableToolbar.style.display = 'flex';
  }

  function hideTableToolbar() {
    tableToolbar.style.display = 'none';
  }

  function setTableFontSize(id, size) {
    const entry = elements.get(id);
    if (!entry || entry.state.type !== 'table') return;
    applyLocalStyle(id, { font_size: clamp(parseInt(size, 10) || 14, 8, 48) });
    commitStyle(id);
  }

  function toggleFocusedCellStyle(id, field) {
    const entry = elements.get(id);
    if (!entry || entry.state.type !== 'table' || !entry.focusedCell) return;
    const { row, col } = entry.focusedCell;
    const rows = tableRowsCopy(entry);
    if (!rows[row] || !rows[row][col]) return;
    const cell = normalizeCell(rows[row][col]);
    rows[row][col] = { ...cell, [field]: !cell[field] };
    applyLocalStyle(id, { rows });
    commitStyle(id);
    updateTableToolbarToggleState(entry);
  }

  tableFontSizeInput.addEventListener('mousedown', (e) => e.stopPropagation());
  tableFontSizeInput.addEventListener('change', () => {
    if (selectedId !== null) setTableFontSize(selectedId, tableFontSizeInput.value);
  });
  [tableBoldBtn, tableItalicBtn].forEach((btn) => btn.addEventListener('mousedown', (e) => e.stopPropagation()));
  tableBoldBtn.addEventListener('click', () => {
    if (selectedId !== null) toggleFocusedCellStyle(selectedId, 'bold');
  });
  tableItalicBtn.addEventListener('click', () => {
    if (selectedId !== null) toggleFocusedCellStyle(selectedId, 'italic');
  });

  function tableRowsCopy(entry) {
    // normalize every cell (not just the one being touched) so an update to
    // a table that still has legacy plain-string cells doesn't send a
    // mixed-shape rows array the server rejects, silently discarding the edit
    return entry.state.data.rows.map((row) => row.map(normalizeCell));
  }

  function blankCell() {
    return { text: '', bold: false, italic: false };
  }

  // tables created before cells gained bold/italic stored plain strings;
  // keep reading them working instead of rendering blank cells
  function normalizeCell(cell) {
    return typeof cell === 'string' ? { text: cell, bold: false, italic: false } : cell;
  }

  // .textContent ignores <br>/block boundaries entirely (multi-line edits
  // would come back as one concatenated line); .innerText respects the
  // rendered layout and turns line breaks back into '\n'. Contenteditable
  // often leaves one stray trailing <br>, so trim a single trailing newline.
  function getCellText(td) {
    return td.innerText.replace(/\n$/, '');
  }

  // build actual <br> elements for line breaks instead of relying on
  // white-space:pre-wrap to render literal '\n' characters — keeps
  // multi-line cells working even if that CSS isn't in effect for any
  // reason. Text is inserted via createTextNode, never HTML, so it stays
  // safe against injection.
  function setCellText(td, text) {
    td.textContent = '';
    const lines = (text || '').split('\n');
    lines.forEach((line, i) => {
      if (i > 0) td.appendChild(document.createElement('br'));
      if (line) td.appendChild(document.createTextNode(line));
    });
  }

  function addTableRow(id) {
    const entry = elements.get(id);
    if (!entry || entry.state.type !== 'table') return;
    const rows = tableRowsCopy(entry);
    if (rows.length >= MAX_TABLE_ROWS) return;
    const ncols = rows[0] ? rows[0].length : 2;
    const insertAt = entry.focusedCell ? entry.focusedCell.row + 1 : rows.length;
    rows.splice(insertAt, 0, Array.from({ length: ncols }, blankCell));
    applyLocalStyle(id, { rows });
    commitStyle(id);
  }

  function removeTableRow(id) {
    const entry = elements.get(id);
    if (!entry || entry.state.type !== 'table') return;
    const rows = tableRowsCopy(entry);
    if (rows.length <= 1) return;
    const removeAt = entry.focusedCell ? Math.min(entry.focusedCell.row, rows.length - 1) : rows.length - 1;
    rows.splice(removeAt, 1);
    applyLocalStyle(id, { rows });
    commitStyle(id);
  }

  function addTableColumn(id) {
    const entry = elements.get(id);
    if (!entry || entry.state.type !== 'table') return;
    const rows = tableRowsCopy(entry);
    if (rows[0] && rows[0].length >= MAX_TABLE_COLS) return;
    const insertAt = entry.focusedCell ? entry.focusedCell.col + 1 : rows[0] ? rows[0].length : 0;
    rows.forEach((row) => row.splice(insertAt, 0, blankCell()));
    applyLocalStyle(id, { rows });
    commitStyle(id);
  }

  function removeTableColumn(id) {
    const entry = elements.get(id);
    if (!entry || entry.state.type !== 'table') return;
    const rows = tableRowsCopy(entry);
    if (!rows[0] || rows[0].length <= 1) return;
    const removeAt = entry.focusedCell ? Math.min(entry.focusedCell.col, rows[0].length - 1) : rows[0].length - 1;
    rows.forEach((row) => row.splice(removeAt, 1));
    applyLocalStyle(id, { rows });
    commitStyle(id);
  }

  function applyLocalStyle(id, partialData) {
    const entry = elements.get(id);
    if (!entry) return;
    entry.state.data = { ...entry.state.data, ...partialData };
    if (entry.state.type === 'text') {
      applyTextStyle(entry.div, entry.content, entry.state.data);
    } else if (entry.state.type === 'line') {
      applyLineStyle(entry.content, entry.state);
    } else if (entry.state.type === 'table') {
      renderTableCells(entry.content, entry.state.data.rows, entry.state.data.font_size);
    }
  }

  function commitStyle(id) {
    const entry = elements.get(id);
    if (!entry) return;
    sendOp({
      op: 'update',
      id,
      element: {
        type: entry.state.type,
        x: entry.state.x,
        y: entry.state.y,
        width: entry.state.width,
        height: entry.state.height,
        z_index: entry.state.z_index,
        data: entry.state.data,
      },
    });
  }

  tbFontSize.addEventListener('change', () => {
    if (selectedId === null || !savedTextSelection) return;
    const entry = elements.get(selectedId);
    if (!entry) return;
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(savedTextSelection);
    // execCommand needs the contenteditable itself focused to target it —
    // focus moved to this number input when the user clicked it, so move
    // it back first (this doesn't disturb the selection we just restored)
    entry.content.focus({ preventScroll: true });
    applyFontSizeToSelection(entry.content, clamp(parseInt(tbFontSize.value, 10) || DEFAULT_FONT_SIZE, 8, 96));
    savedTextSelection = sel.rangeCount > 0 ? sel.getRangeAt(0).cloneRange() : null;
    commitLiveTextEdit(selectedId);
  });
  tbColor.addEventListener('input', () => selectedId !== null && applyLocalStyle(selectedId, { color: tbColor.value }));
  tbColor.addEventListener('change', () => selectedId !== null && commitStyle(selectedId));
  tbBg.addEventListener('input', () => selectedId !== null && applyLocalStyle(selectedId, { bg_color: tbBg.value }));
  tbBg.addEventListener('change', () => selectedId !== null && commitStyle(selectedId));
  tbBorder.addEventListener('input', () => selectedId !== null && applyLocalStyle(selectedId, { border_color: tbBorder.value }));
  tbBorder.addEventListener('change', () => selectedId !== null && commitStyle(selectedId));
  tbBold.addEventListener('click', () => {
    if (selectedId === null) return;
    document.execCommand('bold');
    tbBold.classList.toggle('active', document.queryCommandState('bold'));
    commitLiveTextEdit(selectedId);
  });
  tbItalic.addEventListener('click', () => {
    if (selectedId === null) return;
    document.execCommand('italic');
    tbItalic.classList.toggle('active', document.queryCommandState('italic'));
    commitLiveTextEdit(selectedId);
  });
  tbUnderline.addEventListener('click', () => {
    if (selectedId === null) return;
    document.execCommand('underline');
    tbUnderline.classList.toggle('active', document.queryCommandState('underline'));
    commitLiveTextEdit(selectedId);
  });
  tbStrikethrough.addEventListener('click', () => {
    if (selectedId === null) return;
    document.execCommand('strikeThrough');
    tbStrikethrough.classList.toggle('active', document.queryCommandState('strikeThrough'));
    commitLiveTextEdit(selectedId);
  });
  tbBullet.addEventListener('click', () => {
    if (selectedId === null) return;
    document.execCommand('insertUnorderedList');
    commitLiveTextEdit(selectedId);
  });
  // bold/italic/underline/strikethrough/bullet act on the current text
  // selection, so their mousedown must preventDefault (not just
  // stopPropagation) or clicking the button would itself blur the block and
  // clear the selection first
  [tbBold, tbItalic, tbUnderline, tbStrikethrough, tbBullet].forEach((el) =>
    el.addEventListener('mousedown', (e) => {
      e.preventDefault();
      e.stopPropagation();
    })
  );
  [tbFontSize, tbColor, tbBg, tbBorder].forEach((el) => el.addEventListener('mousedown', (e) => e.stopPropagation()));

  function setSelection(newIds) {
    selectedIds.forEach((id) => {
      const entry = elements.get(id);
      if (entry) entry.div.classList.remove('selected', 'multi-selected');
    });
    selectedIds = newIds;
    selectedId = selectedIds.size === 1 ? [...selectedIds][0] : null;

    const isGroup = selectedIds.size > 1;
    selectedIds.forEach((id) => {
      const entry = elements.get(id);
      if (entry) entry.div.classList.add(isGroup ? 'multi-selected' : 'selected');
    });

    if (selectedId !== null) {
      const entry = elements.get(selectedId);
      if (entry && entry.state.type === 'line') {
        pencilColorInput.value = entry.state.data.stroke_color;
        pencilWidthInput.value = entry.state.data.stroke_width;
      }
      showToolbarFor(selectedId);
    } else {
      hideToolbar();
      hideTableToolbar();
    }
  }

  function selectElement(id) {
    if (editingId !== null && editingId !== id) stopEditing();
    if (selectedIds.size === 1 && selectedIds.has(id)) {
      showToolbarFor(id);
      return;
    }
    setSelection(new Set([id]));
  }

  function toggleSelection(id) {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelection(next);
  }

  function deselectAll() {
    stopEditing();
    setSelection(new Set());
  }

  // ---- text edit mode ----
  function placeCaretAtEnd(el) {
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function startEditing(id) {
    if (editingId === id) return;
    if (editingId !== null) stopEditing();
    const entry = elements.get(id);
    if (!entry || entry.state.type !== 'text') return;
    entry.content.contentEditable = 'true';
    entry.div.classList.add('editing');
    placeCaretAtEnd(entry.content);
    editingId = id;
    sendOp({ op: 'editing', id, editing: true, cell: null });
  }

  function stopEditing() {
    if (editingId === null) return;
    const entry = elements.get(editingId);
    const id = editingId;
    editingId = null;
    if (!entry) return;
    if (entry.state.type === 'text') {
      entry.content.contentEditable = 'false';
      entry.div.classList.remove('editing');
      sendOp({ op: 'editing', id, editing: false, cell: null });
      const newParagraphs = serializeTextParagraphs(entry.content);
      const oldParagraphs = normalizeTextData(entry.state.data).paragraphs;
      if (JSON.stringify(newParagraphs) !== JSON.stringify(oldParagraphs)) {
        applyLocalStyle(id, { paragraphs: newParagraphs });
        commitStyle(id);
      }
    } else if (entry.state.type === 'table') {
      // blurring the focused cell triggers its own focusout handler, which
      // already commits any pending edit for that cell
      const activeCell = entry.content.querySelector('td:focus');
      if (activeCell) activeCell.blur();
    }
  }

  // ---- element rendering ----
  // text blocks created before rich text (bold/italic/size per character,
  // bullets) stored a flat { content, font_size, bold, italic } shape;
  // convert it to a single paragraph/run on the fly, same pattern as
  // normalizeCell() for legacy table cells.
  // always returns all three reaction kinds as arrays of voter ids (never
  // missing, never a bare number) — also self-heals the brief window where
  // this app stored reactions as plain counts instead of voter-id lists
  function normalizeReactions(raw) {
    const out = {};
    REACTION_ORDER.forEach((key) => {
      const v = raw ? raw[key] : null;
      out[key] = Array.isArray(v) ? v : [];
    });
    return out;
  }

  function normalizeTextData(data) {
    if (data.paragraphs) return { ...data, reactions: normalizeReactions(data.reactions) };
    return {
      paragraphs: [
        {
          bullet: false,
          runs: [
            {
              text: data.content || '',
              bold: !!data.bold,
              italic: !!data.italic,
              underline: !!data.underline,
              strikethrough: !!data.strikethrough,
              font_size: data.font_size || DEFAULT_FONT_SIZE,
            },
          ],
        },
      ],
      color: data.color || TEXT_DEFAULTS.color,
      bg_color: data.bg_color || TEXT_DEFAULTS.bg_color,
      border_color: data.border_color || TEXT_DEFAULTS.border_color,
      reactions: normalizeReactions(data.reactions),
    };
  }

  function applyTextStyle(div, content, rawData) {
    const data = normalizeTextData(rawData);
    content.style.color = data.color || TEXT_DEFAULTS.color;
    div.style.background = data.bg_color || TEXT_DEFAULTS.bg_color;
    div.style.borderColor = data.border_color || TEXT_DEFAULTS.border_color;
  }

  // reaction badges live as a sibling of the contenteditable content div
  // (never inside it), so they can never end up parsed as text by
  // serializeTextParagraphs and can be refreshed independently of any
  // active edit session. Icons come from a fixed literal dict and counts
  // are inserted via textContent, so this stays safe even though it uses
  // innerHTML='' to clear (same clearing pattern as renderTextParagraphs).
  function renderReactions(div, rawData) {
    const data = normalizeTextData(rawData);
    let container = div.querySelector('.wb-text-reactions');
    if (!container) {
      container = document.createElement('div');
      container.className = 'wb-text-reactions';
      div.appendChild(container);
    }
    container.innerHTML = '';
    REACTION_ORDER.forEach((key) => {
      const voters = data.reactions[key];
      const count = voters.length;
      if (count <= 0) return;
      const badge = document.createElement('span');
      badge.className = 'wb-reaction-badge' + (voters.includes(VOTER_ID) ? ' voted' : '');
      const icon = document.createElement('span');
      icon.textContent = REACTION_ICONS[key];
      badge.appendChild(icon);
      if (count > 1) {
        const num = document.createElement('span');
        num.className = 'wb-reaction-count';
        num.textContent = String(count);
        badge.appendChild(num);
      }
      container.appendChild(badge);
    });
  }

  // structure -> DOM: one <div> (or <li>, grouped under a <ul>) per
  // paragraph, one <span> per styled run. Text is always inserted via
  // textContent, never HTML, so this can't be used to inject markup.
  function renderTextParagraphs(content, rawData) {
    const data = normalizeTextData(rawData);
    content.innerHTML = '';
    let currentList = null;
    data.paragraphs.forEach((para) => {
      const lineEl = document.createElement(para.bullet ? 'li' : 'div');
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
      if (para.bullet) {
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

  // DOM -> structure: walk the edited contenteditable, tracking
  // bold/italic/font-size as we descend, and start a new paragraph on
  // <br>/<div>/<p>/<li> boundaries (bullet:true for <li>). Called only on
  // blur, mirroring how table cells are only serialized on focusout.
  function serializeTextParagraphs(content) {
    const paragraphs = [];
    let current = { bullet: false, runs: [] };

    function pushParagraph() {
      if (current.runs.length === 0) current.runs.push(blankRun());
      paragraphs.push(current);
      current = { bullet: false, runs: [] };
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
      if (tag === 'DIV' || tag === 'P') {
        if (current.runs.length > 0) pushParagraph();
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

    // merge consecutive runs with identical formatting, and drop the
    // trailing empty paragraph contenteditable often leaves after the last br/div
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
        return { bullet: para.bullet, runs: runs.length ? runs : [blankRun()] };
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

  // wraps the current text selection in a <span style="font-size:…px">.
  // execCommand('fontSize') only accepts the legacy relative sizes 1-7, not
  // arbitrary px, so we apply a throwaway marker size (7) and swap every
  // <font size="7"> it wraps the selection in for our real span. This
  // reuses the browser's own selection-splitting logic — the same one
  // bold/italic/underline already rely on below — instead of manual Range
  // surgery (surroundContents/extractContents), which mishandled a
  // selection crossing an existing run's boundary (e.g. text already
  // partly bold, or a previous size change) by wrapping the wrong span:
  // extracting a boundary-crossing range clones/splits the partially
  // selected run, and the new size span ended up wrapping that split-off
  // sliver of the *previous* run instead of the intended selection.
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

  function positionDiv(div, el) {
    div.style.left = el.x + 'px';
    div.style.top = el.y + 'px';
    div.style.width = el.width + 'px';
    div.style.height = el.height + 'px';
    div.style.zIndex = el.z_index;
    div.classList.toggle('wb-locked', !!el.locked);
  }

  function buildDragItems(ids) {
    const items = [];
    ids.forEach((id) => {
      const entry = elements.get(id);
      if (!entry || entry.state.locked) return;
      const state = entry.state;
      items.push({
        id,
        div: entry.div,
        origX: state.x,
        origY: state.y,
        newX: state.x,
        newY: state.y,
        rest: { type: state.type, width: state.width, height: state.height, z_index: state.z_index, data: state.data },
      });
    });
    return items;
  }

  function startDragCandidate(e, el) {
    if (e.button !== 0) return;
    e.stopPropagation();
    if (e.shiftKey) {
      toggleSelection(el.id);
      if (!selectedIds.has(el.id)) return; // just removed from selection: nothing to drag
    } else if (!(selectedIds.size > 1 && selectedIds.has(el.id))) {
      // clicking a lone element, or one outside the current group, replaces
      // the selection; clicking a member of an active group keeps it intact
      // so grabbing any one of them drags the whole group
      selectElement(el.id);
    }
    const items = buildDragItems(selectedIds.size > 0 ? selectedIds : new Set([el.id]));
    if (items.length === 0) return; // everything in the drag is locked
    dragCandidate = { startX: e.clientX, startY: e.clientY, items };
  }

  // ---- freehand line drawing (pencil tool) ----
  function pointsToPath(points) {
    if (!points || points.length === 0) return '';
    return points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p[0]} ${p[1]}`).join(' ');
  }

  function clientToCanvasPoint(e) {
    const rect = viewport.getBoundingClientRect();
    return [(e.clientX - rect.left - panX) / zoom, (e.clientY - rect.top - panY) / zoom];
  }

  function startDrawing(e) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('class', 'wb-line wb-line-preview');
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', pencilColorInput.value);
    path.setAttribute('stroke-width', pencilWidthInput.value || 3);
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    previewSvg.appendChild(path);

    drawState = { points: [clientToCanvasPoint(e)], path };
    path.setAttribute('d', pointsToPath(drawState.points));
  }

  function addDrawPoint(e) {
    if (drawState.points.length < MAX_LINE_POINTS) {
      drawState.points.push(clientToCanvasPoint(e));
      drawState.path.setAttribute('d', pointsToPath(drawState.points));
    }
  }

  function finishDrawing() {
    const points = drawState.points;
    drawState.path.remove();
    drawState = null;
    if (points.length < 2) return;

    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    const minX = Math.min(...xs);
    const minY = Math.min(...ys);
    const relativePoints = points.map((p) => [p[0] - minX, p[1] - minY]);

    sendOp({
      op: 'create',
      element: {
        type: 'line',
        x: minX,
        y: minY,
        width: Math.max(1, Math.max(...xs) - minX),
        height: Math.max(1, Math.max(...ys) - minY),
        z_index: 0,
        data: {
          points: relativePoints,
          stroke_color: pencilColorInput.value,
          stroke_width: parseFloat(pencilWidthInput.value) || 3,
        },
      },
    });
  }

  // ---- straight-line tool (click for one end, click for the other) ----
  // rounds the start->raw angle to the nearest 10°, keeping the raw distance,
  // so the endpoint slides along a snapped ray instead of jumping around
  function snapAngle(start, raw) {
    const dx = raw[0] - start[0];
    const dy = raw[1] - start[1];
    const dist = Math.hypot(dx, dy);
    if (dist === 0) return raw;
    const step = (10 * Math.PI) / 180;
    const angle = Math.round(Math.atan2(dy, dx) / step) * step;
    return [start[0] + Math.cos(angle) * dist, start[1] + Math.sin(angle) * dist];
  }

  function cancelLineTool() {
    if (lineToolState) {
      lineToolState.path.remove();
      lineToolState = null;
    }
  }

  function handleLineToolClick(e) {
    const point = clientToCanvasPoint(e);
    if (!lineToolState) {
      const path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('class', 'wb-line wb-line-preview');
      path.setAttribute('fill', 'none');
      path.setAttribute('stroke', pencilColorInput.value);
      path.setAttribute('stroke-width', pencilWidthInput.value || 3);
      path.setAttribute('stroke-linecap', 'round');
      previewSvg.appendChild(path);
      lineToolState = { start: point, path };
      path.setAttribute('d', pointsToPath([point, point]));
      return;
    }

    const start = lineToolState.start;
    const end = e.shiftKey ? snapAngle(start, point) : point;
    lineToolState.path.remove();
    lineToolState = null;

    const xs = [start[0], end[0]];
    const ys = [start[1], end[1]];
    const minX = Math.min(...xs);
    const minY = Math.min(...ys);
    sendOp({
      op: 'create',
      element: {
        type: 'line',
        x: minX,
        y: minY,
        width: Math.max(1, Math.max(...xs) - minX),
        height: Math.max(1, Math.max(...ys) - minY),
        z_index: 0,
        data: {
          points: [
            [start[0] - minX, start[1] - minY],
            [end[0] - minX, end[1] - minY],
          ],
          stroke_color: pencilColorInput.value,
          stroke_width: parseFloat(pencilWidthInput.value) || 3,
          arrow_start: false,
          arrow_end: false,
        },
      },
    });
  }

  function makeLineElementDiv(el) {
    // each line gets its own positioned <svg> wrapper (like a div) so it
    // participates in normal CSS z-index stacking against text/image blocks —
    // a single shared full-canvas svg layer could never be brought above them.
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('class', 'wb-line-svg');
    svg.dataset.id = String(el.id);
    positionLineSvg(svg, el);

    // one shared arrowhead marker per line, scoped by element id so
    // multiple lines' <defs> never collide; auto-start-reverse orients it
    // forward at the end and mirrored at the start, so both tips point
    // outward without needing two separate marker definitions
    const defs = document.createElementNS(SVG_NS, 'defs');
    const marker = document.createElementNS(SVG_NS, 'marker');
    marker.setAttribute('id', `wb-arrow-${el.id}`);
    marker.setAttribute('viewBox', '0 0 10 10');
    marker.setAttribute('refX', '9');
    marker.setAttribute('refY', '5');
    marker.setAttribute('markerWidth', '6');
    marker.setAttribute('markerHeight', '6');
    marker.setAttribute('orient', 'auto-start-reverse');
    const arrowHead = document.createElementNS(SVG_NS, 'path');
    arrowHead.setAttribute('class', 'wb-arrow-fill');
    arrowHead.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
    marker.appendChild(arrowHead);
    defs.appendChild(marker);
    svg.appendChild(defs);

    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('class', 'wb-line');
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(path);
    applyLineStyle(path, el);

    const startHandle = document.createElementNS(SVG_NS, 'circle');
    startHandle.setAttribute('class', 'wb-line-endpoint');
    startHandle.setAttribute('r', '6');
    svg.appendChild(startHandle);

    const endHandle = document.createElementNS(SVG_NS, 'circle');
    endHandle.setAttribute('class', 'wb-line-endpoint');
    endHandle.setAttribute('r', '6');
    svg.appendChild(endHandle);

    positionLineEndpoints(svg, el.data.points);

    startHandle.addEventListener('mousedown', (e) => startEndpointDrag(e, el.id, 'start'));
    endHandle.addEventListener('mousedown', (e) => startEndpointDrag(e, el.id, 'end'));

    path.addEventListener('mousedown', (e) => {
      if (activeTool === 'pencil' || activeTool === 'line') return;
      startDragCandidate(e, el);
    });
    path.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showContextMenu(e.clientX, e.clientY, el.id);
    });

    return { div: svg, content: path };
  }

  function positionLineSvg(svg, el) {
    positionDiv(svg, el);
    svg.setAttribute('width', el.width);
    svg.setAttribute('height', el.height);
  }

  // first/last point of the polyline — "the ends", for both freehand and
  // straight-line-tool traits alike
  function positionLineEndpoints(svg, points) {
    const handles = svg.querySelectorAll('.wb-line-endpoint');
    if (handles.length < 2 || !points || points.length === 0) return;
    const [startHandle, endHandle] = handles;
    const start = points[0];
    const end = points[points.length - 1];
    startHandle.setAttribute('cx', start[0]);
    startHandle.setAttribute('cy', start[1]);
    endHandle.setAttribute('cx', end[0]);
    endHandle.setAttribute('cy', end[1]);
  }

  function startEndpointDrag(e, id, which) {
    if (activeTool !== 'select' || e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    const entry = elements.get(id);
    if (!entry) return;
    selectElement(id);
    endpointDragState = {
      id,
      which,
      startX: e.clientX,
      startY: e.clientY,
      origPoints: entry.state.data.points.map((p) => [p[0], p[1]]),
    };
  }

  function applyLineStyle(path, el) {
    path.setAttribute('d', pointsToPath(el.data.points));
    const color = el.data.stroke_color || '#1f2430';
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-width', el.data.stroke_width || 3);

    const svg = path.ownerSVGElement;
    const arrowFill = svg && svg.querySelector('.wb-arrow-fill');
    if (arrowFill) arrowFill.setAttribute('fill', color);

    const markerId = `wb-arrow-${el.id}`;
    if (el.data.arrow_start) path.setAttribute('marker-start', `url(#${markerId})`);
    else path.removeAttribute('marker-start');
    if (el.data.arrow_end) path.setAttribute('marker-end', `url(#${markerId})`);
    else path.removeAttribute('marker-end');
  }

  function attachResizeHandle(handle, div, el) {
    handle.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      const state = elements.get(el.id).state;
      resizeState = {
        id: el.id,
        div,
        startX: e.clientX,
        startY: e.clientY,
        origWidth: state.width,
        origHeight: state.height,
        rest: { type: state.type, x: state.x, y: state.y, z_index: state.z_index, data: state.data },
      };
    });
  }

  function makeTextElementDiv(el) {
    const div = document.createElement('div');
    div.className = 'wb-element wb-text-element';
    div.dataset.id = String(el.id);
    positionDiv(div, el);

    const content = document.createElement('div');
    content.className = 'wb-text-content';
    renderTextParagraphs(content, el.data);
    div.appendChild(content);

    const resizeHandle = document.createElement('div');
    resizeHandle.className = 'wb-resize-handle';
    div.appendChild(resizeHandle);

    applyTextStyle(div, content, el.data);
    renderReactions(div, el.data);

    div.addEventListener('mousedown', (e) => {
      if (activeTool === 'pencil' || activeTool === 'line') return;
      // while editing, the rendered text is wrapped in <span> runs (see
      // renderTextParagraphs) so e.target is one of those, never `content`
      // itself — contains() is what actually detects "click landed inside
      // the editable text", letting the browser's native text selection
      // happen instead of hijacking the drag into a block move
      if (content.isContentEditable && content.contains(e.target)) {
        e.stopPropagation();
        return;
      }
      startDragCandidate(e, el);
    });

    div.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      startEditing(el.id);
    });

    div.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showContextMenu(e.clientX, e.clientY, el.id);
    });

    attachResizeHandle(resizeHandle, div, el);

    return { div, content };
  }

  function makeImageElementDiv(el) {
    const div = document.createElement('div');
    div.className = 'wb-element wb-image-element';
    div.dataset.id = String(el.id);
    positionDiv(div, el);

    const img = document.createElement('img');
    img.src = el.data.src;
    img.draggable = false;
    img.alt = '';
    div.appendChild(img);

    const resizeHandle = document.createElement('div');
    resizeHandle.className = 'wb-resize-handle';
    div.appendChild(resizeHandle);

    div.addEventListener('mousedown', (e) => {
      if (activeTool === 'pencil' || activeTool === 'line') return;
      startDragCandidate(e, el);
    });
    div.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showContextMenu(e.clientX, e.clientY, el.id);
    });

    attachResizeHandle(resizeHandle, div, el);

    return { div, content: img };
  }

  function renderTableCells(table, rows, fontSize) {
    const size = (fontSize || 14) + 'px';
    table.innerHTML = '';
    rows.forEach((row, r) => {
      const tr = document.createElement('tr');
      row.forEach((rawCell, c) => {
        const cell = normalizeCell(rawCell);
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

  function focusFirstTableCell(id) {
    const entry = elements.get(id);
    if (!entry || entry.state.type !== 'table') return;
    const firstCell = entry.content.querySelector('td');
    if (firstCell) firstCell.focus();
  }

  function makeTableElementDiv(el) {
    const div = document.createElement('div');
    div.className = 'wb-element wb-table-element';
    div.dataset.id = String(el.id);
    positionDiv(div, el);

    const grip = document.createElement('div');
    grip.className = 'wb-table-grip';
    grip.title = 'Glisser pour déplacer';
    grip.textContent = '⠿';
    div.appendChild(grip);

    const table = document.createElement('table');
    table.className = 'wb-table';
    renderTableCells(table, el.data.rows, el.data.font_size);
    div.appendChild(table);

    const resizeHandle = document.createElement('div');
    resizeHandle.className = 'wb-resize-handle';
    div.appendChild(resizeHandle);

    grip.addEventListener('mousedown', (e) => {
      if (activeTool === 'pencil' || activeTool === 'line') return;
      startDragCandidate(e, el);
    });
    div.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showContextMenu(e.clientX, e.clientY, el.id);
    });

    table.addEventListener('keydown', (e) => {
      const td = e.target.closest('td');
      if (!td) return;
      if (e.key === 'Enter' && !e.shiftKey) {
        // make plain Enter behave exactly like Shift+Enter (insert a line
        // break) instead of letting the browser start a new block element,
        // whose text would then get concatenated with no separator on save
        e.preventDefault();
        document.execCommand('insertLineBreak');
      }
    });

    table.addEventListener('focusin', (e) => {
      const td = e.target.closest('td');
      if (!td) return;
      const entry = elements.get(el.id);
      if (!entry) return;
      const r = Number(td.dataset.row);
      const c = Number(td.dataset.col);
      entry.focusedCell = { row: r, col: c };
      editingId = el.id;
      selectElement(el.id);
      sendOp({ op: 'editing', id: el.id, editing: true, cell: [r, c] });
    });

    table.addEventListener('focusout', (e) => {
      const td = e.target.closest('td');
      if (!td) return;
      const entry = elements.get(el.id);
      if (!entry) return;
      const r = Number(td.dataset.row);
      const c = Number(td.dataset.col);
      sendOp({ op: 'editing', id: el.id, editing: false, cell: [r, c] });
      const newText = getCellText(td);
      const currentRows = entry.state.data.rows;
      if (currentRows[r] && normalizeCell(currentRows[r][c]).text !== newText) {
        const rows = tableRowsCopy(entry);
        rows[r][c] = { ...normalizeCell(rows[r][c]), text: newText };
        applyLocalStyle(el.id, { rows });
        commitStyle(el.id);
      }
      if (editingId === el.id) editingId = null;
    });

    attachResizeHandle(resizeHandle, div, el);

    return { div, content: table };
  }

  function makeGenericElementDiv(el) {
    const div = document.createElement('div');
    div.className = 'wb-element';
    div.dataset.id = String(el.id);
    positionDiv(div, el);
    div.textContent = `${el.type} #${el.id}`;

    div.addEventListener('mousedown', (e) => {
      if (activeTool === 'pencil' || activeTool === 'line' || e.button !== 0) return;
      e.stopPropagation();
      dragState = { startX: e.clientX, startY: e.clientY, items: buildDragItems(new Set([el.id])) };
      div.classList.add('dragging');
    });

    div.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showContextMenu(e.clientX, e.clientY, el.id);
    });

    return { div, content: null };
  }

  function makeElementDiv(el) {
    if (el.type === 'text') return makeTextElementDiv(el);
    if (el.type === 'line') return makeLineElementDiv(el);
    if (el.type === 'image') return makeImageElementDiv(el);
    if (el.type === 'table') return makeTableElementDiv(el);
    return makeGenericElementDiv(el);
  }

  function upsertElement(el) {
    // self-heal reactions here, once, so entry.state.data always carries a
    // clean { heart: [...], thumbsup: [...], thumbsdown: [...] } shape from
    // the moment data enters client state — otherwise an old element whose
    // reactions were still stored as a plain count would get that stale
    // shape echoed straight back on the next unrelated edit (e.g. a color
    // change), which the server's dict[str, list[str]] validation rejects
    if (el.type === 'text') {
      el.data = { ...el.data, reactions: normalizeReactions(el.data.reactions) };
    }
    const existing = elements.get(el.id);
    if (existing) {
      existing.state = el;
      if (el.type === 'line') {
        positionLineSvg(existing.div, el);
        if (existing.content) applyLineStyle(existing.content, el);
        positionLineEndpoints(existing.div, el.data.points);
      } else {
        positionDiv(existing.div, el);
        if (el.type === 'text' && existing.content) {
          applyTextStyle(existing.div, existing.content, el.data);
          renderReactions(existing.div, el.data);
          // Skip the destructive rebuild while this block's edit session is
          // still active — including while focus has temporarily moved to
          // the toolbar's font-size input (e.g. clicking its spinner), which
          // would otherwise wipe the saved text selection on every click.
          // Also covers the live-focus case directly, so this can't get
          // stuck the way a hand-maintained flag alone could (same
          // reasoning as the earlier table sync fix).
          const isEditingNow =
            editingId === el.id || (existing.content.isContentEditable && existing.content.contains(document.activeElement));
          if (!isEditingNow) {
            renderTextParagraphs(existing.content, el.data);
          }
        } else if (el.type === 'image' && existing.content && existing.content.src !== el.data.src) {
          existing.content.src = el.data.src;
        } else if (el.type === 'table' && existing.content) {
          // use live DOM focus rather than the editingId flag: it can't get
          // stuck out of sync, so a remote update never permanently stops
          // rendering for this table once the flag fails to clear correctly
          const activeCell = existing.content.querySelector('td:focus');
          if (!activeCell) {
            renderTableCells(existing.content, el.data.rows, el.data.font_size);
          }
        }
      }
      if (selectedId === el.id) showToolbarFor(el.id);
    } else {
      const { div, content } = makeElementDiv(el);
      canvas.appendChild(div);
      elements.set(el.id, { div, content, state: el });
    }
    updateMinimap();
  }

  function removeElement(id) {
    const existing = elements.get(id);
    clearEditingBadgesForElement(id);
    if (existing) {
      if (editingId === id) editingId = null;
      if (selectedIds.has(id)) {
        const next = new Set(selectedIds);
        next.delete(id);
        setSelection(next);
      }
      existing.div.remove();
      elements.delete(id);
      updateMinimap();
    }
  }

  function sendOp(payload) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    }
  }

  // ---- everyone's pointer, relayed live to every other connected viewer ----
  const CURSOR_COLORS = ['#e8590c', '#2f9e44', '#3b5bdb', '#ae3ec9', '#e64980', '#0c8599', '#f08c00'];
  function colorForId(id) {
    let hash = 0;
    for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
    return CURSOR_COLORS[hash % CURSOR_COLORS.length];
  }

  const remoteCursors = new Map(); // connection id -> { el, hideTimer }

  function updateRemoteCursor(id, label, x, y) {
    let entry = remoteCursors.get(id);
    if (!entry) {
      const el = document.createElement('div');
      el.className = 'wb-remote-cursor';
      const color = colorForId(id);
      el.innerHTML = `<span class="wb-remote-cursor-dot" style="background:${color}"></span><span class="wb-remote-cursor-label" style="background:${color}"></span>`;
      el.querySelector('.wb-remote-cursor-label').textContent = label;
      canvas.appendChild(el);
      entry = { el, hideTimer: null };
      remoteCursors.set(id, entry);
    }
    entry.el.style.left = x + 'px';
    entry.el.style.top = y + 'px';
    entry.el.style.display = 'flex';
    clearTimeout(entry.hideTimer);
    entry.hideTimer = setTimeout(() => removeRemoteCursor(id), 3000);
  }

  function removeRemoteCursor(id) {
    const entry = remoteCursors.get(id);
    if (!entry) return;
    clearTimeout(entry.hideTimer);
    entry.el.remove();
    remoteCursors.delete(id);
  }

  // ---- "so-and-so is editing" badge, shown in the corner of the text
  // block / table cell someone else currently has open ----
  const remoteEditing = new Map(); // "id" or "id:row:col" -> { el, editorId }

  function editingKey(id, cell) {
    return cell ? `${id}:${cell[0]}:${cell[1]}` : `${id}`;
  }

  function editingTargetEl(id, cell) {
    const entry = elements.get(id);
    if (!entry) return null;
    if (cell && entry.content) {
      return entry.content.querySelector(`td[data-row="${cell[0]}"][data-col="${cell[1]}"]`);
    }
    return entry.div;
  }

  function showEditingBadge(id, cell, label, editorId) {
    const key = editingKey(id, cell);
    const target = editingTargetEl(id, cell);
    if (!target) return;
    let entry = remoteEditing.get(key);
    if (!entry || entry.el.parentElement !== target) {
      if (entry) entry.el.remove();
      const el = document.createElement('div');
      el.className = 'wb-editing-badge';
      target.appendChild(el);
      entry = { el, editorId };
      remoteEditing.set(key, entry);
    }
    entry.editorId = editorId;
    entry.el.textContent = `✎ ${label}`;
  }

  function hideEditingBadge(id, cell, editorId) {
    const key = editingKey(id, cell);
    const entry = remoteEditing.get(key);
    if (!entry || entry.editorId !== editorId) return;
    entry.el.remove();
    remoteEditing.delete(key);
  }

  function clearEditingBadgesForElement(id) {
    const exact = `${id}`;
    const prefix = `${id}:`;
    Array.from(remoteEditing.keys()).forEach((key) => {
      if (key === exact || key.startsWith(prefix)) {
        remoteEditing.get(key).el.remove();
        remoteEditing.delete(key);
      }
    });
  }

  function clearAllEditingBadges() {
    remoteEditing.forEach((entry) => entry.el.remove());
    remoteEditing.clear();
  }

  let lastCursorSentAt = 0;
  viewport.addEventListener('mousemove', (e) => {
    const now = Date.now();
    if (now - lastCursorSentAt < 50) return;
    lastCursorSentAt = now;
    const [x, y] = clientToCanvasPoint(e);
    sendOp({ op: 'cursor', x, y });
  });

  function connectWs() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/whiteboard/${boardId}`);

    ws.addEventListener('open', () => {
      statusEl.textContent = 'Connecté';
      setTimeout(() => {
        if (statusEl.textContent === 'Connecté') statusEl.textContent = '';
      }, 1500);
    });
    ws.addEventListener('close', () => {
      statusEl.textContent = 'Déconnecté — reconnexion...';
      // these are relayed live by the server and never replayed on
      // reconnect — without this they'd linger forever if the closing
      // connection couldn't get its own cleanup message out in time
      clearAllEditingBadges();
      setTimeout(connectWs, 2000);
    });
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.op === 'created' || msg.op === 'updated') {
        upsertElement(msg.element);
        if (msg.op === 'created' && msg.client_ref && msg.client_ref === pendingFocusRef) {
          pendingFocusRef = null;
          selectElement(msg.element.id);
          if (pendingFocusEdit) {
            if (msg.element.type === 'text') startEditing(msg.element.id);
            else if (msg.element.type === 'table') focusFirstTableCell(msg.element.id);
          }
          pendingFocusEdit = false;
        }
        if (msg.op === 'created' && msg.client_ref && pendingPasteRefs && pendingPasteRefs.has(msg.client_ref)) {
          pendingPasteRefs.delete(msg.client_ref);
          pendingPasteIds.add(msg.element.id);
          if (pendingPasteRefs.size === 0) {
            setSelection(pendingPasteIds);
            pendingPasteRefs = null;
            pendingPasteIds = null;
          }
        }
      } else if (msg.op === 'deleted') {
        removeElement(msg.id);
      } else if (msg.op === 'cursor') {
        updateRemoteCursor(msg.id, msg.label, msg.x, msg.y);
      } else if (msg.op === 'cursor_gone') {
        removeRemoteCursor(msg.id);
      } else if (msg.op === 'editing') {
        if (msg.editing) showEditingBadge(msg.id, msg.cell, msg.label, msg.editor_id);
        else hideEditingBadge(msg.id, msg.cell, msg.editor_id);
      } else if (msg.op === 'error') {
        console.error('whiteboard error:', msg.detail);
      }
    });
  }

  async function load() {
    const res = await fetch(`/api/whiteboard/${boardId}`);
    const data = await res.json();
    data.elements.forEach(upsertElement);
    connectWs();
  }

  addTextBtn.addEventListener('click', () => {
    const center = viewportCenterInCanvas();
    const width = 220;
    const height = 110;
    const ref = 'c' + Math.random().toString(36).slice(2);
    pendingFocusRef = ref;
    pendingFocusEdit = true;
    sendOp({
      op: 'create',
      client_ref: ref,
      element: {
        type: 'text',
        x: Math.round(center.x - width / 2),
        y: Math.round(center.y - height / 2),
        width,
        height,
        z_index: 0,
        data: { ...TEXT_DEFAULTS, paragraphs: [blankParagraph()] },
      },
    });
  });

  addTableBtn.addEventListener('click', () => {
    const center = viewportCenterInCanvas();
    const width = 280;
    const height = 160;
    const ref = 'c' + Math.random().toString(36).slice(2);
    pendingFocusRef = ref;
    pendingFocusEdit = true;
    sendOp({
      op: 'create',
      client_ref: ref,
      element: {
        type: 'table',
        x: Math.round(center.x - width / 2),
        y: Math.round(center.y - height / 2),
        width,
        height,
        z_index: 0,
        data: { rows: Array.from({ length: 5 }, () => [blankCell(), blankCell()]), font_size: 14 },
      },
    });
  });

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

  function placeImageElement(url, dropPosition) {
    const preview = new Image();
    preview.onload = () => {
      const scale = Math.min(1, MAX_IMAGE_DIM / Math.max(preview.naturalWidth, preview.naturalHeight, 1));
      const width = Math.round(preview.naturalWidth * scale) || 200;
      const height = Math.round(preview.naturalHeight * scale) || 150;
      const pos = dropPosition || viewportCenterInCanvas();
      const ref = 'c' + Math.random().toString(36).slice(2);
      pendingFocusRef = ref;
      pendingFocusEdit = false;
      sendOp({
        op: 'create',
        client_ref: ref,
        element: {
          type: 'image',
          x: Math.round(pos.x - width / 2),
          y: Math.round(pos.y - height / 2),
          width,
          height,
          z_index: 0,
          data: { src: url },
        },
      });
    };
    preview.src = url;
  }

  async function uploadImageFile(file, dropPosition) {
    if (!file || !file.type.startsWith('image/')) return;

    const formData = new FormData();
    formData.append('file', file);

    let res;
    try {
      res = await fetch(`/api/whiteboard/${boardId}/upload-image`, { method: 'POST', body: formData });
    } catch (err) {
      showTransientStatus("Échec de l'envoi de l'image", true);
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      showTransientStatus(body.detail || "Image refusée", true);
      return;
    }
    const { url } = await res.json();
    placeImageElement(url, dropPosition);
  }

  async function importImageFromUrl(sourceUrl, dropPosition) {
    let res;
    try {
      res = await fetch(`/api/whiteboard/${boardId}/upload-image-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: sourceUrl }),
      });
    } catch (err) {
      showTransientStatus("Échec de la récupération de l'image", true);
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      showTransientStatus(body.detail || "Image refusée", true);
      return;
    }
    const { url } = await res.json();
    placeImageElement(url, dropPosition);
  }

  addImageBtn.addEventListener('click', () => imageFileInput.click());
  imageFileInput.addEventListener('change', () => {
    if (imageFileInput.files[0]) uploadImageFile(imageFileInput.files[0]);
    imageFileInput.value = '';
  });

  document.addEventListener('paste', (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) uploadImageFile(file);
        return;
      }
    }

    // fallback: copying an image from another page's *rendered content*
    // (rather than an explicit "copy image") often puts HTML on the
    // clipboard instead of raw image bytes, with an <img src="..."> back
    // at that site — fetch it server-side (see upload-image-url). Only do
    // this outside of actual text editing: text/html is what any normal
    // text copy puts on the clipboard too, and must keep pasting as text.
    if (editingId !== null) return;
    const active = document.activeElement;
    if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return;
    const htmlItem = Array.from(items).find((item) => item.type === 'text/html');
    if (!htmlItem) return;
    htmlItem.getAsString((html) => {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const img = doc.querySelector('img[src]');
      const src = img && img.getAttribute('src');
      if (src && /^https?:\/\//i.test(src)) importImageFromUrl(src);
    });
    e.preventDefault();
  });

  viewport.addEventListener('dragover', (e) => {
    if (e.dataTransfer && Array.from(e.dataTransfer.types).includes('Files')) {
      e.preventDefault();
      viewport.classList.add('drag-over');
    }
  });
  viewport.addEventListener('dragleave', (e) => {
    if (e.target === viewport) viewport.classList.remove('drag-over');
  });
  viewport.addEventListener('drop', (e) => {
    viewport.classList.remove('drag-over');
    if (!e.dataTransfer || !e.dataTransfer.files || e.dataTransfer.files.length === 0) return;
    e.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const pos = { x: (e.clientX - rect.left - panX) / zoom, y: (e.clientY - rect.top - panY) / zoom };
    uploadImageFile(e.dataTransfer.files[0], pos);
  });

  nameInput.addEventListener('change', () => {
    fetch(`/api/whiteboard/${boardId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: nameInput.value }),
    });
  });

  if (window.initHistoryPanel) {
    window.initHistoryPanel({
      buttonId: 'history-btn',
      apiUrl: `/api/whiteboard/${boardId}/history`,
      onRestore: (entry) => sendOp({ op: 'restore', log_id: entry.id }),
    });
  }

  load();
})();
