(function () {
  const root = document.querySelector('.cours-page');
  const sessionId = root.dataset.sessionId;
  const isOwner = root.dataset.isOwner === '1';

  const treeEl = document.getElementById('cours-tree');
  const pickerEl = document.getElementById('cours-picker');
  const topicListEl = document.getElementById('cours-topic-list');
  const waitingEl = document.getElementById('cours-waiting');
  const slideWrapEl = document.getElementById('cours-slide-wrap');
  const docTitleEl = document.getElementById('cours-doc-title');
  const changeTopicBtn = document.getElementById('cours-change-topic-btn');
  const resyncBanner = document.getElementById('cours-resync-banner');
  const resyncBtn = document.getElementById('cours-resync-btn');
  const slideEl = document.getElementById('cours-slide');

  const init = JSON.parse(document.getElementById('cours-init-data').textContent);
  const topics = init.topics || [];

  let content = init.content; // { title, tree, slides } | null
  let liveSlideId = init.presentation.current_slide_id;
  let localSlideId = liveSlideId;
  let visitedSlideIds = new Set(init.presentation.visited_slide_ids || []);
  let forcePicker = false;
  let qcmAvailable = init.qcm_available || {}; // { chapter_title: question_count }

  let ws = null;
  function sendOp(payload) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  }

  function parseUtc(iso) {
    // Python's isoformat() on a naive UTC datetime has no timezone suffix —
    // without one, JS's Date parser assumes *local* time, which would
    // shift every countdown by the viewer's UTC offset
    return new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  }

  // ---- QCM corner widget ----
  const qcmPanel = document.createElement('div');
  qcmPanel.className = 'qcm-panel';
  qcmPanel.hidden = true;
  document.body.appendChild(qcmPanel);

  const qcmCountdown = document.createElement('div');
  qcmCountdown.className = 'qcm-countdown';
  qcmCountdown.hidden = true;
  document.body.appendChild(qcmCountdown);

  let qcmState = init.active_qcm_state || null; // last qcm_state payload (owner or student shape)
  let qcmDismissed = false;
  let lastSeenQcmId = qcmState ? qcmState.qcm_session_id : null;

  // student-only: the local, per-viewer queue of questions still to get
  // through (validated ones are dropped, deferred ones move to the end) —
  // reconstructed once per qcm run from the server's own_progress snapshot,
  // then driven purely client-side so a harmless roster-refresh broadcast
  // (another student connecting) never resets an in-progress answer
  let studentQuestions = null;
  let studentOrder = null;
  let studentQueueQcmId = null; // which qcm_session_id studentOrder was built for
  let renderedQuestionIndex = null;
  let questionTimerHandle = null;
  let questionTimerRemaining = 0;
  let questionTimerEl = null;

  function nodeContainsSlide(node, id) {
    if (node.id === id) return true;
    return (node.children || []).some((c) => nodeContainsSlide(c, id));
  }

  // the QCM trigger should show anywhere within a chapter, not only on its
  // exact title slide — a teacher normally works through every ##/### under
  // a chapter before wrapping up with its quiz
  function currentChapterTitle() {
    if (!content) return null;
    const rootNode = content.tree.find((n) => nodeContainsSlide(n, localSlideId));
    return rootNode ? rootNode.title : null;
  }

  function currentStudentQuestion() {
    if (!studentOrder || studentOrder.length === 0) return null;
    return studentQuestions.find((q) => q.index === studentOrder[0]) || null;
  }

  function ensureStudentQueue() {
    // must NOT reuse lastSeenQcmId here: that flag flips as soon as the
    // *pending* broadcast for a new qcm arrives, well before its real
    // questions exist — this needs its own tracker, set only once the
    // queue is actually (re)built from that session's data
    if (studentQueueQcmId === qcmState.qcm_session_id && studentOrder !== null) return;
    studentQueueQcmId = qcmState.qcm_session_id;
    studentQuestions = qcmState.questions || [];
    const progress = qcmState.own_progress || {};
    studentOrder = studentQuestions.map((q) => q.index).filter((i) => progress[String(i)] !== 'validated');
    renderedQuestionIndex = null;
  }

  function clearQuestionTimer() {
    if (questionTimerHandle) clearInterval(questionTimerHandle);
    questionTimerHandle = null;
  }

  function resetQuestionTimer() {
    clearQuestionTimer();
    if (!qcmState || qcmState.status !== 'running' || !currentStudentQuestion()) return;
    questionTimerRemaining = qcmState.seconds_per_question;
    updateQuestionTimerUI();
    questionTimerHandle = setInterval(() => {
      questionTimerRemaining -= 1;
      updateQuestionTimerUI();
      if (questionTimerRemaining <= 0) submitCurrentAnswer('defer');
    }, 1000);
  }

  function updateQuestionTimerUI() {
    if (questionTimerEl) questionTimerEl.textContent = `${Math.max(0, questionTimerRemaining)}s`;
  }

  function submitCurrentAnswer(action) {
    const q = currentStudentQuestion();
    if (!q) return;
    let optionIndices = [];
    if (action === 'validate') {
      optionIndices = Array.from(qcmPanel.querySelectorAll('.qcm-option-checkbox:checked')).map((el) =>
        parseInt(el.value, 10)
      );
    }
    sendOp({ op: 'qcm_answer', question_index: q.index, option_indices: optionIndices, action });
    studentOrder.shift();
    if (action === 'defer') studentOrder.push(q.index);
    renderedQuestionIndex = null;
    resetQuestionTimer();
    renderQcmPanel();
  }

  function renderStudentQuestionForm(q) {
    const form = document.createElement('div');
    form.className = 'qcm-question-form';

    const label = document.createElement('div');
    label.className = 'qcm-question-label';
    label.textContent = q.label;
    form.appendChild(label);

    const text = document.createElement('p');
    text.textContent = q.text;
    form.appendChild(text);

    const list = document.createElement('ul');
    list.className = 'qcm-options';
    q.options.forEach((o) => {
      const li = document.createElement('li');
      const optLabel = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'qcm-option-checkbox';
      cb.value = String(o.index);
      optLabel.appendChild(cb);
      optLabel.appendChild(document.createTextNode(' ' + o.text));
      li.appendChild(optLabel);
      list.appendChild(li);
    });
    form.appendChild(list);

    const actions = document.createElement('div');
    actions.className = 'qcm-question-actions';
    questionTimerEl = document.createElement('span');
    questionTimerEl.className = 'qcm-question-timer';
    actions.appendChild(questionTimerEl);
    const validateBtn = document.createElement('button');
    validateBtn.type = 'button';
    validateBtn.className = 'primary-btn';
    validateBtn.textContent = 'Valider';
    validateBtn.addEventListener('click', () => submitCurrentAnswer('validate'));
    actions.appendChild(validateBtn);
    const deferBtn = document.createElement('button');
    deferBtn.type = 'button';
    deferBtn.className = 'icon-btn';
    deferBtn.textContent = 'Reporter à la fin';
    deferBtn.addEventListener('click', () => submitCurrentAnswer('defer'));
    actions.appendChild(deferBtn);
    form.appendChild(actions);

    return form;
  }

  function renderStudentQcmState() {
    const title = document.createElement('h3');
    title.textContent = `QCM — ${qcmState.chapter_title}`;

    if (qcmState.status === 'pending') {
      qcmPanel.innerHTML = '';
      qcmPanel.appendChild(title);
      const p = document.createElement('p');
      p.textContent = 'Le QCM va commencer, restez sur cette page.';
      qcmPanel.appendChild(p);
      renderedQuestionIndex = null;
      return;
    }

    if (qcmState.status === 'running') {
      ensureStudentQueue();
      const q = currentStudentQuestion();
      if (!q) {
        qcmPanel.innerHTML = '';
        qcmPanel.appendChild(title);
        const p = document.createElement('p');
        p.textContent = "Vous avez terminé, en attente de la fin du QCM.";
        qcmPanel.appendChild(p);
        renderedQuestionIndex = null;
        return;
      }
      if (renderedQuestionIndex !== q.index) {
        qcmPanel.innerHTML = '';
        qcmPanel.appendChild(title);
        qcmPanel.appendChild(renderStudentQuestionForm(q));
        renderedQuestionIndex = q.index;
        resetQuestionTimer();
      }
      return;
    }

    // ended
    clearQuestionTimer();
    qcmPanel.innerHTML = '';
    qcmPanel.appendChild(title);
    const p = document.createElement('p');
    p.textContent = 'QCM terminé, merci !';
    qcmPanel.appendChild(p);
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'icon-btn';
    closeBtn.textContent = 'Fermer';
    closeBtn.addEventListener('click', () => {
      qcmDismissed = true;
      renderQcmPanel();
    });
    qcmPanel.appendChild(closeBtn);
    renderedQuestionIndex = null;
  }

  function renderOwnerTrigger(chapterTitle, count) {
    qcmPanel.innerHTML = '';
    const p = document.createElement('p');
    p.textContent = `QCM disponible pour « ${chapterTitle} » (${count} question${count > 1 ? 's' : ''})`;
    qcmPanel.appendChild(p);

    const label = document.createElement('label');
    label.className = 'qcm-seconds-label';
    label.textContent = 'Secondes par question ';
    const secondsInput = document.createElement('input');
    secondsInput.type = 'number';
    secondsInput.min = '3';
    secondsInput.max = '120';
    secondsInput.value = '10';
    secondsInput.className = 'wb-tb-input';
    label.appendChild(secondsInput);
    qcmPanel.appendChild(label);

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'primary-btn';
    btn.textContent = 'Préparer le QCM';
    btn.addEventListener('click', () => {
      const seconds = Math.min(120, Math.max(3, parseInt(secondsInput.value, 10) || 10));
      sendOp({ op: 'qcm_prepare', chapter_title: chapterTitle, seconds_per_question: seconds });
    });
    qcmPanel.appendChild(btn);
  }

  function renderOwnerQcmState() {
    qcmPanel.innerHTML = '';
    const title = document.createElement('h3');
    title.textContent = `QCM — ${qcmState.chapter_title}`;
    qcmPanel.appendChild(title);

    if (qcmState.status === 'pending') {
      const p = document.createElement('p');
      p.textContent = `${qcmState.question_count} question(s) — en attente de lancement`;
      qcmPanel.appendChild(p);

      const roster = document.createElement('ul');
      roster.className = 'qcm-roster';
      (qcmState.roster || []).forEach((m) => {
        const li = document.createElement('li');
        li.textContent = `${m.online ? '🟢' : '⚪'} ${m.username}`;
        roster.appendChild(li);
      });
      qcmPanel.appendChild(roster);

      const startBtn = document.createElement('button');
      startBtn.type = 'button';
      startBtn.className = 'primary-btn';
      startBtn.textContent = 'Lancer';
      startBtn.addEventListener('click', () => sendOp({ op: 'qcm_start' }));
      qcmPanel.appendChild(startBtn);

      const cancelBtn = document.createElement('button');
      cancelBtn.type = 'button';
      cancelBtn.className = 'icon-btn';
      cancelBtn.textContent = 'Annuler';
      cancelBtn.addEventListener('click', () => sendOp({ op: 'qcm_stop' }));
      qcmPanel.appendChild(cancelBtn);
      return;
    }

    if (qcmState.status === 'running') {
      const remaining = document.createElement('p');
      remaining.className = 'qcm-remaining';
      qcmPanel.appendChild(remaining);

      const table = document.createElement('table');
      table.className = 'qcm-progress-table';
      const headRow = document.createElement('tr');
      headRow.appendChild(document.createElement('th'));
      for (let i = 0; i < qcmState.question_count; i++) {
        const th = document.createElement('th');
        th.textContent = String(i + 1);
        headRow.appendChild(th);
      }
      table.appendChild(headRow);
      (qcmState.roster || []).forEach((m) => {
        const tr = document.createElement('tr');
        const nameTd = document.createElement('td');
        nameTd.textContent = `${m.online ? '🟢' : '⚪'} ${m.username}`;
        tr.appendChild(nameTd);
        for (let i = 0; i < qcmState.question_count; i++) {
          const td = document.createElement('td');
          const status = (m.progress || {})[String(i)];
          td.textContent = status === 'validated' ? '✓' : status === 'deferred' ? '⏭' : '·';
          td.className = `qcm-cell qcm-cell-${status || 'not_seen'}`;
          tr.appendChild(td);
        }
        table.appendChild(tr);
      });
      qcmPanel.appendChild(table);

      const stopBtn = document.createElement('button');
      stopBtn.type = 'button';
      stopBtn.className = 'icon-btn';
      stopBtn.textContent = 'Arrêter';
      stopBtn.addEventListener('click', () => sendOp({ op: 'qcm_stop' }));
      qcmPanel.appendChild(stopBtn);
      return;
    }

    // ended
    const p = document.createElement('p');
    p.textContent = 'QCM terminé.';
    qcmPanel.appendChild(p);
    const link = document.createElement('a');
    link.href = `/sessions/${sessionId}/qcm/${qcmState.qcm_session_id}/results`;
    link.className = 'primary-btn';
    link.textContent = 'Voir les résultats';
    qcmPanel.appendChild(link);
    const closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'icon-btn';
    closeBtn.textContent = 'Fermer';
    closeBtn.addEventListener('click', () => {
      qcmDismissed = true;
      renderQcmPanel();
    });
    qcmPanel.appendChild(closeBtn);
  }

  function renderQcmPanel() {
    if (isOwner) {
      qcmCountdown.hidden = true;
      if (qcmState) {
        qcmPanel.hidden = qcmDismissed;
        if (!qcmDismissed) renderOwnerQcmState();
      } else {
        const chapterTitle = currentChapterTitle();
        const count = chapterTitle ? qcmAvailable[chapterTitle] : undefined;
        if (count) {
          qcmPanel.hidden = false;
          renderOwnerTrigger(chapterTitle, count);
        } else {
          qcmPanel.hidden = true;
        }
      }
      return;
    }

    if (!qcmState || qcmDismissed) {
      qcmPanel.hidden = true;
      qcmCountdown.hidden = true;
      clearQuestionTimer();
      return;
    }
    qcmPanel.hidden = false;
    renderStudentQcmState();
  }

  setInterval(() => {
    if (isOwner && qcmState && qcmState.status === 'running') {
      const remainingEl = qcmPanel.querySelector('.qcm-remaining');
      if (remainingEl) {
        const ms = parseUtc(qcmState.ends_at).getTime() - Date.now();
        const totalSec = Math.max(0, Math.ceil(ms / 1000));
        remainingEl.textContent = `Temps restant : ${Math.floor(totalSec / 60)}:${String(totalSec % 60).padStart(2, '0')}`;
      }
    }
    if (!isOwner && qcmState && qcmState.status === 'running' && qcmState.ends_at) {
      const ms = parseUtc(qcmState.ends_at).getTime() - Date.now();
      const totalSec = Math.max(0, Math.ceil(ms / 1000));
      qcmCountdown.textContent = `${Math.floor(totalSec / 60)}:${String(totalSec % 60).padStart(2, '0')}`;
      qcmCountdown.hidden = false;
    } else {
      qcmCountdown.hidden = true;
    }
  }, 1000);

  function showOnly(el) {
    [pickerEl, waitingEl, slideWrapEl].forEach((e) => {
      e.hidden = e !== el;
    });
  }

  function renderTopicList() {
    topicListEl.innerHTML = '';
    topics.forEach((t) => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'cours-topic-btn';
      btn.textContent = t.title;
      btn.addEventListener('click', () => {
        sendOp({ op: 'select_topic', topic: t.filename });
      });
      li.appendChild(btn);
      topicListEl.appendChild(li);
    });
    if (topics.length === 0) {
      const li = document.createElement('li');
      li.textContent = 'Aucun cours disponible dans /cours.';
      topicListEl.appendChild(li);
    }
  }

  function buildTreeNode(node, parentUl) {
    const li = document.createElement('li');
    li.className = `cours-tree-item cours-tree-level-${node.level}`;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'cours-tree-btn';
    btn.textContent = node.title || '(sans titre)';

    const visited = visitedSlideIds.has(node.id);
    if (!isOwner && !visited) {
      btn.disabled = true;
      btn.classList.add('locked');
      btn.title = "Pas encore présentée par l'enseignant";
    }
    if (node.id === localSlideId) btn.classList.add('current');
    if (node.id === liveSlideId) btn.classList.add('live');
    btn.addEventListener('click', () => selectSlide(node.id));
    li.appendChild(btn);

    if (node.children && node.children.length > 0) {
      const childUl = document.createElement('ul');
      node.children.forEach((c) => buildTreeNode(c, childUl));
      li.appendChild(childUl);
    }
    parentUl.appendChild(li);
  }

  function renderTree() {
    treeEl.innerHTML = '';
    if (!content) return;
    const ul = document.createElement('ul');
    ul.className = 'cours-tree-root';
    content.tree.forEach((n) => buildTreeNode(n, ul));
    treeEl.appendChild(ul);
  }

  function renderSlide() {
    slideEl.innerHTML = '';
    if (!content) return;
    const slide = content.slides[String(localSlideId)];
    const h = document.createElement('h2');
    h.textContent = slide ? slide.title : '';
    slideEl.appendChild(h);
    if (slide && slide.body_html) {
      const body = document.createElement('div');
      body.className = 'cours-slide-body';
      // this HTML comes from server-side rendering of a course file in
      // /cours — authored and deployed by the teacher/admin, same trust
      // level as a Jinja template, never user-submitted at runtime
      body.innerHTML = slide.body_html;
      slideEl.appendChild(body);
    }
  }

  function updateBanner() {
    resyncBanner.hidden = localSlideId === liveSlideId;
  }

  function render() {
    renderQcmPanel();
    if (!content || forcePicker) {
      showOnly(isOwner ? pickerEl : waitingEl);
      if (isOwner) renderTopicList();
      return;
    }
    showOnly(slideWrapEl);
    docTitleEl.textContent = content.title;
    changeTopicBtn.hidden = !isOwner;
    renderTree();
    renderSlide();
    updateBanner();
  }

  function selectSlide(id) {
    if (!isOwner && !visitedSlideIds.has(id)) return;
    localSlideId = id;
    if (isOwner) {
      liveSlideId = id;
      visitedSlideIds.add(id);
      sendOp({ op: 'goto', slide_id: id });
    }
    render();
  }

  async function applyTopicChanged(data) {
    liveSlideId = data.current_slide_id;
    visitedSlideIds = new Set(data.visited_slide_ids || []);
    forcePicker = false;
    try {
      const res = await fetch(`/api/cours/${sessionId}/content`);
      if (!res.ok) throw new Error('failed to load course content');
      const full = await res.json();
      content = { title: full.title, tree: full.tree, slides: full.slides };
      qcmAvailable = full.qcm_available || {};
      localSlideId = liveSlideId;
    } catch (err) {
      content = null;
    }
    render();
  }

  changeTopicBtn.addEventListener('click', () => {
    forcePicker = true;
    render();
  });

  resyncBtn.addEventListener('click', () => {
    localSlideId = liveSlideId;
    render();
  });

  function connectWs() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws/cours/${sessionId}`);
    ws.addEventListener('close', () => setTimeout(connectWs, 2000));
    ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.op === 'topic_changed') {
        applyTopicChanged(msg);
      } else if (msg.op === 'slide_changed') {
        const wasFollowing = localSlideId === liveSlideId;
        liveSlideId = msg.current_slide_id;
        visitedSlideIds = new Set(msg.visited_slide_ids || []);
        if (wasFollowing) localSlideId = liveSlideId;
        render();
      } else if (msg.op === 'qcm_state') {
        if (msg.qcm_session_id !== lastSeenQcmId) {
          lastSeenQcmId = msg.qcm_session_id;
          qcmDismissed = false;
        }
        qcmState = msg;
        renderQcmPanel();
      } else if (msg.op === 'qcm_progress') {
        if (isOwner && qcmState && qcmState.qcm_session_id && qcmState.roster) {
          const member = qcmState.roster.find((m) => m.user_id === msg.user_id);
          if (member) {
            member.progress = { ...member.progress, [String(msg.question_index)]: msg.status };
            renderQcmPanel();
          }
        }
      }
    });
  }
  connectWs();

  render();
})();
