/* review-common.js
   Shared render/filter/expand engine for the three doc-review HTML pages
   (accuracy-v2-review.html, critique-plan-review.html, moonshots-review.html).
   Each page defines window.__DATA = [ {id, kind:'feature'|'suggestion', tag, title, blurb, explain, current, updated, poc, extra}, ... ]
   before loading this script. `extra` is optional raw HTML appended after the
   standard body (used for one-off interactive demos).
*/
(function () {
  const DATA = window.__DATA || [];
  const list = document.getElementById('list');
  const state = {}; // id -> {open:bool}

  function chipsFor(item) {
    const kindChip = item.kind === 'feature'
      ? `<span class="chip kind-feature">● Feature — POC</span>`
      : `<span class="chip kind-suggestion">● Suggestion</span>`;
    const tagChip = item.tag ? `<span class="chip layer">${item.tag}</span>` : '';
    return kindChip + tagChip;
  }

  function bodyFor(item) {
    let html = '';
    if (item.explain) {
      html += `<div class="lbl">What this means</div><div class="prose">${item.explain}</div>`;
    }
    if (item.kind === 'suggestion' && (item.current || item.updated)) {
      html += `<div class="lbl">Today vs. proposed</div>
        <div class="cvs">
          <div class="col today"><span class="tag">Today</span><div class="prose">${item.current || ''}</div></div>
          <div class="col v2"><span class="tag">Proposed (v2)</span><div class="prose">${item.updated || ''}</div></div>
        </div>`;
    }
    if (item.kind === 'feature' && item.poc) {
      html += `<div class="lbl">How it would look</div><div class="mock">${item.poc}</div>`;
    }
    if (item.extra) html += item.extra;
    return html;
  }

  function render() {
    const q = (document.getElementById('f-search').value || '').toLowerCase().trim();
    const fk = document.getElementById('f-kind').value;
    list.innerHTML = '';
    let shown = 0;
    DATA.forEach((item, i) => {
      if (fk && item.kind !== fk) return;
      if (q) {
        const hay = (item.title + ' ' + (item.blurb || '') + ' ' + (item.explain || '')).toLowerCase();
        if (!hay.includes(q)) return;
      }
      shown++;
      const open = !!(state[item.id] && state[item.id].open);
      const el = document.createElement('div');
      el.className = 'card' + (open ? ' open' : '');
      el.innerHTML = `
        <div class="head" data-toggle="${item.id}">
          <div class="num">${i + 1}</div>
          <div class="htext">
            <h2>${item.title}</h2>
            ${item.blurb ? `<div class="blurb">${item.blurb}</div>` : ''}
            <div class="meta">${chipsFor(item)}</div>
          </div>
          <div class="caret">▶</div>
        </div>
        <div class="body">${bodyFor(item)}</div>`;
      list.appendChild(el);
    });
    updateKPIs(shown);
  }

  function updateKPIs(shown) {
    const total = DATA.length;
    const feat = DATA.filter(d => d.kind === 'feature').length;
    const sugg = DATA.filter(d => d.kind === 'suggestion').length;
    const kTotal = document.getElementById('k-total');
    const kFeat = document.getElementById('k-feat');
    const kSugg = document.getElementById('k-sugg');
    if (kTotal) kTotal.textContent = total;
    if (kFeat) kFeat.textContent = feat;
    if (kSugg) kSugg.textContent = sugg;
  }

  document.addEventListener('click', (e) => {
    const tog = e.target.closest('[data-toggle]');
    if (tog) {
      const id = tog.dataset.toggle;
      state[id] = state[id] || {};
      state[id].open = !state[id].open;
      render();
      // re-run any per-card init hooks (e.g. audio demo buttons) after re-render
      if (window.__afterRender) window.__afterRender();
      return;
    }
  });

  document.getElementById('f-search').addEventListener('input', render);
  document.getElementById('f-kind').addEventListener('change', render);
  document.getElementById('expandAll').addEventListener('click', () => {
    const anyClosed = DATA.some(d => !(state[d.id] && state[d.id].open));
    DATA.forEach(d => { state[d.id] = state[d.id] || {}; state[d.id].open = anyClosed; });
    document.getElementById('expandAll').textContent = anyClosed ? 'Collapse all' : 'Expand all';
    render();
    if (window.__afterRender) window.__afterRender();
  });

  render();
  if (window.__afterRender) window.__afterRender();
})();
