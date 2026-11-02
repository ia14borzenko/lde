/* LDE Studio — клиент. Без сборки и фреймворков; MathLive — для формул. */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
// Встроенный append(null) вставляет текст «null» (так было в подвале «Истории и версий»); в интерфейсе
// пустые части (условие ? h(…) : null) всегда пропускаются — и в h(), и в append
for (const P of [Element.prototype, DocumentFragment.prototype]) {
  const append = P.append;
  P.append = function (...kids) { return append.apply(this, kids.filter((k) => k !== null && k !== undefined && k !== false)); };
}
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "html") e.innerHTML = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
};

const ICONS = {
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
  pdf: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/>',
  word: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="m8 12 1.5 6L12 13l2.5 5L16 12"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  theme: '<path d="M12 3a9 9 0 1 0 9 9 7 7 0 0 1-9-9z"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  sigma: '<path d="M18 7V4H6l6 8-6 8h12v-3"/>',
  calc: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h4"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
$$("[data-icon]").forEach((e) => (e.innerHTML = icon(e.dataset.icon) + e.innerHTML));

/* Роль: автор (Studio со своим проектом) или рецензент (та же Studio по ссылке: «сервер» — в браузере,
   lde-server/web/static/rbackend.js, данные — расшифрованный снимок документа и копия рецензента). */
const REVIEWER = document.documentElement.dataset.ldeRole === "reviewer";
const BACK = window.LDE_BACKEND || null;

/* --------------------------------------------------------------- api ---- */
async function api(path, body) {
  if (BACK) {
    try { return await BACK.api(path, body); } catch (e) { const err = new Error(e.message || String(e)); err.status = 400; throw err; }
  }
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* пусто */ }
  if (!r.ok) { const err = new Error(data.error || `ошибка ${r.status}`); err.data = data; err.status = r.status; throw err; }
  return data;
}
function toast(msg, opts = {}) {
  const t = h("div", { class: "toast" + (opts.err ? " err" : "") }, msg);
  if (opts.action) t.append(h("button", { onclick: () => { opts.action.fn(); t.remove(); } }, opts.action.label));
  $("#toasts").append(t);
  setTimeout(() => t.remove(), opts.ms || (opts.err ? 7000 : 3200));
}
const fail = (e) => toast(e.message || String(e), { err: true });

/* ------------------------------------------------------------- state ---- */
const S = { state: null, tab: "outline", zoom: 1, cur: null, editor: null, pages: [], hl: null, dirty: false };

/* Тема и масштаб хранятся на сервере (~/.config/lde/prefs.json): Studio каждый раз открывается
   на новом порту, а хранилище браузера привязано к порту — оно лишь ускоряет первую отрисовку. */
function setTheme(t, save = true) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("lde-theme", t); } catch (e) { /* без хранилища */ }
  if (save) api("/api/prefs", { theme: t }).catch(() => {});
}
(() => {
  let t = null;
  try { t = localStorage.getItem(REVIEWER ? "lde-review-theme" : "lde-theme"); } catch (e) { /* */ }
  if (REVIEWER && !t) t = "light";     // документ — как бумага; тёмная тема только по кнопке
  setTheme(t || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"), false);
  try { S.zoom = parseFloat(localStorage.getItem("lde-zoom")) || 1; } catch (e) { /* */ }
})();
function applyPrefs(p) {
  if (!p || S.prefsApplied) return;
  S.prefsApplied = true;
  if (p.theme && p.theme !== document.documentElement.dataset.theme) setTheme(p.theme, false);
  if (p.zoom && Math.abs(p.zoom - S.zoom) > 0.001) { S.zoom = p.zoom; try { localStorage.setItem("lde-zoom", S.zoom); } catch (e) { /* */ } }
}

async function refresh() {
  try { S.state = await api("/api/state"); } catch (e) { return fail(e); }
  applyPrefs(S.state.prefs);
  const key = S.state.project ? S.state.project.root + "#" + S.state.project.doc : "";
  if (key !== S.viewKey) { S.viewKey = key; S.viewRestored = false; S.cur = null; S.hl = null; $("#pages").innerHTML = ""; S.pages = []; }
  render();
}

/* ------------------------------------------- где закрыли — там и откроется ---- */
function currentView() {
  const box = $("#pages"), kids = [...box.children];
  if (!kids.length) return null;
  const top = box.scrollTop + 8;
  let d = kids[0];
  for (const k of kids) { if (k.offsetTop <= top) d = k; else break; }
  return { page: +d.dataset.n, frac: Math.max(0, Math.min(1, (top - d.offsetTop) / (d.offsetHeight || 1))), zoom: S.zoom };
}
let viewTimer = null;
function saveViewSoon() {
  if (!S.viewRestored || !S.state || !S.state.project) return;
  clearTimeout(viewTimer);
  viewTimer = setTimeout(() => { const v = currentView(); if (v) api("/api/view", v).catch(() => {}); }, 700);
}
function restoreView() {
  if (S.viewRestored || !S.pages.length) return;
  S.viewRestored = true;
  const v = S.state && S.state.view;
  if (!v || !v.page) return;
  const d = $(`.page[data-n="${Math.min(v.page, S.pages.length)}"]`);
  if (d) $("#pages").scrollTop = d.offsetTop + (v.frac || 0) * d.offsetHeight - 8;
}
$("#pages").addEventListener("scroll", saveViewSoon, { passive: true });
addEventListener("pagehide", () => {
  const v = S.viewRestored && currentView();
  if (v) navigator.sendBeacon("/api/view", new Blob([JSON.stringify(v)], { type: "application/json" }));
});
let refreshTimer = null;
const refreshSoon = () => { clearTimeout(refreshTimer); refreshTimer = setTimeout(refresh, 250); };

function render() {
  const st = S.state;
  document.body.classList.toggle("no-project", !st.project);
  if (!st.project) return renderStart();
  $("#start").hidden = true;
  $("#main").hidden = false;
  const docs = st.project.documents || [];
  $("#proj-title").textContent = st.project.title;
  $("#proj-doc").textContent = docs.length > 1 ? (docs.find((d) => d.key === st.project.doc) || {}).name || "" : "";
  $("#proj-doc").hidden = docs.length < 2;
  document.title = `${st.project.title} — LDE Studio`;
  renderPill();
  renderSide();
  renderPages(st.pages);
  renderBuild(st.build);
}

function renderPill() {
  const st = S.state, p = $("#status-pill");
  if (REVIEWER) { p.hidden = true; $("#changes-btn").hidden = true; return; }
  p.hidden = false;
  p.className = "pill";
  const calcRunning = st.calcs.find((c) => c.running);
  const staleN = st.status.stale.reduce((a, s) => a + s.figures.length + s.tables.length + s.values, 0);
  if (calcRunning) { p.classList.add("warn"); p.innerHTML = `<span class="spin"></span> Пересчёт: ${esc(calcRunning.title)}`; }
  else if (st.status.stale.length) { p.classList.add("warn"); p.innerHTML = `<span class="dot"></span> Устарело: ${staleN || st.status.stale.length} → Обновить`; }
  else if (st.status.errors) { p.classList.add("err"); p.innerHTML = `<span class="dot"></span> Ошибок: ${st.status.errors}`; }
  else if (st.status.warnings) { p.classList.add("warn"); p.innerHTML = `<span class="dot"></span> Замечаний: ${st.status.warnings}`; }
  else { p.classList.add("ok"); p.innerHTML = `<span class="dot"></span> Всё актуально`; }
  const rv = $("#review-btn");
  rv.textContent = st.review ? `Примечания: ${st.review}` : "Примечания";
  rv.classList.toggle("warn", !!st.review);
  const c = $("#changes-btn");
  c.hidden = !st.changes;
  c.textContent = `Что изменилось: ${st.changes}`;
}

function renderBuild(b) {
  const e = $("#build-state");
  if (REVIEWER) { e.textContent = (S.state && S.state.reviewer && S.state.reviewer.saveState) || ""; return; }
  if (!b) return;
  if (b.state === "building") e.innerHTML = '<span class="spin"></span> Обновляю PDF…';
  else if (b.state === "error") { e.textContent = "PDF не обновлён"; }
  else if (b.state === "ok") e.textContent = `PDF обновлён за ${String(b.duration).replace(".", ",")} с`;
  else e.textContent = "";
}

/* -------------------------------------------------------- start screen ---- */
function renderStart() {
  $("#main").hidden = true;
  $("#status-pill").hidden = true;
  const st = $("#start");
  st.hidden = false;
  st.innerHTML = "";
  const file = h("input", { type: "file", accept: ".docx", hidden: true, onchange: (e) => importDocx(e.target.files[0]) });
  st.append(h("div", { class: "start-card" },
    h("h1", {}, "Отчёт в LDE"),
    h("div", { class: "muted" }, "Начните с нового отчёта или перенесите существующий из Word."),
    h("div", { class: "start-actions" },
      h("button", { class: "btn primary", id: "start-new", onclick: newProject }, "Новый отчёт"),
      h("button", { class: "btn", id: "start-import", onclick: () => file.click() }, "Импортировать из Word"), file),
    h("div", {}, h("a", { href: "#", onclick: (e) => { e.preventDefault(); openProjectDialog(); } }, "Открыть существующий проект…")),
    S.state.recent && S.state.recent.length ? h("div", { class: "recent" }, h("div", { class: "group-title" }, "Недавние"),
      S.state.recent.map((r) => h("div", { class: "item", onclick: () => api("/api/open-project", { path: r.path }).then(refresh, fail) },
        h("span", { class: "t" }, r.name), h("span", { class: "muted small" }, r.path)))) : null));
}
function openProjectDialog() {
  dialog("Открыть проект", (body, foot, close) => {
    const inp = h("input", { class: "inp", placeholder: "/путь/к/папке с lde.toml" });
    body.append(h("div", { class: "field" }, h("label", {}, "Папка проекта"), inp));
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"),
      h("button", { class: "btn primary", onclick: () => api("/api/open-project", { path: inp.value.trim() }).then(() => { close(); refresh(); }, fail) }, "Открыть"));
    setTimeout(() => inp.focus(), 40);
  });
}
function newProject() {
  dialog("Новый отчёт", (body, foot, close) => {
    const inp = h("input", { class: "inp", value: "Новый отчёт" });
    body.append(h("div", { class: "field" }, h("label", {}, "Название (папка в Документы/LDE)"), inp));
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"),
      h("button", { class: "btn primary", onclick: () => api("/api/new-project", { name: inp.value }).then(() => { close(); refresh(); }, fail) }, "Создать"));
    setTimeout(() => inp.select(), 50);
  });
}
async function importDocx(f) {
  if (!f) return;
  toast(`Импортирую ${f.name}…`, { ms: 8000 });
  const r = await fetch(`/api/upload?kind=docx&name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
  const d = await r.json();
  if (!r.ok) return fail(new Error(d.error));
  toast(`Импортировано. Приблизительно: ${d.approx}, не перенесено: ${d.lost} — подробности в import/report.md`, { ms: 8000 });
  refresh();
}

/* ------------------------------------------------------------- sidebar ---- */
$$("#tabs button").forEach((b) => b.addEventListener("click", () => {
  S.tab = b.dataset.tab;
  $$("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
  renderSide();
}));
$("#side-search").addEventListener("input", () => renderSide());

function renderSide() {
  const st = S.state, list = $("#side-list"), foot = $("#side-foot");
  const q = $("#side-search").value.trim().toLowerCase();
  const m = (...f) => !q || f.some((x) => (x || "").toString().toLowerCase().includes(q));
  list.innerHTML = "";
  foot.innerHTML = "";
  const sel = (uid) => (S.cur && S.cur.uid === uid ? " sel" : "");
  if (S.tab === "outline") {
    st.outline.filter((o) => m(o.text, o.number)).forEach((o) => list.append(
      h("div", { class: `item l${o.level}${sel(o.uid)}`, onclick: () => openElement(o.uid, true) },
        h("span", { class: "num" }, o.number), o.latex !== null && o.latex !== undefined ? h("span", { class: "t mathline", html: mathHTML(o.latex) }) : h("span", { class: "t", title: o.text }, o.text))));
  } else if (S.tab === "objects") {
    const groups = { figure: "Рисунки", table: "Таблицы", formula: "Формулы" };
    for (const [k, title] of Object.entries(groups)) {
      const items = st.objects.filter((o) => o.kind === k && m(o.title, o.text));
      if (!items.length) continue;
      list.append(h("div", { class: "group-title" }, title));
      items.forEach((o) => list.append(h("div", { class: "item" + sel(o.uid), "data-uid": o.uid, "data-kind": o.kind, onclick: () => openElement(o.uid, true) },
        h("span", { class: "num" }, o.title.replace(/^[^\d(А-ЯЁ]*(?=[\d(]|[А-Я]\.)/, "").replace(/^[^\d(]+ /, "")), o.latex !== null && o.latex !== undefined ? h("span", { class: "t mathline", html: mathHTML(o.latex) }) : h("span", { class: "t", title: o.text }, o.text))));
    }
    foot.append(h("div", { class: "row" },
      h("button", { class: "btn", onclick: () => addBlock("figure") }, "+ Рисунок"),
      h("button", { class: "btn", onclick: () => addBlock("table") }, "+ Таблица"),
      h("button", { class: "btn", onclick: () => addBlock("formula") }, "+ Формула")));
  } else if (S.tab === "quantities") {
    st.quantities.filter((x) => m(x.key, x.meaning)).forEach((x) => {
      const prev = h("span", { class: "num", html: mathHTML(x.latex) });
      list.append(h("div", { class: "item", onclick: () => openQuantity(x.key) }, prev,
        h("span", { class: "t", title: x.meaning }, x.meaning || h("span", { class: "muted" }, "смысл не указан")),
        h("span", { class: "muted small" }, x.unit)));
    });
    foot.append(h("button", { class: "btn", onclick: () => openQuantity("") }, "+ Величина"));
  } else if (S.tab === "calcs") {
    st.calcs.filter((c) => m(c.title, c.key)).forEach((c) => {
      const badge = { ok: ["ok", "актуален"], stale: ["warn", "устарел"], never: ["warn", "не запускался"], error: ["err", "ошибка"] }[c.state];
      list.append(h("div", { class: "item", onclick: () => openCalc(c.key) },
        h("span", { class: "t", title: c.title }, c.title),
        c.running ? h("span", { class: "spin" }) : h("span", { class: "badge " + (c.failure ? "err" : badge[0]) }, c.failure ? "упал" : badge[1])));
    });
    if (!st.calcs.length) list.append(h("div", { class: "muted small", style: "padding:8px" }, "Расчётов нет. Их добавляет агент: lde new calc …"));
  } else if (S.tab === "review") {
    rvSide(list, foot);
  } else if (S.tab === "mine") {
    reviewerMine(list);
  } else if (S.tab === "sources") {
    st.sources.filter((s) => m(s.key, s.text)).forEach((s) => list.append(
      h("div", { class: "item", onclick: () => openSource(s.key) },
        h("span", { class: "num" }, s.n ? `[${s.n}]` : "—"), h("span", { class: "t", title: s.text }, s.text))));
    foot.append(h("button", { class: "btn", onclick: () => openSource("") }, "+ Источник"));
  }
}

/* --------------------------------------------------------------- pages ---- */
function pageWidth() {
  const avail = $("#pages").clientWidth - 90;
  return Math.max(320, Math.min(avail, 900) * S.zoom);
}
function renderPages(pages) {
  const box = $("#pages");
  $("#doc-empty").hidden = !!(pages && pages.length);
  if (!pages || !pages.length) {
    const b = S.state.build;
    $("#doc-empty").innerHTML = b && b.state === "error" ? `<div>PDF не собран.<br><button class="btn" onclick="showBuildErrors()">Почему?</button></div>` : '<div><span class="spin"></span> Собираю PDF…</div>';
    return;
  }
  const w = pageWidth();
  const pxw = Math.ceil((w * devicePixelRatio) / 100) * 100;
  const old = new Map($$(".page", box).map((d) => [+d.dataset.n, d]));
  pages.forEach((p) => {
    let d = old.get(p.n);
    const hgt = (w * p.h) / p.w;
    if (!d) {
      d = h("div", { class: "page", "data-n": p.n }, h("img", { loading: "lazy", alt: `страница ${p.n}`, draggable: "false" }), h("span", { class: "pnum" }, String(p.n)));
      d.addEventListener("click", (e) => clickPage(e, d));
      box.append(d);
    }
    old.delete(p.n);
    d.style.width = w + "px";
    d.style.height = hgt + "px";
    d.dataset.w = p.w; d.dataset.h = p.h;
    const src = BACK ? BACK.pageSrc(p.n) : `/api/page/${p.n}?w=${pxw}&h=${p.hash}`;
    const img = d.querySelector("img");
    if (img.getAttribute("src") !== src) {
      // подменяем, когда новая картинка загрузилась — без мигания и с той же прокруткой
      const pre = new Image();
      pre.onload = () => { img.src = src; };
      pre.src = src;
      if (!img.getAttribute("src")) img.src = src;
    }
  });
  old.forEach((d) => d.remove());
  const kids = [...box.children];
  if (kids.some((d, i) => i && +kids[i - 1].dataset.n > +d.dataset.n)) {
    const top = box.scrollTop;
    kids.sort((a, b) => a.dataset.n - b.dataset.n).forEach((d) => box.append(d));
    box.scrollTop = top;
  }
  S.pages = pages;
  drawHighlight();
  $("#zoom-val").textContent = Math.round(S.zoom * 100) + "%";
  restoreView();
}
function zoom(f) {
  const v = currentView();
  S.zoom = Math.max(0.5, Math.min(2.5, S.zoom * f));
  try { localStorage.setItem("lde-zoom", S.zoom); } catch (e) { /* */ }
  renderPages(S.pages);
  if (v) { const d = $(`.page[data-n="${v.page}"]`); if (d) $("#pages").scrollTop = d.offsetTop + v.frac * d.offsetHeight - 8; }
  clearTimeout(S.zoomT);
  S.zoomT = setTimeout(() => api("/api/prefs", { zoom: S.zoom }).catch(() => {}), 500);
  saveViewSoon();
}
$("#zoom-in").onclick = () => zoom(1.15);
$("#zoom-out").onclick = () => zoom(1 / 1.15);
window.addEventListener("resize", () => { renderPages(S.pages); if (S.rv && S.rv.right) { renderPagesR(); if (S.rv.i >= 0) rvSelect(S.rv.i, true); } });

async function clickPage(e, d) {
  const r = d.getBoundingClientRect();
  const x = ((e.clientX - r.left) / r.width) * +d.dataset.w;
  const y = ((e.clientY - r.top) / r.height) * +d.dataset.h;
  try {
    const res = await api(`/api/locate?page=${d.dataset.n}&x=${x.toFixed(1)}&y=${y.toFixed(1)}`);
    if (!res.uid) return;
    S.hl = res.rects;
    if (res.uid.startsWith("gen:")) { drawHighlight(); return openGenerated(res.uid); }
    drawHighlight();
    openElement(res.uid, false);
  } catch (err) { fail(err); }
}
function drawHighlight() {
  $$("#pages .hl, .rv-mk, .rv-badge").forEach((x) => x.remove());
  if (REVIEWER && S.state && S.state.reviewer) {
    // пометки рецензента на полях: что он исправил и где оставил замечания
    for (const m of S.state.reviewer.marks) m.rects.forEach((r, k) => {
      const d = $(`.page[data-n="${r.page}"]`);
      if (!d) return;
      const H = +d.dataset.h, ch = d.clientHeight, top = (r.top / H) * ch, hh = Math.max(12, ((r.bottom - r.top) / H) * ch);
      if (m.edit) d.append(h("div", { class: "rv-mk edit", style: `top:${top}px;height:${hh}px` }));
      if (m.cm) d.append(h("div", { class: "rv-mk cm", style: `top:${top}px;height:${hh}px` }));
      if (k === 0) d.append(h("div", { class: "rv-badge " + (m.edit ? "edit" : "cm"), style: `top:${top}px` }, m.edit ? "исправлено" : "замечание"));
    });
  }
  if (!S.hl) return;
  for (const r of S.hl) {
    const d = $(`.page[data-n="${r.page}"]`);
    if (!d) continue;
    const H = +d.dataset.h, ch = d.clientHeight;
    d.append(h("div", { class: "hl", style: `top:${(r.top / H) * ch - 3}px;height:${Math.max(14, ((r.bottom - r.top) / H) * ch + 6)}px` }));
  }
}
function scrollToRects(rects) {
  if (!rects || !rects.length) return;
  const r = rects[0], d = $(`.page[data-n="${r.page}"]`);
  if (!d) return;
  const box = $("#pages");
  const y = d.offsetTop + (r.top / +d.dataset.h) * d.clientHeight - 120;
  box.scrollTo({ top: Math.max(0, y) });
}

/* -------------------------------------------------------- chip editor ---- */
function mathHTML(latex) {
  try { return MathLive.convertLatexToMarkup(mtShow(latex || ""), { defaultMode: "inline-math" }); } catch (e) { return esc(latex); }
}
/* Размеры как в MathType (и как в PDF LDE): числитель и знаменатель дроби — кеглем строки, где
   стоит дробь (в тексте, скобках, матрице — полного размера), в индексе — размера индекса.
   MathLive следует TeX и уменьшает их, поэтому для показа \frac вне индексов → \dfrac, при
   сохранении — обратно (в исходнике остаётся \frac). Макрос в MathLive не годится: дробь
   становится нередактируемой. */
function mtShow(t) {
  let out = "", pendingScript = false;
  const stack = [];
  for (let i = 0; i < t.length;) {
    const c = t[i];
    if (c === "\\") {
      const m = /^\\([A-Za-z]+)/.exec(t.slice(i));
      if (m) {
        const inScript = pendingScript || (stack.length && stack[stack.length - 1]);
        out += m[1] === "frac" && !inScript ? "\\dfrac" : m[0];
        pendingScript = false;
        i += m[0].length;
        continue;
      }
      out += t.slice(i, i + 2); i += 2; pendingScript = false; continue;
    }
    if (c === "^" || c === "_") { pendingScript = true; out += c; i++; continue; }
    if (c === "{") { stack.push(pendingScript || (stack.length && stack[stack.length - 1]) || false); pendingScript = false; out += c; i++; continue; }
    if (c === "}") { stack.pop(); out += c; i++; continue; }
    if (c !== " ") pendingScript = false;
    out += c; i++;
  }
  return out;
}
const mtSave = (t) => t.replace(/\\dfrac(?![A-Za-z])/g, "\\frac");
// те же размеры индексов внутри поля MathLive (его разметка — в теневом DOM, общие стили туда не доходят)
const MT_SCRIPT_CSS = '[style*="font-size: 70%"]{font-size:58%!important}[style*="font-size: 71.43%"]{font-size:72.41%!important}[style*="font-size: 50%"]{font-size:42%!important}';
function mtField(mf) {
  const add = () => { if (mf.shadowRoot && !mf.shadowRoot.querySelector("style[data-lde]")) mf.shadowRoot.append(h("style", { "data-lde": "1" }, MT_SCRIPT_CSS)); };
  add();
  mf.addEventListener("mount", add);
  return mf;
}
// как model.esc_text: экранировать только то, что иначе станет разметкой LDE
function escText(s) {
  return s.replace(/\\/g, "\\\\").replace(/[{}$]/g, (m) => "\\" + m).replace(/\*/g, "\\*").replace(/\[@/g, "\\[@")
    .replace(/(?<![\p{L}\p{N}_@])@(?=(fig|tab|eq|sec|app):)/gu, "\\@");
}
async function copyText(s, what) {
  try {
    await navigator.clipboard.writeText(s);
  } catch (e) {
    const ta = h("textarea", { style: "position:fixed;left:-9999px;top:0" });
    ta.value = s;
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    if (!ok) return toast("Не удалось скопировать", { err: true });
  }
  toast(what);
}
function copySrcButton(get) {
  return h("button", { class: "btn copy-src", title: "Скопировать содержимое поля как исходный текст LDE — со вставками {величина}, {=расчёт.число}, @ссылка, $формула$; выделение в поле не меняется",
    onmousedown: (e) => e.preventDefault(), onclick: () => copyText(get(), "Исходный текст скопирован") }, "Копировать исходник");
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

class ChipEditor {
  constructor(tokens, opts = {}) {
    this.el = h("div", { class: "chips" + (opts.small ? " small" : ""), contenteditable: "true", spellcheck: "true", lang: "ru" });
    this.opts = opts;
    this.range = null;
    this.initial = tokens || [];
    for (const t of tokens || []) this.el.append(t.t === "chip" ? this.chip(t) : document.createTextNode(t.v));
    this.el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.ctrlKey) { e.preventDefault(); }
      if ((e.ctrlKey || e.metaKey) && !e.altKey && ["b", "i", "и", "ш"].includes(e.key.toLowerCase())) {
        e.preventDefault();
        this.wrap(["b", "и"].includes(e.key.toLowerCase()) ? "b" : "i");
      }
    });
    this.el.addEventListener("paste", (e) => {
      e.preventDefault();
      const t = (e.clipboardData.getData("text/plain") || "").replace(/\s*\n\s*/g, " ");
      document.execCommand("insertText", false, t);
    });
    const keep = () => { const s = getSelection(); if (s.rangeCount && this.el.contains(s.anchorNode)) this.range = s.getRangeAt(0).cloneRange(); };
    this.el.addEventListener("keyup", keep);
    this.el.addEventListener("mouseup", keep);
    this.el.addEventListener("input", () => { keep(); S.dirty = true; });
  }
  chip(t) {
    const kind = t.kind || "x";
    const c = h("span", { class: `chip ${kind}${t.bad ? " bad" : ""}`, contenteditable: "false", "data-src": t.src, "data-kind": kind, title: t.title || "" });
    if ((kind === "q" || kind === "math") && t.latex !== undefined) c.innerHTML = mathHTML(t.latex);
    if (kind === "math" && t.latex !== undefined) c.dataset.ml = t.latex;
    else c.textContent = t.text || t.src;
    c.addEventListener("click", (e) => { e.stopPropagation(); this.opts.onChip && this.opts.onChip(c, this); });
    return c;
  }
  tokens() {
    const out = [];
    const push = (v) => { if (out.length && out[out.length - 1].t === "text") out[out.length - 1].v += v; else out.push({ t: "text", v }); };
    const walk = (n) => {
      for (const c of n.childNodes) {
        if (c.nodeType === 3) push(c.nodeValue.replace(/\u00a0(?!\S)/g, " "));
        else if (c.classList && c.classList.contains("chip")) out.push({ t: "chip", src: c.dataset.src });
        else if (c.nodeName === "BR") push(" ");
        else walk(c);
      }
    };
    walk(this.el);
    return out;
  }
  // Исходник LDE того, что сейчас в поле (вставки — как в файле: {R}, {=расчёт.ключ}, @fig:…, $…$);
  // неизменённый текст — как был в исходнике, новый — экранирован, как при сохранении (model.detokenize)
  source() {
    const pool = this.initial.filter((t) => t.t === "text");
    return this.tokens().map((t) => {
      if (t.t === "chip") return t.src;
      const i = pool.findIndex((o) => o.v === t.v);
      return i >= 0 && pool[i].src !== undefined ? pool.splice(i, 1)[0].src : escText(t.v);
    }).join("");
  }
  wrap(kind) {
    // выделенные слова → **жирный** / *курсив* (чип; щелчок по нему снимает выделение)
    const s = getSelection();
    let r = s.rangeCount && this.el.contains(s.anchorNode) ? s.getRangeAt(0) : this.range;
    if (!r || r.collapsed || !this.el.contains(r.commonAncestorContainer)) return toast("Выделите слова в тексте");
    const frag = r.cloneContents();
    if (frag.querySelector(".chip")) return toast("Выделите только текст, без вставок");
    let text = r.toString();
    const lead = text.match(/^\s*/)[0], tail = text.match(/\s*$/)[0];
    text = text.trim();
    if (!text) return;
    const m = kind === "b" ? "**" : "*";
    const esc = text.replace(/\\/g, "\\\\").replace(/[{}$*]/g, (x) => "\\" + x).replace(/\[@/g, "\\[@");
    const c = this.chip({ t: "chip", src: m + esc + m, kind, text, title: kind === "b" ? "полужирный — щелчок снимает" : "курсив — щелчок снимает" });
    r.deleteContents();
    const tailN = document.createTextNode(tail), leadN = document.createTextNode(lead);
    r.insertNode(tailN); r.insertNode(c); r.insertNode(leadN);
    const nr = document.createRange(); nr.setStart(tailN, tail.length); nr.collapse(true);
    s.removeAllRanges(); s.addRange(nr);
    this.range = nr.cloneRange();
    this.el.focus();
    S.dirty = true;
  }
  insertChip(t) {
    const c = this.chip(t);
    let r = this.range;
    if (!r || !this.el.contains(r.startContainer)) { r = document.createRange(); r.selectNodeContents(this.el); r.collapse(false); }
    r.deleteContents();
    r.insertNode(c);
    const sp = document.createTextNode(" ");
    c.after(sp);
    const nr = document.createRange(); nr.setStart(sp, 1); nr.collapse(true);
    const s = getSelection(); s.removeAllRanges(); s.addRange(nr);
    this.range = nr.cloneRange();
    this.el.focus();
    S.dirty = true;
  }
}

/* ------------------------------------------------------------ panel ---- */
function openPanel(kind, title) {
  $("#panel").hidden = false;
  $("#rs-right").hidden = false;
  $("#panel-kind").textContent = kind;
  $("#panel-title").textContent = title;
  $("#panel-body").innerHTML = "";
  $("#panel-foot").innerHTML = "";
  S.dirty = false;
  requestAnimationFrame(() => renderPages(S.pages));
  return [$("#panel-body"), $("#panel-foot")];
}
function closePanel() {
  if (S.dirty && !confirm("Закрыть без сохранения?")) return;
  $("#panel").hidden = true;
  $("#rs-right").hidden = true;
  S.cur = null; S.editor = null; S.hl = null; S.dirty = false;
  renderPages(S.pages);
  renderSide();
}
$("#panel-close").onclick = closePanel;

async function openElement(uid, scroll) {
  if (S.dirty && S.cur && S.cur.uid !== uid && !confirm("Есть несохранённая правка. Перейти без сохранения?")) return;
  let d;
  try { d = await api(`/api/element?uid=${encodeURIComponent(uid)}`); } catch (e) { return fail(e); }
  S.cur = d;
  const [body, foot] = openPanel(d.kind_ru, d.title);
  if (scroll) { S.hl = d.rects; requestAnimationFrame(() => requestAnimationFrame(() => { renderPages(S.pages); scrollToRects(d.rects); })); }
  const saveBtn = h("button", { class: "btn primary", id: "save-btn", title: "Сохранить (Ctrl+S)", onclick: () => saveCurrent() }, "Сохранить");
  const more = REVIEWER ? h("button", { class: "btn", id: "cm-btn", title: "Замечание к этому месту (если выделить слова — к ним)", onclick: () => reviewerComment(d) }, "Замечание…")
    : h("button", { class: "btn", onclick: (e) => moreMenu(e, d) }, "Ещё ▾");
  foot.append(saveBtn, h("button", { class: "btn", onclick: closePanel }, "Отмена"), h("div", { class: "spacer" }), more);
  S.editor = null;
  if (REVIEWER && (d.kind === "group" || d.readonly || !d.editable)) {
    reviewerReadonly(d, body, foot, saveBtn);
  } else if (["para", "heading"].includes(d.kind)) {
    const ed = new ChipEditor(d.tokens, { onChip: chipClick });
    S.editor = { type: "tokens", ed };
    body.append(ed.el, insertToolbar(ed));
    if (d.kind === "heading") body.append(h("div", { class: "hint" }, d.unnumbered ? "Структурный заголовок без номера." : "Номер раздела ставится автоматически."));
    setTimeout(() => { ed.el.focus(); placeEnd(ed.el); }, 30);
  } else if (d.kind === "formula") {
    body.append(...formulaEditor(d.latex, d));
  } else if (d.kind === "figure") {
    const ed = new ChipEditor(d.tokens, { onChip: chipClick, small: true });
    S.editor = { type: "tokens", ed };
    const src = (d.subfigs && d.subfigs.length ? d.subfigs[0] : d.path);
    body.append(h("div", { class: "field" }, h("label", {}, `Подпись: «Рисунок ${d.number} — …»`), ed.el), insertToolbar(ed));
    body.append(h("img", { class: "fig-prev", src: BACK ? BACK.fileSrc(src) : `/api/file?path=${encodeURIComponent(src)}&t=${Date.now()}`, alt: "" }));
    if (REVIEWER) { /* рисунок у рецензента — только подпись */ } else if (d.calc) {
      const c = S.state.calcs.find((x) => x.key === d.calc);
      body.append(h("div", { class: "row" }, h("div", { class: "hint" }, `Рисунок — результат расчёта «${c ? c.title : d.calc}».`),
        h("button", { class: "btn", style: "flex:none", onclick: () => openCalc(d.calc) }, "Параметры расчёта")));
    } else {
      const file = h("input", { type: "file", accept: ".png,.jpg,.jpeg,.pdf", hidden: true, onchange: (e) => replaceFigure(d, e.target.files[0]) });
      body.append(h("div", { class: "row" }, h("div", { class: "hint" }, d.path), h("button", { class: "btn", style: "flex:none", onclick: () => file.click() }, "Заменить файл…"), file));
    }
    if (!REVIEWER) {
      const wv = Math.round(parseFloat(d.width || "0.9") * 100);
      const lab = h("span", { class: "muted small" }, wv + "%");
      const rng = h("input", { type: "range", min: 20, max: 100, step: 5, value: wv, oninput: (e) => { lab.textContent = e.target.value + "%"; S.dirty = true; } });
      S.editor.width = rng;
      body.append(h("div", { class: "field" }, h("label", {}, "Ширина рисунка"), h("div", { class: "row" }, rng, lab)));
    }
  } else if (d.kind === "conflict") {
    conflictEditor(d, body, foot);
  } else if (d.kind === "table") {
    body.append(...tableEditor(d));
  } else {
    sourceMode(d);
  }
  if ((d.style_keys || []).length) body.append(styleSection(d));
  renderSide();
}

/* ------------------------------------------------ ручное оформление ---- */
const STYLE_UI = {
  align: { label: "Выравнивание", type: "select", opts: [["", "как обычно"], ["justify", "по ширине"], ["left", "по левому краю"], ["center", "по центру"], ["right", "по правому краю"]] },
  indent: { label: "Отступ первой строки, см", type: "num", step: 0.25, ph: "как обычно" },
  left: { label: "Отступ слева, см", type: "num", step: 0.25, ph: "0" },
  right: { label: "Отступ справа, см", type: "num", step: 0.25, ph: "0" },
  before: { label: "Интервал перед, pt", type: "num", step: 1, ph: "0", tip: "Добавка к обычному интервалу; можно отрицательную" },
  after: { label: "Интервал после, pt", type: "num", step: 1, ph: "0", tip: "Добавка к обычному интервалу; можно отрицательную" },
  line: { label: "Межстрочный, ×", type: "num", step: 0.05, ph: "как обычно (1,5)", tip: "Множитель, как в Word: 1; 1,15; 1,5; 2" },
  size: { label: "Размер шрифта, pt", type: "num", step: 0.5, ph: "как обычно" },
  bold: { label: "Полужирный", type: "tri" },
  italic: { label: "Курсив", type: "tri" },
  newpage: { label: "С новой страницы", type: "check" },
  keep: { label: "Не отрывать от следующего", type: "check" },
};
function styleSection(d) {
  const box = h("div", { class: "style-box" + (d.style_on ? " on" : ""), id: "style-box" });
  const fields = h("div", { class: "style-fields" });
  const ctl = {};
  const cb = h("input", { type: "checkbox", id: "style-on", checked: d.style_on || undefined });
  const status = h("span", { class: "muted small" });
  let timer = null;
  const collect = () => {
    const st = {};
    for (const [k, c] of Object.entries(ctl)) {
      const u = STYLE_UI[k];
      if (u.type === "check") { if (c.checked) st[k] = true; }
      else if (u.type === "tri") { if (c.value) st[k] = c.value === "yes"; }
      else if (c.value !== "") st[k] = u.type === "num" ? parseFloat(String(c.value).replace(",", ".")) : c.value;
    }
    return st;
  };
  const send = async (style) => {
    status.textContent = "применяю…";
    try {
      const r = await api("/api/element/style", { uid: d.uid, base: d.base, style });
      if (r.src !== undefined) d.base = r.src;
      status.textContent = r.changed === false ? "" : "применено — PDF обновляется";
      refreshSoon();
    } catch (e) {
      status.textContent = "";
      if (e.data && e.data.conflict) { toast("Элемент изменился — открываю заново", { err: true }); return openElement(d.uid); }
      fail(e);
    }
  };
  for (const k of d.style_keys) {
    const u = STYLE_UI[k];
    const v = (d.style || {})[k];
    let c;
    if (u.type === "select") c = h("select", { class: "inp" }, u.opts.map(([val, t]) => h("option", { value: val, selected: v === val || undefined }, t)));
    else if (u.type === "tri") c = h("select", { class: "inp" }, [["", "как обычно"], ["yes", "да"], ["no", "нет"]].map(([val, t]) =>
      h("option", { value: val, selected: (v === true && val === "yes") || (v === false && val === "no") || (v === undefined && !val) || undefined }, t)));
    else if (u.type === "check") c = h("input", { type: "checkbox", checked: v || undefined });
    else c = h("input", { class: "inp", type: "number", step: u.step, placeholder: u.ph, value: v === undefined ? "" : String(v) });
    c.dataset.key = k;
    c.addEventListener(u.type === "num" ? "input" : "change", () => { clearTimeout(timer); timer = setTimeout(() => send(collect()), u.type === "num" ? 700 : 150); });
    ctl[k] = c;
    fields.append(u.type === "check" ? h("label", { class: "check" }, c, " " + u.label) : h("div", { class: "field", title: u.tip || "" }, h("label", {}, u.label), c));
  }
  if (S.editor && S.editor.type === "tokens") {
    fields.append(h("div", { class: "field wide" }, h("label", {}, "Выделение слов в тексте"),
      h("div", { class: "row emph" },
        h("button", { class: "btn", title: "Полужирный (Ctrl+B)", onclick: () => S.editor.ed.wrap("b") }, h("b", {}, "Ж")),
        h("button", { class: "btn", title: "Курсив (Ctrl+I)", onclick: () => S.editor.ed.wrap("i") }, h("i", {}, "К")),
        h("span", { class: "muted small" }, "выделите слова и нажмите, затем «Сохранить»; щелчок по выделенному — снять"))));
  }
  fields.hidden = !d.style_on;
  const HINT_ON = "Этот элемент оформлен вручную: LDE не подгоняет его под ГОСТ. Снимите флажок, чтобы вернуть автоматическое оформление.";
  const HINT_OFF = "Для исправления «на месте»: отступы, интервалы, шрифт, выравнивание этого элемента. Действует в PDF и Word.";
  const hint = h("div", { class: "hint" }, cb.checked ? HINT_ON : HINT_OFF);
  cb.addEventListener("change", () => {
    box.classList.toggle("on", cb.checked);
    fields.hidden = !cb.checked;
    hint.textContent = cb.checked ? HINT_ON : HINT_OFF;
    if (cb.checked) send(collect());
    else send(null);
  });
  box.append(h("label", { class: "check style-toggle", title: "Оформление этого элемента задаётся вручную и не подгоняется автоматически; действует в PDF и Word" },
    cb, " Ручное оформление"), status, hint, fields);
  return box;
}
/* Конфликт слияния: «моё / было / чужое», выбор или свой вариант — как в git-инструментах */
function conflictEditor(d, body, foot) {
  const c = d.conflict || {};
  foot.innerHTML = "";
  const take = async (src, what) => {
    try { await api("/api/element/save", { uid: d.uid, base: d.base, src }); toast(`Конфликт решён: ${what}`); S.dirty = false; closePanel(); refreshSoon(); }
    catch (e) { fail(e); }
  };
  const col = (title, cls, words, raw) => h("div", { class: "cf-col " + cls }, h("div", { class: "lbl" }, title),
    words ? wordsView(words) : h("div", { class: "diff words" }, raw || h("span", { class: "muted" }, "(нет)")));
  body.append(h("div", { class: "hint" }, "Этот фрагмент изменили обе стороны по-разному. Выберите вариант или напишите свой — остальные изменения уже слиты."),
    h("div", { class: "cf" },
      col(`Моё (${c.l_ours || "моё"})`, "ours", c.base ? c.w_ours : null, c.ours),
      col(`Чужое (${c.l_theirs || "чужое"})`, "theirs", c.base ? c.w_theirs : null, c.theirs)),
    h("details", {}, h("summary", {}, "Как было до обеих правок"), h("div", { class: "diff words" }, c.base || h("span", { class: "muted" }, "(не было — обе стороны вставили своё)"))));
  const ta = h("textarea", { rows: 6, class: "inp", style: "height:auto", oninput: () => (S.dirty = true) });
  ta.value = c.ours || "";
  body.append(h("div", { class: "field" }, h("label", {}, "Свой вариант (исходный текст LDE; можно собрать из обоих)"), ta));
  foot.append(
    h("button", { class: "btn primary", id: "cf-ours", onclick: () => take(c.ours || "", "моё") }, "Взять моё"),
    h("button", { class: "btn primary", id: "cf-theirs", onclick: () => take(c.theirs || "", "чужое") }, "Взять чужое"),
    h("button", { class: "btn", id: "cf-both", onclick: () => take([c.ours, c.theirs].filter(Boolean).join("\n\n"), "оба") }, "Оба"),
    h("button", { class: "btn", id: "cf-own", onclick: () => take(ta.value.trim(), "свой вариант") }, "Свой вариант"),
    h("div", { class: "spacer" }),
    h("button", { class: "btn", onclick: closePanel }, "Позже"));
}
function placeEnd(el) { const r = document.createRange(); r.selectNodeContents(el); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }

function sourceMode(d) {
  const [body, foot] = [$("#panel-body"), $("#panel-foot")];
  body.innerHTML = "";
  const ta = h("textarea", { rows: 14, oninput: () => (S.dirty = true) });
  ta.value = d.src;
  S.editor = { type: "src", ta };
  body.append(h("div", { class: "field" }, h("label", {}, "Исходный текст элемента"), ta), h("div", { class: "toolbar" }, h("div", { class: "spacer" }), copySrcButton(() => ta.value)),
    h("div", { class: "hint" }, "Вставки: {величина}, {=расчёт.число}, @fig:метка, [@источник], формулы — $…$."));
}

function insertToolbar(ed) {
  return h("div", { class: "toolbar" },
    h("button", { class: "btn", title: "Величина (Ctrl+K)", onclick: () => picker(ed, "q") }, "+ Величина"),
    h("button", { class: "btn", onclick: () => picker(ed, "val") }, "+ Число"),
    h("button", { class: "btn", onclick: () => formulaDialog("", (tex) => ed.insertChip({ t: "chip", src: "$" + tex + "$", kind: "math", latex: tex })) }, "+ Формула"),
    h("button", { class: "btn", onclick: () => picker(ed, "ref") }, "+ Ссылка"),
    h("button", { class: "btn", onclick: () => picker(ed, "cite") }, "+ Цитата"),
    h("div", { class: "spacer" }), copySrcButton(() => ed.source()));
}

function chipClick(c, ed) {
  const kind = c.dataset.kind, src = c.dataset.src;
  if (REVIEWER && kind !== "math" && kind !== "b" && kind !== "i") return toast(c.title || c.textContent);
  if (kind === "math") {
    formulaDialog(c.dataset.ml || src.slice(1, -1), (tex) => { c.dataset.src = "$" + tex + "$"; c.dataset.ml = tex; c.innerHTML = mathHTML(tex); S.dirty = true; });
  } else if (kind === "q") {
    openQuantity(src.slice(1, -1));
  } else if (kind === "val") {
    const key = src.slice(2, -1).split(/[.\s]/)[0];
    openCalc(key);
  } else if (kind === "b" || kind === "i") {
    c.replaceWith(document.createTextNode(c.textContent));   // снять выделение
    S.dirty = true;
  } else {
    toast(c.title || src);
  }
}

async function saveCurrent() {
  const d = S.cur;
  if (!d) return;
  const t0 = performance.now();
  let body = { uid: d.uid, base: d.base };
  const E = S.editor;
  try {
    if (E && E.type === "tokens") {
      body.tokens = E.ed.tokens();
      if (d.kind === "figure" && E.width) {
        const res = await api("/api/element/save", body);
        const cur = await api(`/api/element?uid=${encodeURIComponent(res.uid || d.uid)}`);
        const w = (E.width.value / 100).toFixed(2).replace(/0$/, "");
        let src = cur.src.replace(/width=[0-9.]+/, `width=${w}`);
        if (!/width=/.test(src)) src = src.replace(/\{#([^}]*)\}\s*$/, `{#$1 width=${w}}`);
        if (src !== cur.src) await api("/api/element/save", { uid: cur.uid, base: cur.base, src });
        return afterSave(d, t0);
      }
    } else if (E && E.type === "src") body.src = E.ta.value;
    else if (E && E.type === "formula") {
      body.src = buildFormulaSrc(E.value(), E.numbered.checked, d.label);
    } else if (E && E.type === "table") body.src = E.build();
    const res = await api("/api/element/save", body);
    if (res.changed === false) { S.dirty = false; return toast("Без изменений"); }
    afterSave(d, t0);
  } catch (e) {
    if (e.data && e.data.conflict) return conflictDialog(e.data, body);
    fail(e);
  }
}
function afterSave(d, t0) {
  S.dirty = false;
  S.saveT0 = t0;
  toast(REVIEWER ? "Записано в вашу копию — автор увидит исправление" : "Сохранено — обновляю PDF");
  refreshSoon();
}
function conflictDialog(data, mine) {
  dialog("Элемент изменился снаружи", (body, foot, close) => {
    body.append(h("div", {}, data.error),
      h("div", { class: "lbl" }, "Сейчас в файле:"), h("div", { class: "diff old" }, data.current || "(элемент удалён)"),
      h("div", { class: "lbl" }, "Ваш вариант (скопируйте, если нужен):"), h("textarea", { rows: 6, class: "inp", style: "height:auto" }, mine.src || (mine.tokens || []).map((t) => t.src || t.v).join("")));
    foot.append(h("button", { class: "btn", onclick: () => { close(); S.dirty = false; openElement(S.cur.uid, false); } }, "Открыть текущую версию"));
  });
}

function moreMenu(e, d) {
  dialog("Действия", (body, foot, close) => {
    const act = (label, fn) => h("div", { class: "item", onclick: () => { close(); fn(); } }, h("span", { class: "t" }, label));
    body.append(
      act("Править исходный текст элемента", () => sourceMode(d)),
      act("Добавить абзац после", () => addBlock("para", d.uid)),
      act("Добавить формулу после", () => addBlock("formula", d.uid)),
      act("Добавить рисунок после", () => addBlock("figure", d.uid)),
      act("Добавить таблицу после", () => addBlock("table", d.uid)),
      act("Удалить элемент", async () => {
        if (!confirm(`Удалить «${d.title}»? Это можно будет вернуть кнопкой «Вернуть».`)) return;
        try { await api("/api/element/save", { uid: d.uid, base: d.base, src: "" }); S.dirty = false; closePanel(); refreshSoon(); } catch (er) { fail(er); }
      }));
  });
}

/* ------------------------------------------------------------ formulas ---- */
function formulaEditor(latex, d) {
  const mf = mtField(new MathfieldElement());
  mf.value = mtShow(latex || "");
  mf.mathVirtualKeyboardPolicy = "manual";
  const tex = h("textarea", { rows: 4 });
  tex.value = latex || "";
  const ed = { type: "formula", mf, tex, last: "mf" };
  mf.addEventListener("input", () => {
    // новая дробь (клавиша «/», клавиатура формул) — тоже полного размера, каретка на месте
    const v = mf.value, sv = mtShow(v);
    if (sv !== v) { const pos = mf.position; mf.setValue(sv, { silenceNotifications: true }); mf.position = pos; }
    tex.value = mtSave(mf.value); ed.last = "mf"; S.dirty = true;
  });
  tex.addEventListener("input", () => { mf.value = mtShow(tex.value); ed.last = "tex"; S.dirty = true; });
  ed.value = () => (ed.last === "tex" ? tex.value : mtSave(mf.value)).trim();
  const numbered = h("input", { type: "checkbox", checked: d ? d.numbered : true });
  numbered.checked = d ? !!d.numbered : true;
  numbered.onchange = () => (S.dirty = true);
  ed.numbered = numbered;
  // вставки в формулу: связанная величина / число расчёта (в исходнике — \q{…} / \val{…})
  const target = {
    insertChip: (t) => {
      let frag;
      if (t.kind === "q") frag = `\\htmlData{lde=q:${t.src.slice(1, -1)}}{${t.latex}}`;
      else frag = `\\htmlData{lde=v:${t.src.slice(2, -1)}}{\\text{${(t.text || "").replace(/[{}\\]/g, "")}}}`;
      mf.focus();
      mf.insert(frag, { insertionMode: "replaceSelection", selectionMode: "after" });
      tex.value = mtSave(mf.value); ed.last = "mf"; S.dirty = true;
    },
  };
  const bar = h("div", { class: "toolbar" },
    h("button", { class: "btn", id: "mf-add-q", title: "Вставить величину в формулу", onclick: () => picker(target, "q", ["q", "val"]) }, "+ Величина"),
    h("button", { class: "btn", id: "mf-add-val", title: "Вставить число из расчёта", onclick: () => picker(target, "val", ["q", "val"]) }, "+ Число"));
  S.editor = ed;
  setTimeout(() => mf.focus(), 60);
  return [h("div", { class: "field" }, h("label", {}, "Формула"), mf), bar,
    h("details", {}, h("summary", {}, "LaTeX"), h("div", { class: "field" }, tex)),
    h("label", { class: "check" }, numbered, h("span", {}, d && d.number ? `Номер справа: (${d.number})` : "С номером справа")),
    h("div", { class: "hint" }, "Кириллица в индексах печатается прямым шрифтом автоматически. Номер и ссылки на него обновляются сами.")];
}
function buildFormulaSrc(tex, numbered, label) {
  let lab = label;
  if (numbered && !lab) lab = "eq:" + slugify(tex.split(/=|\\approx/)[0]) + "-" + Math.random().toString(36).slice(2, 5);
  return `$$ ${tex} $$` + (numbered && lab ? ` {#${lab}}` : "");
}
function formulaDialog(latex, ok) {
  dialog("Формула", (body, foot, close) => {
    const keep = S.editor;
    const els = formulaEditor(latex, null);
    const E = S.editor;
    S.editor = keep;
    body.append(els[0], els[1], els[2]);
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"),
      h("button", { class: "btn primary", onclick: () => { const v = E.value(); if (v) ok(v); close(); } }, "Готово"));
  });
}

/* --------------------------------------------------------------- tables ---- */
function tableEditor(d) {
  const capEd = new ChipEditor(d.tokens, { onChip: chipClick, small: true });
  const rows = [d.header_tokens || d.header.map((x) => [{ t: "text", v: x }]), ...(d.rows_tokens || d.rows.map((r) => r.map((x) => [{ t: "text", v: x }])))];
  const grid = rows.map((r) => r.map((cell) => new ChipEditor(cell, { onChip: chipClick, small: true })));
  const box = h("div", { class: "grid" });
  const draw = () => {
    box.innerHTML = "";
    const t = h("table");
    grid.forEach((r) => t.append(h("tr", {}, r.map((c) => h("td", {}, c.el)))));
    box.append(t);
  };
  draw();
  const ncol = () => Math.max(...grid.map((r) => r.length));
  S.editor = {
    type: "table",
    build: () => {
      const cell = (c) => c.tokens().map((t) => (t.t === "chip" ? t.src : t.v.replace(/\|/g, "\\|").replace(/[{}$]/g, (m) => "\\" + m))).join("").trim();
      const cap = capEd.tokens().map((t) => (t.t === "chip" ? t.src : t.v)).join("").trim();
      const lines = [`: ${cap}` + (d.label ? ` {#${d.label}}` : "")];
      grid.forEach((r, i) => {
        const cells = r.map(cell);
        while (cells.length < ncol()) cells.push("");
        lines.push("| " + cells.join(" | ") + " |");
        if (i === 0) lines.push("|" + "---|".repeat(ncol()));
      });
      return lines.join("\n");
    },
  };
  return [h("div", { class: "field" }, h("label", {}, `Заголовок: «Таблица ${d.number} — …»`), capEd.el), box,
    h("div", { class: "toolbar" },
      h("button", { class: "btn", onclick: () => { grid.push(Array.from({ length: ncol() }, () => new ChipEditor([], { small: true }))); draw(); S.dirty = true; } }, "+ Строка"),
      h("button", { class: "btn", onclick: () => { grid.forEach((r) => r.push(new ChipEditor([], { small: true }))); draw(); S.dirty = true; } }, "+ Столбец"),
      h("button", { class: "btn", onclick: () => { if (grid.length > 2) { grid.pop(); draw(); S.dirty = true; } } }, "− Строка"),
      h("button", { class: "btn", onclick: () => { if (ncol() > 1) { grid.forEach((r) => r.pop()); draw(); S.dirty = true; } } }, "− Столбец")),
    h("div", { class: "hint" }, "Первая строка — шапка. В ячейки можно вставлять величины и числа: щёлкните в ячейку и нажмите Ctrl+K."),
    insertToolbarFor(() => lastFocusedChips(grid.flat().concat([capEd])))];
}
let lastChips = null;
document.addEventListener("focusin", (e) => { if (e.target.classList && e.target.classList.contains("chips")) lastChips = e.target; });
function lastFocusedChips(eds) { return eds.find((x) => x.el === lastChips) || eds[eds.length - 1]; }
function insertToolbarFor(getEd) {
  return h("div", { class: "toolbar" },
    h("button", { class: "btn", onclick: () => picker(getEd(), "q") }, "+ Величина"),
    h("button", { class: "btn", onclick: () => picker(getEd(), "val") }, "+ Число"),
    h("button", { class: "btn", onclick: () => picker(getEd(), "ref") }, "+ Ссылка"));
}

/* -------------------------------------------------------------- picker ---- */
function picker(ed, kind, only) {
  const all = { q: "Величина", val: "Число из расчёта", ref: "Ссылка", cite: "Цитата" };
  const kinds = Object.fromEntries(Object.entries(all).filter(([k]) => !only || only.includes(k)));
  dialog("Вставить", (body, foot, close) => {
    let cur = kind || "q";
    const cats = h("div", { class: "cat" });
    const inp = h("input", { class: "inp", placeholder: "Поиск по названию, обозначению, смыслу" });
    const list = h("div", { class: "pick" });
    const loc = h("input", { class: "inp", placeholder: "страница, например: с. 45 (необязательно)", style: "display:none" });
    let items = [], seq = 0;
    const choose = (it) => {
      let src = it.src;
      if (it.kind === "cite" && loc.value.trim()) src = src.replace(/\]$/, `, ${loc.value.trim()}]`);
      ed.insertChip({ t: "chip", src, kind: it.kind, latex: it.latex, text: it.kind === "ref" ? it.text : it.text, title: it.title });
      close();
    };
    const load = async () => {
      loc.style.display = cur === "cite" ? "" : "none";
      const my = ++seq;
      const res = await api(`/api/search?kind=${cur}&q=${encodeURIComponent(inp.value)}`);
      if (my !== seq) return;            // пока шёл запрос, ввели ещё — этот ответ устарел
      items = res;
      list.innerHTML = "";
      list.dataset.q = cur + ":" + inp.value;
      items.slice(0, 120).forEach((it, i) => list.append(h("div", { class: "item" + (i === 0 ? " sel" : ""), onclick: () => choose(it) },
        h("span", { class: "prev", html: it.latex !== undefined ? mathHTML(it.latex) : esc(it.text) }),
        h("span", { class: "t", title: it.title }, it.title))));
      if (!items.length) {
        list.append(h("div", { class: "muted", style: "padding:8px" }, "Ничего не найдено."));
        if (!REVIEWER && cur === "q" && inp.value.trim()) list.append(h("button", { class: "btn", onclick: () => { close(); openQuantity("", inp.value.trim(), ed); } }, `Создать величину «${inp.value.trim()}»`));
        if (!REVIEWER && cur === "cite") list.append(h("button", { class: "btn", onclick: () => { close(); openSource(""); } }, "Добавить источник"));
      }
    };
    Object.entries(kinds).forEach(([k, t]) => cats.append(h("button", { class: k === cur ? "on" : "", onclick: (e) => { cur = k; $$("button", cats).forEach((b) => b.classList.toggle("on", b === e.target)); load(); inp.focus(); } }, t)));
    inp.addEventListener("input", load);
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && items[0]) { e.preventDefault(); choose(items[0]); } });
    body.append(cats, inp, loc, list);
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"));
    load();
    setTimeout(() => inp.focus(), 40);
  }, 2);
}

/* ------------------------------------------------------- новые элементы ---- */
async function addBlock(kind, after) {
  after = after || (S.cur && S.cur.uid) || null;
  if (kind === "para") return insertSrc(after, "Новый абзац.");
  if (kind === "formula") return formulaDialog("", (tex) => insertSrc(after, buildFormulaSrc(tex, true, null)));
  if (kind === "table") {
    const lab = "tab:t" + Math.random().toString(36).slice(2, 6);
    return insertSrc(after, `: Заголовок таблицы {#${lab}}\n| Параметр | Значение |\n|---|---|\n|  |  |`);
  }
  if (kind === "figure") {
    dialog("Новый рисунок", (body, foot, close) => {
      const cap = h("input", { class: "inp", placeholder: "Подпись рисунка (без «Рисунок N —»)" });
      const sel = h("select", { class: "inp" }, h("option", { value: "" }, "— выберите выход расчёта или загрузите файл —"));
      const outs = [];
      (S.state.calcs || []).forEach((c) => outs.push(c));
      api("/api/state").then((st) => st.calcs.forEach((c) => api(`/api/calc?key=${c.key}`).then((cc) => cc.outputs.filter((o) => /\.(png|jpe?g|pdf)$/i.test(o)).forEach((o) => sel.append(h("option", { value: `calc/${c.key}/out/${o}` }, `${c.title}: ${o}`))))));
      let path = "";
      const file = h("input", { type: "file", accept: ".png,.jpg,.jpeg,.pdf", onchange: async (e) => {
        const f = e.target.files[0];
        const r = await fetch(`/api/upload?kind=figure&name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
        const dd = await r.json();
        if (!r.ok) return fail(new Error(dd.error));
        path = dd.path; toast(`Файл загружен: ${path}`);
      } });
      body.append(h("div", { class: "field" }, h("label", {}, "Подпись"), cap), h("div", { class: "field" }, h("label", {}, "Изображение"), sel, file));
      foot.append(h("button", { class: "btn", onclick: close }, "Отмена"), h("button", { class: "btn primary", onclick: () => {
        const p = sel.value || path;
        if (!p) return toast("Выберите изображение", { err: true });
        const lab = "fig:" + slugify(cap.value || "ris") + "-" + Math.random().toString(36).slice(2, 4);
        insertSrc(after, `![${cap.value.replace(/[\[\]]/g, "")}](${p}){#${lab} width=0.8}`);
        close();
      } }, "Добавить"));
    });
  }
}
async function insertSrc(after, src) {
  try { await api("/api/element/insert", { after, src }); toast("Добавлено"); refreshSoon(); } catch (e) { fail(e); }
}
async function replaceFigure(d, f) {
  if (!f) return;
  const r = await fetch(`/api/upload?kind=figure&name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
  const dd = await r.json();
  if (!r.ok) return fail(new Error(dd.error));
  const src = d.src.replace(/\]\(([^)]*)\)/, `](${dd.path})`);
  try { await api("/api/element/save", { uid: d.uid, base: d.base, src }); toast("Рисунок заменён"); S.dirty = false; openElement(d.uid); refreshSoon(); } catch (e) { fail(e); }
}
function slugify(s) {
  const tr = { а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c", ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya" };
  const t = (s || "").toLowerCase().replace(/\\[a-z]+/g, (m) => m.slice(1)).split("").map((c) => tr[c] ?? c).join("");
  return (t.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").split("-").slice(0, 3).join("-") || "x").slice(0, 20);
}

/* ------------------------------------------------- величины, расчёты, источники ---- */
async function openQuantity(key, newKey, insertInto) {
  const d = key ? await api(`/api/quantity?key=${encodeURIComponent(key)}`) : { key: newKey || "", new: true, meaning: "", unit: "", latex: "" };
  const keep = S.editor, keepCur = S.cur;
  const [body, foot] = insertInto ? [null, null] : openPanel("Величина", key ? key : "Новая величина");
  const run = (body, foot, close) => {
    const k = h("input", { class: "inp", value: d.key, placeholder: "R_ц, f_0, φ_1 …" });
    if (!d.new) k.disabled = true;
    const prev = h("div", { class: "chips small", style: "min-height:44px;font-size:20px", html: mathHTML(d.latex || d.auto_latex || "") });
    const meaning = h("input", { class: "inp", value: d.meaning, placeholder: "что обозначает" });
    const unit = h("input", { class: "inp", value: d.unit, placeholder: "м, Гц, с …" });
    const tex = h("input", { class: "inp", value: d.latex || "", placeholder: d.auto_latex ? `автоматически: ${d.auto_latex}` : "LaTeX (необязательно)" });
    const upd = () => { prev.innerHTML = mathHTML(tex.value || autoTex(k.value)); };
    k.oninput = upd; tex.oninput = upd;
    body.append(h("div", { class: "field" }, h("label", {}, "Обозначение (как пишется: основа_индекс)"), k), prev,
      h("div", { class: "field" }, h("label", {}, "Смысл"), meaning), h("div", { class: "field" }, h("label", {}, "Единица"), unit),
      h("details", {}, h("summary", {}, "Набор (LaTeX)"), h("div", { class: "field" }, tex), h("div", { class: "hint" }, "Пусто — по правилу: латинская основа курсивом, кириллический индекс прямым.")),
      d.uses !== undefined ? h("div", { class: "hint" }, `Вставлена в текст: ${d.uses} раз.`) : null);
    foot.append(h("button", { class: "btn primary", onclick: async () => {
      try {
        const r = await api("/api/quantity", { key: k.value, meaning: meaning.value, unit: unit.value, latex: tex.value });
        toast("Величина сохранена");
        if (insertInto) insertInto.insertChip({ t: "chip", src: r.src, kind: "q", latex: tex.value || autoTex(k.value) });
        close ? close() : null;
        refreshSoon();
      } catch (e) { fail(e); }
    } }, insertInto ? "Сохранить и вставить" : "Сохранить"), h("button", { class: "btn", onclick: () => (close ? close() : closePanel()) }, "Закрыть"));
  };
  if (insertInto) dialog("Новая величина", run, 2);
  else { run(body, foot, null); S.editor = null; }
  if (insertInto) { S.editor = keep; S.cur = keepCur; }
}
function autoTex(key) {
  const [b, ...rest] = (key || "").split("_");
  const ix = rest.join("_");
  const part = (s, idx) => s.replace(/[А-Яа-яЁё][А-Яа-яЁё.]*/g, (m) => `\\text{${m}}`).replace(/([A-Za-z]{2,})/g, (m) => (idx ? `\\mathrm{${m}}` : m));
  return ix ? `${part(b, false)}_{${part(ix, true)}}` : part(b, false);
}

async function openCalc(key) {
  let d;
  try { d = await api(`/api/calc?key=${encodeURIComponent(key)}`); } catch (e) { return fail(e); }
  const [body, foot] = openPanel("Расчёт", d.title);
  const state = { ok: "Актуален", stale: "Устарел", never: "Ещё не запускался", error: "Ошибка в описании" }[d.state];
  body.append(h("div", {}, h("b", {}, state), d.when ? h("span", { class: "muted" }, ` · последний запуск ${d.when}, ${String(d.duration).replace(".", ",")} с`) : ""));
  if (d.reasons && d.reasons.length && d.state !== "ok") body.append(h("div", { class: "hint" }, "Почему: " + d.reasons.join("; ")));
  if (d.failure) body.append(h("div", { class: "pill err", style: "height:auto;padding:8px 12px;border-radius:8px;white-space:normal" }, `Упал: ${d.failure.error}`));
  const grid = h("div", { class: "kv" });
  const inputs = {};
  d.params.forEach((p) => {
    const inp = h("input", { class: "inp", value: fmtNum(p.value), "data-param": p.name, title: p.pretty || "" });
    const pretty = h("span", { class: "muted small", title: "как будет в тексте" }, p.unit ? "= " + p.pretty : "");
    inp.oninput = () => (S.dirty = true);
    inputs[p.name] = [inp, p.value];
    grid.append(h("div", { class: "name" }, p.title || p.name, p.title ? h("small", {}, p.name) : null), inp, pretty);
  });
  body.append(h("div", { class: "lbl" }, "Параметры"), grid);
  if (d.figures.length) body.append(h("div", { class: "hint" }, "Рисунки: ", ...d.figures.map((f) => h("a", { href: "#", onclick: (e) => { e.preventDefault(); openElement(f.uid, true); } }, f.title + " "))));
  body.append(h("details", {}, h("summary", {}, "Подробности: скрипт, входы, журнал"),
    h("div", { class: "hint" }, `Скрипт: ${d.script}`), d.inputs.length ? h("div", { class: "hint" }, `Входы: ${d.inputs.join(", ")}`) : null,
    h("div", { class: "hint" }, `Выходы: ${d.outputs.join(", ")}`), d.isolated ? h("div", { class: "hint" }, "Запуск без доступа к сети.") : null,
    h("pre", { class: "log" }, d.log || "журнала нет")));
  foot.append(
    h("button", { class: "btn primary", id: "params-save", onclick: async () => {
      try {
        for (const [name, [inp, old]] of Object.entries(inputs)) {
          if (inp.value.trim() !== fmtNum(old)) await api("/api/param", { calc: key, name, value: inp.value });
        }
        S.dirty = false;
        toast("Параметры сохранены — расчёт устарел");
        await refresh();
        openCalc(key);
      } catch (e) { fail(e); }
    } }, "Сохранить параметры"),
    h("button", { class: "btn", id: "calc-run", onclick: () => recalc([key]) }, "Пересчитать"));
}
const fmtNum = (v) => {
  if (typeof v !== "number") return String(v);
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e6 || a < 1e-3)) return v.toExponential().replace("e+", "e").replace(/\.?0+e/, "e").replace(".", ",");
  return String(v).replace(".", ",");
};

async function recalc(keys) {
  try { await api("/api/recalc", { calcs: keys || null }); toast("Пересчёт запущен"); refreshSoon(); } catch (e) { fail(e); }
}

async function openSource(key) {
  const d = key ? await api(`/api/source?key=${encodeURIComponent(key)}`) : { key: "", new: true, fields: {}, type: "book" };
  const [body, foot] = openPanel("Источник", d.n ? `[${d.n}] ${key}` : key || "Новый источник");
  const k = h("input", { class: "inp", value: d.key, placeholder: "ключ латиницей: cumming2005" });
  if (!d.new) k.disabled = true;
  const typ = h("select", { class: "inp" }, ...[["book", "Книга"], ["article", "Статья"], ["incollection", "Глава/статья в сборнике"], ["online", "Сайт"], ["misc", "Другое"]].map(([v, t]) => h("option", { value: v, selected: v === d.type }, t)));
  const F = [["author", "Авторы (Фамилия, И. О. and Фамилия, И. О.)"], ["title", "Заглавие"], ["journal", "Журнал"], ["booktitle", "Сборник/книга"], ["address", "Город"], ["publisher", "Издательство"], ["year", "Год"], ["volume", "Том"], ["number", "Номер"], ["pages", "Страницы"], ["pagetotal", "Всего страниц"], ["url", "URL"], ["urldate", "Дата обращения"], ["formatted", "Готовое описание (если задано — используется как есть)"], ["file", "Файл источника"]];
  const inputs = {};
  body.append(h("div", { class: "field" }, h("label", {}, "Ключ"), k), h("div", { class: "field" }, h("label", {}, "Вид"), typ));
  if (d.text) body.append(h("div", { class: "hint" }, "В списке: " + d.text));
  F.forEach(([f, lbl]) => {
    const inp = f === "formatted" ? h("textarea", { rows: 3 }) : h("input", { class: "inp" });
    inp.value = d.fields[f] || "";
    inputs[f] = inp;
    if (f === "file" && d.fields.file) body.append(h("div", { class: "field" }, h("label", {}, lbl), inp, ...d.fields.file.split(";").map((x) => h("a", { href: "#", onclick: (e) => { e.preventDefault(); api("/api/open", { what: "file", path: x.trim() }); } }, "Открыть " + x.trim().split("/").pop()))));
    else body.append(h("div", { class: "field" }, h("label", {}, lbl), inp));
  });
  foot.append(h("button", { class: "btn primary", onclick: async () => {
    const fields = {};
    for (const [f, inp] of Object.entries(inputs)) fields[f] = inp.value;
    try { await api("/api/source", { key: k.value, type: typ.value, fields }); toast("Источник сохранён"); refreshSoon(); } catch (e) { fail(e); }
  } }, "Сохранить"));
}

/* ------------------------------------------------- сгенерированные части ---- */
function openGenerated(uid) {
  if (uid === "gen:title") return settingsDialog();
  if (uid === "gen:toc") return toast("Содержание собирается автоматически из заголовков разделов");
  if (uid === "gen:abbr") return abbrDialog();
  if (uid === "gen:symbols") { $$("#tabs button").find((b) => b.dataset.tab === "quantities").click(); return toast("Перечень обозначений собирается из величин"); }
  if (uid.startsWith("gen:bib:")) return openSource(uid.slice(8));
  if (uid === "gen:bib") { $$("#tabs button").find((b) => b.dataset.tab === "sources").click(); return toast("Список источников — в порядке первой ссылки в тексте"); }
}
function abbrDialog() {
  dialog("Перечень сокращений", (body, foot, close) => {
    const rows = h("div", { style: "display:flex;flex-direction:column;gap:6px" });
    const add = (k = "", v = "") => rows.append(h("div", { class: "row" }, h("input", { class: "inp", value: k, style: "flex:0 0 120px", placeholder: "РСА" }), h("input", { class: "inp", value: v, placeholder: "расшифровка" }),
      h("button", { class: "icon-btn", style: "flex:none", title: "Удалить", html: icon("close"), onclick: (e) => e.target.closest(".row").remove() })));
    (S.state.abbreviations || []).forEach((a) => add(a.key, a.text));
    body.append(rows, h("div", {}, h("button", { class: "btn", onclick: () => add() }, "+ Сокращение")));
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"), h("button", { class: "btn primary", onclick: async () => {
      const items = $$(".row", rows).map((r) => { const [k, v] = $$("input", r); return { key: k.value.trim(), text: v.value.trim() }; }).filter((x) => x.key);
      try { await api("/api/abbreviations", { items }); toast("Сокращения сохранены"); close(); refreshSoon(); } catch (e) { fail(e); }
    } }, "Сохранить"));
  });
}

/* ------------------------------------------------------------- dialogs ---- */
function dialog(title, fill, layer) {
  const wrap = $(layer === 2 ? "#dialog-wrap2" : "#dialog-wrap"), dlg = $(layer === 2 ? "#dialog2" : "#dialog");
  dlg.innerHTML = "";
  const body = h("div", { class: "dlg-body" }), foot = h("div", { class: "dlg-foot" });
  const close = () => { wrap.hidden = true; dlg.innerHTML = ""; };
  dlg.append(h("div", { class: "dlg-head" }, h("h2", {}, title), h("button", { class: "icon-btn", title: "Закрыть (Esc)", html: icon("close"), onclick: close })), body, foot);
  wrap.hidden = false;
  // закрыть щелчком по фону — только если и нажали, и отпустили на фоне: выделение текста в поле,
  // закончившееся за краем окна, браузер тоже считает щелчком по фону — окно не должно закрываться
  let downOnWrap = false;
  wrap.onmousedown = (e) => { downOnWrap = e.target === wrap; };
  wrap.onclick = (e) => { if (e.target === wrap && downOnWrap) close(); downOnWrap = false; };
  fill(body, foot, close);
  return close;
}

async function issuesDialog() {
  const st = S.state;
  dialog("Состояние документа", async (body, foot, close) => {
    if (st.status.stale.length) {
      body.append(h("div", { class: "lbl" }, "Устарело"));
      st.status.stale.forEach((s) => body.append(h("div", { class: "issue" }, h("span", { class: "sev warning" }), h("div", { class: "body" }, s.message))));
      body.append(h("div", {}, h("button", { class: "btn primary", id: "recalc-all", onclick: () => { close(); recalc(null); } }, h("span", { html: icon("refresh") }), "Обновить устаревшее")));
    }
    const iss = await api("/api/issues");
    const real = iss.filter((i) => i.rule !== "stale");
    if (!real.length && !st.status.stale.length) body.append(h("div", {}, "Всё актуально, замечаний нет."));
    if (real.length) {
      body.append(h("div", { class: "lbl" }, `Замечания (${real.length})`));
      const fixable = real.filter((i) => i.fix && i.fix.start !== undefined);
      if (fixable.length) body.append(h("div", {}, h("button", { class: "btn", id: "fix-all", onclick: async () => { try { const r = await api("/api/fix", {}); toast(`Исправлено: ${r.fixed}`); close(); refreshSoon(); } catch (e) { fail(e); } } }, `Исправить автоматически (${fixable.length})`)));
      real.slice(0, 300).forEach((i) => body.append(h("div", { class: "issue" }, h("span", { class: "sev " + i.severity }),
        h("div", { class: "body" }, h("div", {}, i.message + (i.hint ? " → " + i.hint : "")), h("div", { class: "where" }, i.title || i.file)),
        i.fix && i.fix.start !== undefined ? h("button", { class: "btn", onclick: async (e) => { try { await api("/api/fix", { only: [i.fix] }); e.target.closest(".issue").remove(); refreshSoon(); } catch (er) { fail(er); } } }, "Исправить") : null,
        i.element ? h("button", { class: "btn", onclick: () => { close(); openElement(i.element, true); } }, "Перейти") : null)));
    }
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"));
  });
}
$("#status-pill").onclick = issuesDialog;

/* ---------------------------------------------- изменения и сравнение ---- */
function wordsView(words) {
  // пословная разница; длинные неизменённые куски сокращаются
  const box = h("div", { class: "diff words" });
  words.forEach(([op, t], i) => {
    if (op === "=") {
      let x = t;
      if (x.length > 160) x = i === 0 ? "…" + x.slice(-80) : i === words.length - 1 ? x.slice(0, 80) + "…" : x.slice(0, 60) + " … " + x.slice(-60);
      box.append(x);
    } else box.append(h(op === "+" ? "ins" : "del", {}, t));
  });
  return box;
}
function changeItems(body, d, opts = {}) {
  d.groups.forEach((g) => {
    body.append(h("div", { class: "lbl" }, `${/^\d|^[А-Я]$/.test(g.where) ? "Раздел " : ""}${g.where}: ${g.summary}`));
    g.items.forEach((it) => {
      const opName = { added: "добавлен", removed: "удалён", changed: "изменён", moved: "перенесён" }[it.op];
      const box = h("div", { class: "chg" }, h("div", { class: "chg-head" }, h("span", { class: "op " + it.op }, opName), h("span", { class: "t" }, it.title),
        it.uid && it.op !== "removed" && opts.show !== false ? h("button", { class: "btn", onclick: () => { opts.close && opts.close(); openElement(it.uid, true); } }, "Показать") : null,
        opts.revert && it.op !== "moved" ? h("button", { class: "btn", title: opts.revertTitle || "", onclick: async () => { try { await api("/api/changes/revert", { item: it }); toast("Возвращено"); box.remove(); refreshSoon(); } catch (e) { fail(e); } } }, opts.revertLabel || "Вернуть как было") : null));
      const texOf = (src) => { const m = /\$\$([\s\S]*?)\$\$/.exec(src || ""); return m ? m[1].trim() : null; };
      if (it.kind === "formula" && (texOf(it.old) || texOf(it.new))) {
        if (it.old) box.append(h("div", { class: "diff old", html: mathHTML(texOf(it.old)) }));
        if (it.new) box.append(h("div", { class: "diff new", html: mathHTML(texOf(it.new)) }));
      } else if (it.words) box.append(wordsView(it.words));
      else {
        if (it.old) box.append(h("div", { class: "diff old" }, it.old.slice(0, 600)));
        if (it.new && it.op !== "moved") box.append(h("div", { class: "diff new" }, it.new.slice(0, 600)));
      }
      body.append(box);
    });
  });
  if (d.other.length) {
    body.append(h("div", { class: "lbl" }, "Другие изменения"));
    d.other.forEach((o) => body.append(h("div", { class: "hint" }, "• " + o.what)));
  }
}
async function changesDialog() {
  const d = await api("/api/changes");
  dialog("Что изменилось", (body, foot, close) => {
    if (!d.count) body.append(h("div", { class: "muted" }, "Изменений с последнего просмотра нет."));
    changeItems(body, d, { revert: true, close });
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"),
      h("button", { class: "btn primary", onclick: async () => { await api("/api/changes/seen", {}); close(); refresh(); } }, "Просмотрено"));
  });
}
async function compareDialog(base, target, title) {
  let d;
  try { d = await api(`/api/compare?base=${encodeURIComponent(base)}${target ? "&target=" + encodeURIComponent(target) : ""}`); } catch (e) { return fail(e); }
  dialog(title || "Сравнение версий", (body, foot, close) => {
    body.append(h("div", { class: "hint" }, target ? "Что изменилось от первой версии ко второй." : "Что изменилось с этой версии до текущего состояния. «Вернуть» возвращает элемент к той версии."));
    if (!d.count) body.append(h("div", { class: "muted" }, "Различий нет."));
    changeItems(body, d, { revert: !target, show: !target, close, revertLabel: "Вернуть к той версии" });
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"));
  });
}

/* ------------------------------------------------------------ история ---- */
const KIND_CLS = { submit: "ok", share: "accent", review: "warn" };
function fmtTime(t) { return new Date(t * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }); }
async function historyDialog() {
  let d;
  try { d = await api("/api/history"); } catch (e) { return fail(e); }
  dialog("История и версии", (body, foot, close) => {
    const restore = async (ref, name) => {
      if (!confirm(`Вернуть весь проект к «${name}»? Текущее состояние останется в истории — к нему тоже можно вернуться.`)) return;
      try { await api("/api/restore", { ref }); toast(`Возвращено к «${name}»`); close(); refreshSoon(); } catch (e) { fail(e); }
    };
    body.append(h("div", { class: "lbl" }, "Версии"));
    if (!d.versions.length) body.append(h("div", { class: "hint" }, "Именованных версий пока нет. Версия ставится сама при «Сдать» и «Поделиться», или кнопкой «Сохранить версию…»."));
    d.versions.forEach((v) => body.append(h("div", { class: "ver" },
      h("div", { class: "t" }, h("b", {}, v.name), " ", h("span", { class: "badge " + (KIND_CLS[v.kind] || "") }, v.kind_ru), h("span", { class: "muted small" }, " " + v.when),
        v.note ? h("div", { class: "muted small" }, v.note) : null),
      h("button", { class: "btn", onclick: () => compareDialog(v.tag, null, `Сравнение: «${v.name}» → сейчас`) }, "Сравнить с текущим"),
      h("button", { class: "btn", onclick: () => restore(v.tag, v.name) }, "Вернуть к ней"))));
    body.append(h("div", { class: "lbl" }, "Правки по сеансам"));
    d.sessions.forEach((sn, i) => {
      const det = h("details", { class: "sess", open: i === 0 || undefined },
        h("summary", {}, `${sn.since}${sn.since !== sn.when ? " — " + sn.when.replace(/^.*, /, "") : ""} · ${sn.authors.join(", ")} · ${sn.points.length} ${sn.points.length === 1 ? "точка" : "точек"}`));
      sn.points.forEach((pt, j) => det.append(h("div", { class: "pt" + (pt.undo ? " undo" : "") },
        h("span", { class: "muted small" }, fmtTime(pt.time)), h("span", { class: "t", title: pt.files.join(", ") }, pt.subject),
        ...pt.versions.map((v) => h("span", { class: "badge " + (KIND_CLS[v.kind] || "") }, v.name)),
        pt.hash !== d.head ? h("button", { class: "btn", onclick: () => compareDialog(pt.hash, null, `Сравнение: ${fmtTime(pt.time)} → сейчас`) }, "Сравнить") : h("span", { class: "muted small" }, "сейчас"),
        pt.hash !== d.head ? h("button", { class: "btn", onclick: () => restore(pt.hash, pt.subject) }, "Вернуть сюда") : null)));
      body.append(det);
    });
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"),
      d.can_redo ? h("button", { class: "btn", onclick: () => { close(); redo(); } }, "Повторить отменённое") : null,
      h("button", { class: "btn primary", onclick: () => { close(); saveVersionDialog(); } }, "Сохранить версию…"));
  });
}
function saveVersionDialog() {
  dialog("Сохранить версию", (body, foot, close) => {
    const name = h("input", { class: "inp", placeholder: "Например: Отправлено руководителю" });
    const note = h("input", { class: "inp", placeholder: "необязательно" });
    body.append(h("div", { class: "field" }, h("label", {}, "Название"), name), h("div", { class: "field" }, h("label", {}, "Заметка"), note),
      h("div", { class: "hint" }, "Версия — отметка в истории: с ней можно сравнить или вернуться к ней."));
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"),
      h("button", { class: "btn primary", id: "ver-save", onclick: async () => { try { const r = await api("/api/version", { name: name.value, note: note.value }); toast(`Версия «${r.name}» сохранена`); close(); } catch (e) { fail(e); } } }, "Сохранить"));
    setTimeout(() => name.focus(), 40);
  });
}
async function redo() {
  try { const r = await api("/api/redo", {}); toast(`Повторено: ${r.redone}`); refreshSoon(); } catch (e) { fail(e); }
}
$("#changes-btn").onclick = changesDialog;

async function reviewDialog() {
  const items = await api("/api/review");
  dialog("Примечания рецензента", (body, foot, close) => {
    const file = h("input", { type: "file", accept: ".docx", hidden: true, onchange: async (e) => {
      const f = e.target.files[0];
      const r = await fetch(`/api/upload?kind=review&name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
      const d = await r.json();
      if (!r.ok) return fail(new Error(d.error));
      toast(`Примечаний: ${d.count}`); close(); refresh(); reviewDialog();
    } });
    if (!items.length) body.append(h("div", { class: "muted" }, "Пришлите DOCX с примечаниями Word — они появятся здесь, привязанные к местам в тексте. Текст из Word не переносится: исходник остаётся главным."));
    items.forEach((i) => {
      const cb = h("input", { type: "checkbox" }); cb.checked = i.done;
      cb.onchange = () => api("/api/review/done", { n: i.n, done: cb.checked }).then(refreshSoon, fail);
      body.append(h("div", { class: "issue" }, h("label", { class: "check", title: "Выполнено" }, cb),
        h("div", { class: "body" }, h("div", { style: i.done ? "text-decoration:line-through;opacity:.6" : "" }, i.text),
          h("div", { class: "where" }, `${i.author} · ${i.title || "место не найдено"}${i.anchor ? " · к «" + i.anchor.slice(0, 50) + "»" : ""}`)),
        i.uid ? h("button", { class: "btn", onclick: () => { close(); openElement(i.uid, true); } }, "Перейти") : null));
    });
    if (items.length) body.append(h("div", { class: "hint" }, "Агенту можно написать: «исправь по примечаниям» — он увидит их командой lde review."));
    foot.append(h("button", { class: "btn", onclick: () => file.click() }, "Загрузить DOCX с примечаниями…"), file,
      h("button", { class: "btn primary", onclick: close }, "Закрыть"));
  });
}
$("#review-btn").onclick = reviewDialog;

async function settingsDialog() {
  const d = await api("/api/settings");
  dialog("Настройки документа", (body, foot, close) => {
    const doc = d.document;
    const prof = h("select", { class: "inp" }, ...d.profiles.map((p) => h("option", { value: p.key, selected: p.key === doc.profile }, p.title)));
    const num = h("select", { class: "inp" }, h("option", { value: "continuous", selected: doc.numbering === "continuous" }, "Сквозная: 1, 2, 3"), h("option", { value: "section", selected: doc.numbering === "section" }, "По разделам: 2.1, 2.2"));
    const cb = (k, lbl, def = false) => { const c = h("input", { type: "checkbox" }); c.checked = doc[k] == null ? def : !!doc[k]; c.dataset.k = k; return h("label", { class: "check" }, c, h("span", {}, lbl)); };
    const toggles = [cb("toc", "Содержание"), cb("abbreviations", "Перечень сокращений"), cb("symbols", "Перечень обозначений (величины)"), cb("bibliography", "Список источников"),
      cb("hyphenation", "Переносы слов в тексте (снять — текст без переносов, PDF и Word)", true)];
    const sp = h("select", { class: "inp", id: "set-spacing" }, h("option", { value: "none", selected: (doc.block_spacing || "none") === "none" }, "Нет (абзацы, формулы, рисунки вплотную)"),
      h("option", { value: "line", selected: doc.block_spacing === "line" }, "Одна строка до и после"));
    body.append(h("div", { class: "row" }, h("div", { class: "field" }, h("label", {}, "Оформление"), prof), h("div", { class: "field" }, h("label", {}, "Нумерация рисунков, таблиц, формул"), num)),
      h("div", { class: "field" }, h("label", {}, "Отступы вокруг заголовков, формул, рисунков, таблиц"), sp), ...toggles);
    body.append(h("div", { class: "lbl" }, "Абзац (по ГОСТ 7.32, задаётся профилем оформления — вручную у абзацев не настраивается)"),
      h("div", { class: "hint" }, "Times New Roman 14 pt · межстрочный интервал 1,5 · отступ первой строки 1,25 см · выравнивание по ширине · "
        + "без интервалов до и после абзацев. Пояснения после формулы: «где» — без отступа, обозначения следующих строк — точно под первым."));
    body.append(h("div", { class: "lbl" }, "Титульный лист"));
    const tf = {};
    if (d.title_lines) body.append(h("div", { class: "hint" }, `Титул перенесён из Word построчно (${d.title_lines} строк) — правится в lde.toml.`));
    d.fields.forEach((f) => { const i = h("input", { class: "inp", value: d.title[f.key] || "" }); tf[f.key] = i; body.append(h("div", { class: "field" }, h("label", {}, f.label), i)); });
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"), h("button", { class: "btn primary", onclick: async () => {
      const document_ = { profile: prof.value, numbering: num.value, block_spacing: sp.value };
      toggles.forEach((t) => { const c = t.querySelector("input"); document_[c.dataset.k] = c.checked; });
      const title = {};
      for (const [k, i] of Object.entries(tf)) if (i.value !== (d.title[k] || "")) title[k] = i.value;
      try { await api("/api/settings", { document: document_, title }); toast("Настройки сохранены"); close(); refreshSoon(); } catch (e) { fail(e); }
    } }, "Сохранить"));
  });
}
$("#settings-btn").onclick = settingsDialog;

function showBuildErrors() {
  const b = S.state.build;
  dialog("PDF не собран", (body, foot, close) => {
    (b.errors || []).forEach((e) => body.append(h("div", { class: "issue" }, h("span", { class: "sev error" }),
      h("div", { class: "body" }, h("div", {}, e.message), h("div", { class: "where" }, [e.element, e.file && `${e.file}:${e.line}`].filter(Boolean).join(" · "))))));
    body.append(h("div", { class: "hint" }, "Показан последний удачный PDF. Исправьте элемент — PDF обновится сам."));
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"));
  });
}
window.showBuildErrors = showBuildErrors;

/* ------------------------------------------------------- агент (MCP) ---- */
async function agentDialog() {
  let info;
  try { info = await api("/api/mcp"); } catch (e) { return fail(e); }
  S.mcpOn = info.running;
  const copy = (txt, what) => navigator.clipboard.writeText(txt).then(() => toast(what + " скопирован" + (what.endsWith("а") ? "а" : "")), () => toast("Не удалось скопировать", { err: true }));
  const snip = (title, txt, hint) => h("div", { class: "mcp-snip" },
    h("div", { class: "row" }, h("b", {}, title), h("span", { class: "grow" }),
      h("button", { class: "btn small", onclick: () => copy(txt, "Фрагмент") }, "Копировать")),
    hint ? h("div", { class: "muted small" }, hint) : null, h("pre", { class: "fb-text" }, txt));
  dialog("Подключить агента (MCP)", (body, foot, close) => {
    body.classList.add("mcp-dialog");
    body.append(h("div", { class: "hint" }, "ИИ-агент (DSH, Claude Code и др.) подключается к LDE по MCP-ссылке. Сервер сам расскажет агенту, что такое LDE, " +
      "какие у него правила и инструменты; агент работает в проекте, открытом в этом окне (или откроет и создаст проект сам). Каждая правка агента — " +
      "точка в «Истории и версиях», её можно отменить; PDF обновляется сам."));
    if (!info.running) {
      body.append(h("div", { class: "muted" }, `Сейчас выключено. Ссылка будет постоянной: http://127.0.0.1:${info.port}/mcp — только для этого компьютера.`));
      foot.append(h("button", { class: "btn primary", id: "mcp-start", onclick: async () => {
        try { await api("/api/mcp/start", {}); S.mcpOn = true; close(); agentDialog(); } catch (e) { fail(e); }
      } }, "Включить"), h("button", { class: "btn", onclick: close }, "Закрыть"));
      return;
    }
    body.append(h("div", { class: "row" }, h("b", {}, "Ссылка для агента"), h("span", { class: "grow" }),
      h("button", { class: "btn small primary", id: "mcp-copy-link", onclick: () => copy(info.link, "Ссылка") }, "Копировать ссылку")),
      h("input", { class: "inp mono", id: "mcp-link", readonly: true, value: info.link, onfocus: (e) => e.target.select() }),
      h("div", { class: "muted small" }, "В ссылке — ключ доступа: передавайте её только своему агенту. Агенту без MCP-клиента можно дать ту же ссылку — " +
        "по ней он прочитает описание и сможет вызывать инструменты обычными HTTP-запросами."));
    body.append(snip("DSH", info.dsh, "Сохраните в файл, например lde-mcp.yaml, и запустите: dsh --patch lde-mcp.yaml"),
      snip("Claude Code", info.claude, "Выполнить один раз в терминале"),
      snip("Другие клиенты (JSON)", info.json));
    const cl = info.clients || [];
    body.append(h("div", { class: "mh" }, "Активность"),
      h("div", { class: "muted small", id: "mcp-activity" }, cl.length ? `Подключались: ${cl.map((c) => c.name).join(", ")}. Вызовов инструментов: ${info.calls}.` : "Агент ещё не подключался."));
    (info.log || []).slice(0, 12).forEach((l) => body.append(h("div", { class: "small" + (l.error ? " err" : "") },
      h("span", { class: "muted" }, new Date(l.time * 1000).toLocaleTimeString().slice(0, 5) + " "), h("b", {}, l.tool), " — ", l.text)));
    foot.append(h("button", { class: "btn", title: "Старые ссылки перестанут работать", onclick: async () => {
      if (!confirm("Создать новый ключ? Подключённым агентам понадобится новая ссылка.")) return;
      await api("/api/mcp/new-key", {}); close(); agentDialog();
    } }, "Новый ключ"), h("button", { class: "btn", onclick: async () => { await api("/api/mcp/stop", {}); S.mcpOn = false; close(); toast("Подключение агентов выключено"); } }, "Выключить"),
      h("span", { style: "flex:1" }), h("button", { class: "btn primary", onclick: close }, "Готово"));
  });
}
window.agentDialog = agentDialog;

/* -------------------------------------------------------- project menu ---- */
function closeMenu() { const m = $("#proj-menu"); if (m) m.remove(); document.removeEventListener("mousedown", menuOutside, true); }
function menuOutside(e) { if (!e.target.closest("#proj-menu") && !e.target.closest("#brand")) closeMenu(); }
function projectMenu() {
  if ($("#proj-menu")) return closeMenu();
  const st = S.state, p = st && st.project;
  const item = (label, fn, opts = {}) => h("div", { class: "mi" + (opts.on ? " on" : ""), role: "menuitem", title: opts.title || "",
    onclick: () => { closeMenu(); fn(); } }, h("span", { class: "t" }, label), opts.note ? h("span", { class: "muted small" }, opts.note) : null);
  const m = h("div", { id: "proj-menu", role: "menu" });
  if (p) {
    m.append(h("div", { class: "mh" }, "Документы проекта"));
    (p.documents || []).forEach((d) => m.append(item(d.title, () => switchDoc(d.key),
      { on: d.key === p.doc, note: d.key === "main" ? "основной" : d.name, title: `out/${d.name}.pdf` })));
    m.append(item("+ Новый документ в этом проекте…", newDocumentDialog,
      { title: "Отдельный документ (ПЗ, ТЗ, статья…) с общими величинами, расчётами и источниками" }));
    m.append(h("div", { class: "sep" }), h("div", { class: "mh" }, "Проверка и сервер"));
    m.append(item("Поделиться документом…", shareDialog, { title: "Ссылка для руководителя: своя копия документа, правки и замечания" }));
    m.append(item("Ссылки и правки рецензентов…", sharesDialog));
    m.append(item("Синхронизировать с сервером", syncProject, { title: "Получить изменения соавторов (слияние по блокам) и отправить свои; проект на сервере зашифрован" }));
    m.append(item("Сервер LDE…", serverDialog));
    m.append(item("Обращения агентов…", feedbackDialog, { title: "Что агенты сообщили о работе в LDE: ошибки, нехватка инструментов, ручная рутина" }));
    m.append(h("div", { class: "sep" }));
  }
  m.append(item("Подключить агента (MCP)…", agentDialog, { title: "Ссылка для ИИ-агента: он сам узнает, что такое LDE, и будет работать в этом проекте", note: S.mcpOn ? "включено" : "" }));
  m.append(h("div", { class: "sep" }));
  m.append(item("Открыть проект…", openProjectDialog));
  const rec = (st.recent || []).filter((r) => !p || r.path !== p.root).slice(0, 6);
  if (rec.length) {
    m.append(h("div", { class: "mh" }, "Недавние"));
    rec.forEach((r) => m.append(item(r.name, () => api("/api/open-project", { path: r.path }).then(refresh, fail), { note: r.path.replace(/^\/home\/[^/]+/, "~"), title: r.path })));
  }
  m.append(h("div", { class: "sep" }));
  m.append(item("Новый отчёт…", newProject));
  const file = h("input", { type: "file", accept: ".docx", hidden: true, onchange: (e) => importDocx(e.target.files[0]) });
  m.append(item("Импорт из Word…", () => file.click()), file);
  if (p) m.append(h("div", { class: "sep" }), item("Закрыть проект", closeProject));
  $("#top").append(m);
  document.addEventListener("mousedown", menuOutside, true);
}
async function switchDoc(key) {
  if (S.state.project.doc === key) return;
  if (S.dirty && !confirm("В редакторе есть несохранённая правка. Переключить документ без неё?")) return;
  const v = currentView(); if (v) await api("/api/view", v).catch(() => {});
  S.dirty = false; closePanel();
  try { await api("/api/document", { key }); } catch (e) { return fail(e); }
  refresh();
}
async function closeProject() {
  if (S.dirty && !confirm("В редакторе есть несохранённая правка. Закрыть проект без неё?")) return;
  const v = currentView(); if (v) await api("/api/view", v).catch(() => {});
  S.dirty = false; closePanel();
  try { await api("/api/close-project", {}); } catch (e) { return fail(e); }
  refresh();
}
function newDocumentDialog() {
  dialog("Новый документ проекта", (body, foot, close) => {
    const title = h("input", { class: "inp", placeholder: "Пояснительная записка" });
    const key = h("input", { class: "inp", placeholder: "pz" });
    body.append(h("div", { class: "field" }, h("label", {}, "Название"), title),
      h("div", { class: "field" }, h("label", {}, "Короткое имя латиницей (папка и имя файла PDF/DOCX)"), key),
      h("div", { class: "hint" }, "Величины, расчёты, источники, сокращения и рисунки — общие с остальными документами. Текст, нумерация и список литературы — свои. Оформление и титул наследуются от проекта (меняются в настройках документа)."));
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"),
      h("button", { class: "btn primary", onclick: () => api("/api/new-document", { title: title.value.trim(), key: key.value.trim() })
        .then(() => { close(); refresh(); }, fail) }, "Создать"));
    setTimeout(() => title.focus(), 40);
  });
}
$("#brand").onclick = REVIEWER ? () => reviewerHelp() : projectMenu;

/* ------------------------------------------------- сервер LDE и ссылки ---- */
async function serverDialog() {
  let st = {};
  try { st = await api("/api/server/status"); } catch (e) { /* */ }
  dialog("Сервер LDE", (body, foot, close) => {
    if (st.connected) {
      body.append(h("div", {}, h("b", {}, st.url), ` — ${st.username}`, st.is_admin ? " (администратор)" : ""),
        h("div", { class: st.ok ? "hint" : "err" }, st.ok ? "Связь есть." : `Нет связи: ${st.error}`));
      foot.append(h("button", { class: "btn", onclick: async () => { await api("/api/server/logout", {}); close(); toast("Отключено"); } }, "Отключить это устройство"));
    }
    const url = h("input", { class: "inp", id: "srv-url", placeholder: "https://lde.<ip>.sslip.io", value: st.url || "" });
    const user = h("input", { class: "inp", id: "srv-user", placeholder: "имя", value: st.username || "" });
    const pw = h("input", { class: "inp", id: "srv-pw", type: "password", placeholder: "пароль" });
    body.append(h("div", { class: "lbl" }, st.connected ? "Войти заново / другой сервер" : "Подключить"),
      h("div", { class: "field" }, h("label", {}, "Адрес сервера"), url), h("div", { class: "field" }, h("label", {}, "Имя"), user),
      h("div", { class: "field" }, h("label", {}, "Пароль"), pw),
      h("div", { class: "hint" }, "Пароль не сохраняется и не уходит на сервер: из него здесь выводятся ключ входа и ключ шифрования. Регистрация — по приглашению администратора сервера."));
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"), h("button", { class: "btn primary", id: "srv-login", onclick: async () => {
      try { const r = await api("/api/server/login", { url: url.value, username: user.value, password: pw.value }); toast(`Подключено: ${r.username}`); close(); }
      catch (e) { fail(e); }
    } }, "Войти"));
  });
}
async function needServer() {
  let st = {};
  try { st = await api("/api/server/status"); } catch (e) { /* */ }
  if (st.connected && st.ok) return true;
  toast("Сначала подключите сервер LDE", { err: true });
  serverDialog();
  return false;
}
async function syncProject() {
  if (!(await needServer())) return;
  let info = {};
  try { info = await api("/api/remote"); } catch (e) { /* */ }
  if (!info.project) {
    if (!confirm("Проект ещё не на сервере. Опубликовать? История будет зашифрована на этом компьютере; доступ — вам, остальным — по вашему решению (в панели сервера).")) return;
    toast("Публикую проект…");
    try { const r = await api("/api/publish", {}); toast(`Проект опубликован (id ${r.id})`); } catch (e) { fail(e); }
    return;
  }
  toast("Синхронизирую…");
  try {
    const r = await api("/api/sync", {});
    const p = r.pull || {}, q = r.push || {};
    if (p.conflicts) toast(`Получено и слито; конфликтов: ${p.conflicts} — решите их (красные пометки, список замечаний)`, { err: true, ms: 9000 });
    else toast((p.merged ? (p.fast_forward ? "Получены изменения соавторов. " : "Изменения соавторов слиты с вашими. ") : "Новых изменений на сервере нет. ") + (q.pushed ? "Ваши изменения отправлены." : ""), { ms: 6000 });
    refreshSoon();
  } catch (e) { fail(e); }
}
async function shareDialog() {
  if (!(await needServer())) return;
  dialog("Поделиться документом", (body, foot, close) => {
    const label = h("input", { class: "inp", id: "sh-label", placeholder: "Например: Иванов И. И., научный руководитель" });
    const perms = h("select", { class: "inp", id: "sh-perms" }, h("option", { value: "edit" }, "исправлять текст и оставлять замечания"), h("option", { value: "comment" }, "только замечания"));
    const days = h("input", { class: "inp", type: "number", min: 1, value: 30, id: "sh-days" });
    const pin = h("input", { class: "inp", placeholder: "необязательно", id: "sh-pin", inputmode: "numeric" });
    const pdf = h("input", { type: "checkbox", checked: true, id: "sh-pdf" });
    body.append(h("div", { class: "field" }, h("label", {}, "Кому"), label), h("div", { class: "field" }, h("label", {}, "Может"), perms),
      h("div", { class: "row" }, h("div", { class: "field" }, h("label", {}, "Срок, дней"), days), h("div", { class: "field" }, h("label", {}, "PIN-код"), pin)),
      h("label", { class: "check" }, pdf, " показать и PDF (вёрстку)"),
      h("div", { class: "hint" }, "Текущая версия документа будет зафиксирована: ваши дальнейшие правки получатель не увидит. Он работает в своей копии; правки и замечания вы заберёте и примете или отклоните по каждому месту. Документ шифруется ключом из ссылки — сервер его не прочитает."));
    const res = h("div");
    body.append(res);
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"), h("button", { class: "btn primary", id: "sh-go", onclick: async (e) => {
      e.target.disabled = true;
      res.replaceChildren(h("div", { class: "hint" }, h("span", { class: "spin" }), " Фиксирую версию, шифрую и загружаю…"));
      try {
        const r = await api("/api/share/new", { label: label.value, perms: perms.value, days: days.value, pin: pin.value, pdf: pdf.checked });
        res.replaceChildren(h("div", { class: "lbl" }, "Ссылка (передайте целиком — часть после # это ключ):"),
          h("textarea", { class: "inp", id: "sh-link", rows: 3, readonly: true, style: "height:auto" }, r.link),
          h("button", { class: "btn", onclick: () => navigator.clipboard.writeText(r.link).then(() => toast("Скопировано")) }, "Копировать"),
          pin.value ? h("div", { class: "hint" }, "PIN сообщите отдельно (не в том же сообщении, что ссылку).") : null);
        e.target.remove();
      } catch (er) { res.replaceChildren(); fail(er); e.target.disabled = false; }
    } }, "Создать ссылку"));
    setTimeout(() => label.focus(), 40);
  });
}
async function sharesDialog() {
  if (!(await needServer())) return;
  let list;
  try { list = await api("/api/shares"); } catch (e) { return fail(e); }
  dialog("Ссылки и правки рецензентов", (body, foot, close) => {
    if (!list.length) body.append(h("div", { class: "muted" }, "Ссылок на этот документ нет. «Поделиться документом…» — в меню проекта."));
    list.forEach((r) => {
      const state = r.revoked ? ["err", "отозвана"] : r.expires && r.expires < Date.now() / 1000 ? ["warn", "истекла"] : ["ok", "действует"];
      body.append(h("div", { class: "ver" },
        h("div", { class: "t" }, h("b", {}, r.label), " ", h("span", { class: "badge " + state[0] }, state[1]),
          h("div", { class: "muted small" }, `${r.version_name} · открывали: ${r.opens} · ${r.copy_ver ? "правки сохранены " + fmtTime(r.copy_updated) : "правок пока нет"}`)),
        h("button", { class: "btn", onclick: () => navigator.clipboard.writeText(r.link).then(() => toast("Ссылка скопирована")) }, "Ссылка"),
        h("button", { class: "btn", onclick: async () => { await api("/api/share/revoke", { id: r.id, revoked: !r.revoked }); close(); sharesDialog(); } }, r.revoked ? "Вернуть" : "Отозвать"),
        h("button", { class: "btn primary", "data-share": r.id, onclick: () => { close(); reviewMode(r.id); } }, "Разобрать правки")));
    });
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"));
  });
}
async function pullDialog(id) {
  let rv;
  toast("Забираю правки рецензента…");
  try { rv = await api(`/api/share/pull?id=${encodeURIComponent(id)}`); } catch (e) { return fail(e); }
  const ST = { clean: ["ok", "применится как есть"], same: ["", "уже так"], merge: ["accent", "сольётся с вашими правками"], conflict: ["err", "конфликт с вашими правками"], deleted: ["warn", "фрагмента у вас нет"] };
  const DEC = { accepted: "принято", rejected: "отклонено", partial: "принято с правкой" };
  dialog(`Правки: ${rv.name}`, (body, foot, close) => {
    const decide = async (it, how, text) => {
      try {
        const r = await api("/api/share/decide", { id, block: it.block, how, text });
        if (r.conflict) { close(); return reviewConflict(id, it, r); }
        toast(how === "reject" ? "Отклонено" : "Принято"); close(); refreshSoon(); pullDialog(id);
      } catch (e) { fail(e); }
    };
    body.append(h("div", { class: "hint" }, `Версия «${rv.version_name}». Принятое попадает в вашу текущую версию отдельной точкой (её можно отменить). Рецензент увидит решения по своей ссылке.` + (rv.branch ? ` Все правки целиком — в ветке ${rv.branch}.` : "")));
    if (!rv.items.length) body.append(h("div", { class: "muted" }, "Исправлений текста нет."));
    rv.items.forEach((it) => {
      const st = ST[it.status] || ["", it.status];
      const box = h("div", { class: "chg" }, h("div", { class: "chg-head" },
        h("span", { class: "op changed" }, "правка"), h("span", { class: "t" }, it.where),
        h("span", { class: "badge " + st[0] }, st[1]), it.decision ? h("span", { class: "badge" }, DEC[it.decision] || it.decision) : null,
        it.uid ? h("button", { class: "btn", onclick: () => { close(); openElement(it.uid, true); } }, "Показать") : null));
      box.append(wordsView(it.words));
      if (!it.decision || it.decision === "rejected") {
        const row = h("div", { class: "row", style: "margin-top:6px" });
        if (it.status === "conflict") row.append(h("button", { class: "btn primary", onclick: () => { close(); reviewConflict(id, it, it); } }, "Разрешить конфликт…"));
        else if (it.status !== "same" && it.status !== "deleted") row.append(h("button", { class: "btn primary", "data-accept": it.block, onclick: () => decide(it, "accept") }, "Принять"));
        if (it.decision !== "rejected") row.append(h("button", { class: "btn", onclick: () => decide(it, "reject") }, "Отклонить"));
        box.append(row);
      }
      body.append(box);
    });
    if (rv.comments.length) body.append(h("div", { class: "lbl" }, `Замечания (${rv.comments.length})`));
    rv.comments.forEach((c) => {
      const ta = h("textarea", { class: "inp", rows: 2, style: "height:auto", placeholder: "Ответ рецензенту (необязательно)" });
      const send = async (status) => { try { await api("/api/share/reply", { id, comment: c.id, text: ta.value.trim(), status }); toast("Отправлено рецензенту"); close(); pullDialog(id); } catch (e) { fail(e); } };
      body.append(h("div", { class: "chg" },
        h("div", { class: "chg-head" }, h("span", { class: "t" }, c.where || "общее"), c.status ? h("span", { class: "badge" }, { done: "выполнено", rejected: "отклонено" }[c.status]) : null,
          c.uid ? h("button", { class: "btn", onclick: () => { close(); openElement(c.uid, true); } }, "Показать") : null),
        c.quote ? h("div", { class: "hint" }, "«" + c.quote + "»") : null, h("div", {}, c.text),
        ...(c.replies || []).map((r) => h("div", { class: "hint" }, "Вы: " + r.text)),
        ta, h("div", { class: "row" }, h("button", { class: "btn", onclick: () => send(null) }, "Ответить"),
          h("button", { class: "btn", onclick: () => send("done") }, "Выполнено"), h("button", { class: "btn", onclick: () => send("rejected") }, "Отклонить"))));
    });
    (rv.rejected_unsafe || []).forEach((b) => body.append(h("div", { class: "hint err" }, `Правка ${b} отклонена автоматически: меняла вид элемента.`)));
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"),
      h("button", { class: "btn primary", id: "pull-accept-all", onclick: async () => {
        for (const it of rv.items) if (!it.decision && (it.status === "clean" || it.status === "merge")) await api("/api/share/decide", { id, block: it.block, how: "accept" }).catch(fail);
        toast("Приняты все правки без конфликтов"); close(); refreshSoon(); pullDialog(id);
      } }, "Принять все без конфликтов"));
  });
}
function reviewConflict(id, it, c) {
  dialog("Конфликт: правили оба", (body, foot, close) => {
    const take = async (text, what) => {
      try { await api("/api/share/decide", { id, block: it.block, how: "own", text }); toast(`Записано: ${what}`); close(); refreshSoon(); pullDialog(id); } catch (e) { fail(e); }
    };
    body.append(h("div", { class: "hint" }, "Этот фрагмент изменили и вы (после выдачи ссылки), и рецензент — в одном месте."),
      h("div", { class: "cf" }, h("div", { class: "cf-col ours" }, h("div", { class: "lbl" }, "У меня сейчас"), h("div", { class: "diff words" }, c.ours || "")),
        h("div", { class: "cf-col theirs" }, h("div", { class: "lbl" }, "У рецензента"), h("div", { class: "diff words" }, c.theirs || ""))));
    const ta = h("textarea", { class: "inp", rows: 5, style: "height:auto" });
    ta.value = c.ours || "";
    body.append(h("div", { class: "field" }, h("label", {}, "Свой вариант"), ta));
    foot.append(h("button", { class: "btn", onclick: close }, "Позже"), h("button", { class: "btn", onclick: () => take(c.ours, "моё") }, "Оставить моё"),
      h("button", { class: "btn primary", onclick: () => take(c.theirs, "вариант рецензента") }, "Взять рецензента"),
      h("button", { class: "btn", onclick: () => take(ta.value.trim(), "свой вариант") }, "Свой вариант"));
  });
}

/* -------------------------------------------------------- top buttons ---- */
$("#theme-btn").onclick = () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
$("#undo-btn").onclick = async () => {
  try { const r = await api("/api/undo", {}); toast(`Отменено: ${r.undone}`, { ms: 6000, action: { label: "Повторить", fn: redo } }); refreshSoon(); } catch (e) { fail(e); }
};
$("#history-btn").onclick = historyDialog;
$("#open-folder").onclick = () => dialog("Открыть", (body, foot, close) => {
  const act = (label, what) => h("div", { class: "item", onclick: () => { close(); api("/api/open", { what }).catch(fail); } }, h("span", { class: "t" }, label));
  body.append(act("Папка проекта", "folder"), act("Проект в VS Code", "vscode"), act("PDF во внешней программе (по умолчанию в системе)", "pdf"));
  body.append(h("div", { class: "hint" }, S.state && S.state.project ? S.state.project.root : ""));
});
function openPdf() {
  if (BACK) { if (!BACK.pdf()) toast("PDF к этой ссылке не приложен", { err: true }); return; }
  const w = window.open(`/api/pdf?v=${Date.now()}`, "_blank");
  if (!w) toast("Окно PDF не открылось — разрешите всплывающие окна", { err: true });
}
$("#export-pdf").onclick = async () => {
  try { await api("/api/export", { format: "pdf" }); openPdf(); } catch (e) { fail(e); }
};
$("#export-docx").onclick = async () => {
  toast("Готовлю DOCX для Word… это занимает полминуты", { ms: 6000 });
  try { const r = await api("/api/export", { format: "docx" }); toast(r.summary || `DOCX: ${r.path}`, { ms: 8000, action: { label: "Открыть", fn: () => api("/api/open", { what: "docx" }) } }); } catch (e) { fail(e); }
};

$("#submit-btn").onclick = async () => {
  try { await api("/api/submit", {}); } catch (e) { return fail(e); }
  S.submitDlg = dialog("Сдача", (body, foot, close) => {
    body.append(h("div", { id: "submit-progress" }, h("span", { class: "spin" }), " Начинаю…"));
    foot.append(h("button", { class: "btn", onclick: close }, "Скрыть"));
  });
};
function submitEvent(ev) {
  const box = $("#submit-progress");
  if (ev.state === "running") {
    if (box) box.innerHTML = `<span class="spin"></span> Шаг ${ev.step} из ${ev.total}: ${esc(ev.text)}…`;
    return;
  }
  const show = (body, foot, close) => {
    body.append(h("div", { class: "pill " + (ev.ok ? "ok" : "err"), style: "height:auto;padding:8px 14px;font-size:14px" },
      ev.ok ? "Можно сдавать" : "Пока нельзя сдавать — вот что мешает"));
    (ev.blockers || []).forEach((b) => body.append(h("div", { class: "issue" }, h("span", { class: "sev error" }), h("div", { class: "body" }, b))));
    if (ev.ok) body.append(h("div", {}, `PDF и DOCX — в папке out проекта. Текст DOCX совпадает с PDF на ${ev.docx_coverage}.`));
    if ((ev.notes || []).length) body.append(h("details", {}, h("summary", {}, `Замечания, не мешающие сдаче (${ev.notes.length})`), ...ev.notes.map((n) => h("div", { class: "hint" }, "• " + n))));
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"));
    if (ev.ok) foot.append(h("button", { class: "btn", onclick: () => api("/api/open", { what: "folder" }) }, "Открыть папку"),
      h("button", { class: "btn", onclick: openPdf }, "Открыть PDF"),
      h("button", { class: "btn primary", onclick: () => api("/api/open", { what: "docx" }) }, "Открыть DOCX"));
  };
  dialog(ev.ok ? "Готово к сдаче" : "Сдача: есть препятствия", show);
  refreshSoon();
}

/* ------------------------------------------------------------ hotkeys ---- */
document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (e.key === "Escape") {
    if ($("#proj-menu")) { closeMenu(); return; }
    if (!$("#dialog-wrap2").hidden) { $("#dialog-wrap2").hidden = true; return; }
    if (!$("#dialog-wrap").hidden) { $("#dialog-wrap").hidden = true; return; }
    if (!$("#panel").hidden) closePanel();
  } else if (mod && e.key.toLowerCase() === "s" || (mod && e.key === "Enter")) {
    e.preventDefault(); if (S.cur) saveCurrent();
  } else if (mod && e.key.toLowerCase() === "k") {
    if (S.editor && S.editor.ed) { e.preventDefault(); picker(S.editor.ed, "q"); }
  } else if (mod && e.key.toLowerCase() === "e") { e.preventDefault(); $("#export-docx").click(); }
  else if (mod && e.altKey && e.shiftKey && e.key.toLowerCase() === "z") { e.preventDefault(); redo(); }
  else if (mod && e.altKey && e.key.toLowerCase() === "z") { e.preventDefault(); $("#undo-btn").click(); }
  else if (mod && (e.key === "=" || e.key === "+")) { e.preventDefault(); zoom(1.15); }
  else if (mod && e.key === "-") { e.preventDefault(); zoom(1 / 1.15); }
  else if (mod && e.key === ",") { e.preventDefault(); settingsDialog(); }
  else if (S.rv && e.key === "F7") { e.preventDefault(); rvStep(e.shiftKey ? -1 : 1); }
});

/* ------------------------------------------------------------ resizers ---- */
function resizer(id, target, sign) {
  const r = $(id);
  r.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const start = e.clientX, w0 = $(target).offsetWidth;
    r.classList.add("drag");
    const mv = (ev) => { $(target).style.width = Math.max(200, w0 + sign * (ev.clientX - start)) + "px"; };
    const up = () => { r.classList.remove("drag"); removeEventListener("mousemove", mv); removeEventListener("mouseup", up); renderPages(S.pages); };
    addEventListener("mousemove", mv); addEventListener("mouseup", up);
  });
}
resizer("#rs-left", "#side", 1);
resizer("#rs-right", "#panel", -1);

/* -------------------------------------------------------------- events ---- */
function connect() {
  const es = new EventSource("/api/events");
  es.onopen = () => refreshSoon();
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "build") {
      if (S.state) S.state.build = { ...S.state.build, state: ev.state, duration: ev.duration, errors: ev.errors };
      renderBuild(S.state && S.state.build);
      if (ev.state === "ok") {
        renderPages(ev.pages);
        if (S.saveT0) { window.__lastSaveToPdfMs = performance.now() - S.saveT0; S.saveT0 = null; }
        if (S.state && !S.state.pages.length) refreshSoon();
      }
      if (ev.state === "error") toast("PDF не обновлён: " + ((ev.errors && ev.errors[0] && ev.errors[0].message) || "ошибка"), { err: true, action: { label: "Подробнее", fn: showBuildErrors } });
    } else if (ev.type === "status") refreshSoon();
    else if (ev.type === "review") rvLoadRight();
    else if (ev.type === "submit") submitEvent(ev);
    else if (ev.type === "agent") {
      if (ev.changed) toast("Агент: " + ev.text);
      else if (ev.error) toast("Агент: ошибка инструмента " + ev.tool + " — " + ev.text, { err: true });
      refreshSoon();
    } else if (ev.type === "external") { toast("Подхвачены изменения из файлов (агент или редактор)"); refreshSoon(); }
    else if (ev.type === "calc") {
      if (ev.state === "running") toast(`Пересчёт: ${ev.title}…`);
      else if (ev.state === "ok") toast(`Расчёт «${ev.title}» готов за ${String(ev.duration).replace(".", ",")} с`);
      else toast(`Расчёт «${ev.title}» упал: ${ev.error}. Прошлые результаты сохранены.`, { err: true, action: { label: "Журнал", fn: () => openCalc(ev.key) } });
      refreshSoon();
    }
  };
  es.onerror = () => { es.close(); setTimeout(connect, 1500); };
}

if (window.MathfieldElement) {
  MathfieldElement.soundsDirectory = null;
  MathfieldElement.fontsDirectory = null;
}
if (BACK) {
  // Studio рецензента: данные — из снимка по ссылке; событий сервера нет
  BACK.onChange = () => { if (S.state) { refreshSoon(); } };
  BACK.ready.then(async () => { await refresh(); reviewerStart(); }, (e) => {
    $("#main").hidden = true;
    const st = $("#start");
    st.hidden = false;
    st.replaceChildren(h("div", { class: "start-card" }, h("h1", {}, "Документ не открыть"), h("div", { class: "err" }, e.message || String(e))));
  });
} else {
  connect();
  refresh();
}

/* ------------------------------------------------------ роль рецензента ---- */
function reviewerSelectionQuote() {
  const s = getSelection();
  if (!s.rangeCount || s.isCollapsed) return "";
  const n = s.anchorNode && (s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement);
  return n && n.closest("#panel") ? s.toString().replace(/\s+/g, " ").trim().slice(0, 500) : "";
}
function reviewerComment(d, existing) {
  const quote = existing ? existing.quote : reviewerSelectionQuote();
  dialog(existing ? "Изменить замечание" : "Замечание", (body, foot, close) => {
    body.append(h("div", { class: "hint" }, d ? d.title : "общее"), quote ? h("div", { class: "hint" }, "«" + quote + "»") : null);
    const ta = h("textarea", { class: "inp", rows: 5, id: "cm-text", style: "height:auto", placeholder: "Что не так и как исправить" });
    ta.value = existing ? existing.text : "";
    body.append(ta);
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"), h("button", { class: "btn primary", id: "cm-save", onclick: async () => {
      try { await api("/api/comment", { id: existing && existing.id, uid: d ? d.uid : null, quote, text: ta.value }); close(); toast("Замечание записано"); refreshSoon(); if (S.tab === "mine") renderSide(); }
      catch (e) { fail(e); }
    } }, "Сохранить"));
    setTimeout(() => ta.focus(), 40);
  });
}
function reviewerReadonly(d, body, foot, saveBtn) {
  if (d.kind === "group") {
    // титул, сокращения, список источников: строки по отдельности
    const eds = [];
    body.append(h("div", { class: "hint" }, d.editable ? "Исправьте нужные строки и нажмите «Сохранить». Выделите слова и нажмите «Замечание…» — замечание к ним." : "Выделите слова и нажмите «Замечание…»."));
    d.items.forEach((it) => {
      const row = h("div", { class: "field rv-row" }, it.label ? h("label", {}, it.label) : null);
      if (it.latex !== undefined) row.append(h("div", { html: mathHTML(it.latex) + " – " + esc(it.text || "") }));
      else if (it.editable) { const ed = new ChipEditor(it.tokens, { onChip: chipClick, small: true }); eds.push([it, ed]); row.append(ed.el); }
      else row.append(h("div", { class: "chips small ro" }, (it.tokens || []).map((t) => t.v || t.text || "").join("")));
      body.append(row);
    });
    if (eds.length) {
      saveBtn.onclick = async () => {
        try { for (const [it, ed] of eds) await api("/api/element/save", { uid: it.uid, base: "", tokens: ed.tokens() }); S.dirty = false; toast("Записано в вашу копию"); refreshSoon(); }
        catch (e) { fail(e); }
      };
    } else saveBtn.remove();
    return;
  }
  saveBtn.remove();
  body.append(h("div", { class: "hint" }, "Этот элемент можно только прокомментировать: кнопка «Замечание…» внизу."));
  if (d.kind === "formula" && d.latex) body.append(h("div", { class: "fig-prev", html: mathHTML(d.latex) }));
}
async function reviewerMine(list) {
  let m;
  try { m = await api("/api/mine"); } catch (e) { return fail(e); }
  const DEC = { accepted: ["ok", "автор принял"], rejected: ["err", "автор отклонил"], partial: ["ok", "принято частично"] };
  if (!m.edits.length && !m.comments.length) list.append(h("div", { class: "muted small", style: "padding:10px" }, "Пока ничего. Щёлкните по месту в документе — справа откроется редактор; там же «Замечание…»."));
  if (m.edits.length) list.append(h("div", { class: "group-title" }, "Исправления"));
  m.edits.forEach((e) => list.append(h("div", { class: "item", onclick: () => openElement(e.uid, true) },
    h("span", { class: "t", title: e.snippet || "" }, e.where, e.snippet ? h("span", { class: "muted" }, " · " + e.snippet) : null), e.decision ? h("span", { class: "badge " + DEC[e.decision][0] }, DEC[e.decision][1]) : null,
    h("button", { class: "btn small", title: "Убрать исправление (вернуть как у автора)", onclick: async (ev) => { ev.stopPropagation(); if (confirm("Убрать это исправление?")) { await api("/api/edit/drop", { uid: e.uid }); refreshSoon(); renderSide(); } } }, "×"))));
  if (m.comments.length) list.append(h("div", { class: "group-title" }, "Замечания"));
  m.comments.forEach((c) => list.append(h("div", { class: "item rv-cm", onclick: () => c.block && openElement(c.block, true) },
    h("div", { class: "t" }, h("div", { class: "muted small" }, c.where, c.status ? h("span", { class: "badge" }, { done: " · выполнено", rejected: " · отклонено" }[c.status]) : null),
      c.quote ? h("div", { class: "muted small" }, "«" + c.quote + "»") : null, h("div", {}, c.text),
      ...(c.replies || []).map((r) => h("div", { class: "hint" }, "Автор: " + r.text))),
    h("button", { class: "btn small", onclick: (ev) => { ev.stopPropagation(); reviewerComment(c.block ? { uid: c.block, title: c.where } : null, c); } }, "✎"),
    h("button", { class: "btn small", onclick: async (ev) => { ev.stopPropagation(); if (confirm("Удалить замечание?")) { await api("/api/comment/delete", { id: c.id }); refreshSoon(); renderSide(); } } }, "×"))));
  $("#side-foot").replaceChildren(h("button", { class: "btn", onclick: () => reviewerComment(null) }, "+ Общее замечание"));
}
function reviewerHelp() {
  const rv = (S.state && S.state.reviewer) || {};
  dialog("Как это работает", (body, foot, close) => {
    body.append(h("p", {}, `Это ваша копия документа «${S.state ? S.state.project.title : ""}», версия «${rv.version || ""}». Автор зафиксировал её и прислал ссылку.`),
      h("p", {}, "Страницы — точно как у автора. Щёлкните по месту — справа откроется редактор, как у автора: " +
        (rv.perms === "edit" ? "исправьте текст или формулу и нажмите «Сохранить»; вставки (числа, ссылки, величины) выделены — их можно удалить или вставить другие кнопками «+ …»." : "исправлять по этой ссылке нельзя, только замечания.")),
      h("p", {}, "«Замечание…» — замечание к месту (выделите слова — к ним). Всё видно во вкладке «Мои правки»; исправленные места отмечены на полях."),
      h("p", {}, "Страницы не перерисовываются после ваших исправлений: новый вид появится у автора, когда он их примет."),
      h("p", { class: "muted small" }, "Документ и ваши правки зашифрованы ключом из ссылки; сервер их прочитать не может. Не пересылайте ссылку другим."));
    foot.append(h("button", { class: "btn primary", onclick: close }, "Понятно"));
  });
}
async function reviewerStart() {
  document.body.classList.add("role-reviewer");
  const rv = S.state.reviewer;
  if (!rv.name) {
    await new Promise((res) => dialog("Как вас подписать?", (body, foot, close) => {
      const inp = h("input", { class: "inp", id: "rv-name", placeholder: "Например: Иванов И. И." });
      body.append(h("div", { class: "hint" }, "Имя увидит автор документа рядом с вашими исправлениями и замечаниями."), inp);
      foot.append(h("button", { class: "btn primary", id: "rv-name-ok", onclick: async () => { await api("/api/name", { name: inp.value }); close(); res(); } }, "Продолжить"));
      setTimeout(() => inp.focus(), 40);
    }));
  }
}
$("#help-btn").onclick = () => reviewerHelp();


/* ---------------------------------------- режим разбора правок рецензента ---- */
async function reviewMode(id) {
  toast("Забираю правки рецензента и собираю его версию документа…", { ms: 5000 });
  let rv;
  try { rv = await api("/api/review/start", { id }); } catch (e) { return fail(e); }
  S.rv = { ...rv, id, i: -1, right: null };
  document.body.classList.add("reviewing");
  $("#pages-r").hidden = false;
  $("#pages-r").replaceChildren(h("div", { class: "rv-wait" }, h("span", { class: "spin" }), " Собираю документ «как у рецензента»…"));
  S.tab = "review";
  renderSide();
  renderPages(S.pages);
  if (rv.items.length) rvSelect(0);
  else toast("Исправлений текста нет — только замечания", { ms: 5000 });
}
async function rvLoadRight() {
  if (!S.rv) return;
  let st;
  try { st = await api("/api/review/state"); } catch (e) { return; }
  if (st.building) return;
  if (st.error) { $("#pages-r").replaceChildren(h("div", { class: "rv-wait" }, "Версию рецензента собрать не удалось: " + st.error)); return; }
  S.rv.right = st.pages;
  renderPagesR();
  if (S.rv.i >= 0) rvSelect(S.rv.i, true);
}
function renderPagesR() {
  const box = $("#pages-r"), pages = S.rv && S.rv.right;
  if (!pages) return;
  box.replaceChildren();
  const w = Math.max(300, Math.min(box.clientWidth - 60, 900) * S.zoom);
  const pxw = Math.ceil((w * devicePixelRatio) / 100) * 100;
  pages.forEach((p) => {
    const d = h("div", { class: "page rpage", "data-rn": p.n }, h("img", { src: `/api/review/page/${p.n}?w=${pxw}&h=${p.hash}`, loading: "lazy", alt: "" }), h("span", { class: "pnum" }, String(p.n)));
    d.style.width = w + "px";
    d.style.height = (w * p.h) / p.w + "px";
    d.dataset.h = p.h;
    box.append(d);
  });
}
function rvHighlightRight(rects) {
  $$("#pages-r .hl").forEach((x) => x.remove());
  if (!rects || !rects.length) return;
  for (const r of rects) {
    const d = $(`#pages-r .page[data-rn="${r.page}"]`);
    if (!d) continue;
    const H = +d.dataset.h, ch = d.clientHeight;
    d.append(h("div", { class: "hl r", style: `top:${(r.top / H) * ch - 3}px;height:${Math.max(14, ((r.bottom - r.top) / H) * ch + 6)}px` }));
  }
  const d = $(`#pages-r .page[data-rn="${rects[0].page}"]`);
  if (d) $("#pages-r").scrollTo({ top: Math.max(0, d.offsetTop + (rects[0].top / +d.dataset.h) * d.clientHeight - 120), behavior: "smooth" });
}
async function rvSelect(i, keepScroll) {
  const rv = S.rv;
  if (!rv || !rv.items[i]) return;
  rv.i = i;
  renderSide();
  const it = rv.items[i];
  if (it.uid) {
    try { const r = await api(`/api/rects?uid=${encodeURIComponent(it.uid)}`); S.hl = r.rects; drawHighlight(); if (!keepScroll || true) scrollToRects(r.rects); } catch (e) { /* */ }
  }
  if (rv.right && it.uid_tag) {
    try { const r = await api(`/api/review/rects?uid=${encodeURIComponent(it.uid_tag)}`); rvHighlightRight(r.rects); } catch (e) { /* */ }
  }
}
function rvStep(dir) {
  const rv = S.rv;
  if (!rv || !rv.items.length) return;
  rvSelect((rv.i + dir + rv.items.length) % rv.items.length);
}
function rvDiffView(r) {
  if (!r) return h("div");
  if (r.formula) return h("div", { class: "diff" }, h("div", { class: "fml" }, h("div", { class: "lbl2" }, "было"), h("div", { html: mathHTML(r.formula.before) })),
    h("div", { class: "fml" }, h("div", { class: "lbl2" }, "у рецензента"), h("div", { html: mathHTML(r.formula.after) })));
  const box = h("div", { class: "diff" });
  for (const op of r.ops || []) {
    const wrap = op.op === "=" ? box : h(op.op === "+" ? "ins" : "del");
    for (const x of op.items) {
      if (x.t === "text") wrap.append(x.v);
      else wrap.append(h("span", { class: "chip " + (x.kind || ""), title: x.title || "", html: (x.kind === "q" || x.kind === "math") && x.latex ? mathHTML(x.latex) : esc(x.text) }));
    }
    if (wrap !== box) box.append(wrap);
  }
  return box;
}
const RV_ST = { clean: ["ok", "применится как есть"], same: ["", "уже так"], merge: ["accent", "сольётся с вашими правками"], conflict: ["err", "вы тоже правили это место"], deleted: ["warn", "фрагмента у вас нет"] };
const RV_DEC = { accepted: ["ok", "принято"], rejected: ["err", "отклонено"], partial: ["ok", "совмещено"] };
function rvSide(list, foot) {
  const rv = S.rv;
  if (!rv) return;
  const done = rv.items.filter((x) => x.decision).length;
  list.append(h("div", { class: "rv-head" }, h("div", { class: "t" }, `Правки: ${rv.name}`), h("div", { class: "muted small" }, rv.version_name),
    h("div", { class: "muted small" }, `Решено ${done} из ${rv.items.length}` + (rv.comments.length ? ` · замечаний: ${rv.comments.length}` : "")),
    h("div", { class: "rv-nav" }, h("button", { class: "btn", id: "rv-prev", title: "Предыдущая правка (Shift+F7)", onclick: () => rvStep(-1) }, "◀ Предыдущая"),
      h("button", { class: "btn", id: "rv-next", title: "Следующая правка (F7)", onclick: () => rvStep(1) }, "Следующая ▶"))));
  const it = rv.items[rv.i];
  if (it) {
    const st = RV_ST[it.status] || ["", it.status];
    const card = h("div", { class: "rv-card" }, h("div", { class: "row" }, h("b", {}, it.where), h("span", { class: "muted small" }, `${rv.i + 1} из ${rv.items.length}`), h("span", { class: "badge " + st[0] }, st[1]),
      it.decision ? h("span", { class: "badge " + RV_DEC[it.decision][0] }, RV_DEC[it.decision][1]) : null), rvDiffView(it.render));
    const act = h("div", { class: "row" });
    if (it.status === "conflict") act.append(h("button", { class: "btn primary", id: "rv-merge", onclick: () => rvMerge(it) }, "Совместить…"));
    else if (it.status !== "same" && it.status !== "deleted") act.append(h("button", { class: "btn primary", id: "rv-accept", onclick: () => rvDecide(it, "accept") }, "Принять"));
    act.append(h("button", { class: "btn", id: "rv-reject", onclick: () => rvDecide(it, "reject") }, "Отклонить"));
    if (it.status !== "conflict" && it.status !== "deleted") act.append(h("button", { class: "btn", id: "rv-merge2", title: "Взять часть правки, дописать своё", onclick: () => rvMerge(it) }, "Совместить…"));
    card.append(act);
    list.append(card);
  }
  rv.items.forEach((x, i) => list.append(h("div", { class: "rv-item" + (i === rv.i ? " sel" : ""), onclick: () => rvSelect(i) },
    h("span", { class: "t", title: x.snippet || "" }, x.where, x.snippet ? h("span", { class: "muted" }, " · " + x.snippet) : null), x.decision ? h("span", { class: "badge " + RV_DEC[x.decision][0] }, RV_DEC[x.decision][1]) : h("span", { class: "badge " + (RV_ST[x.status] || [""])[0] }, "?"))));
  if (rv.comments.length) list.append(h("div", { class: "group-title" }, "Замечания рецензента"));
  rv.comments.forEach((c) => {
    const ta = h("textarea", { class: "inp", rows: 2, style: "height:auto", placeholder: "Ответ (необязательно)" });
    const send = async (status) => { try { await api("/api/share/reply", { id: rv.id, comment: c.id, text: ta.value.trim(), status }); c.status = status || c.status; if (ta.value.trim()) (c.replies = c.replies || []).push({ text: ta.value.trim() }); toast("Отправлено рецензенту"); renderSide(); } catch (e) { fail(e); } };
    list.append(h("div", { class: "rv-card" }, h("div", { class: "row" }, h("b", {}, c.where || "общее"), c.status ? h("span", { class: "badge" }, { done: "выполнено", rejected: "отклонено" }[c.status]) : null,
      c.uid ? h("button", { class: "btn small", onclick: () => openElement(c.uid, true) }, "Показать") : null),
      c.quote ? h("div", { class: "hint" }, "«" + c.quote + "»") : null, h("div", {}, c.text), ...(c.replies || []).map((r) => h("div", { class: "hint" }, "Вы: " + r.text)),
      ta, h("div", { class: "row" }, h("button", { class: "btn small", onclick: () => send(null) }, "Ответить"), h("button", { class: "btn small", onclick: () => send("done") }, "Выполнено"), h("button", { class: "btn small", onclick: () => send("rejected") }, "Отклонить"))));
  });
  foot.replaceChildren(h("div", { class: "rv-foot" },
    h("button", { class: "btn primary", id: "rv-finish", disabled: done < rv.items.length || undefined, title: done < rv.items.length ? "Сначала решите все правки" : "Зафиксировать версию и закончить разбор", onclick: rvFinish }, "Завершить"),
    h("button", { class: "btn", onclick: rvExit, title: "Решения сохранены — продолжить можно позже" }, "Выйти")));
}
async function rvDecide(it, how) {
  try {
    const r = await api("/api/share/decide", { id: S.rv.id, block: it.block, how });
    if (r.conflict) return rvMerge(it);
    it.decision = how === "reject" ? "rejected" : "accepted";
    toast(how === "reject" ? "Отклонено" : "Принято — PDF обновляется");
    refreshSoon();
    const nxt = S.rv.items.findIndex((x) => !x.decision);
    if (nxt >= 0) rvSelect(nxt); else renderSide();
  } catch (e) { fail(e); }
}
async function rvMerge(it) {
  const formula = !!(it.render && it.render.formula);
  let tokens = null;
  const template = it.merged || it.ours || it.theirs || "";
  if (!formula && !it.gen) {
    try { tokens = (await api(`/api/review/tokens?src=${encodeURIComponent(template)}`)).tokens; } catch (e) { return fail(e); }
  }
  dialog("Совместить: моё и рецензента", (body, foot, close) => {
    body.append(h("div", { class: "hint" }, it.status === "conflict" ? "Это место изменили и вы, и рецензент. Соберите итоговый вариант." : "Возьмите из правки рецензента нужное и допишите своё."),
      h("div", { class: "rv-card" }, rvDiffView(it.render)));
    let get;
    if (formula) {
      const m = /\$\$([\s\S]*?)\$\$/.exec(it.theirs || it.ours || "");
      const ta = h("textarea", { class: "inp", rows: 4, style: "height:auto" });
      ta.value = m ? m[1].trim() : "";
      const prev = h("div", { class: "fig-prev", html: mathHTML(ta.value) });
      ta.addEventListener("input", () => { prev.innerHTML = mathHTML(ta.value); });
      body.append(h("div", { class: "field" }, h("label", {}, "Итоговая формула (LaTeX)"), ta), prev);
      get = () => ({ src: (it.ours || it.theirs).replace(/\$\$[\s\S]*?\$\$/, `$$$$ ${ta.value.trim()} $$$$`) });
    } else if (it.gen) {
      const ta = h("textarea", { class: "inp", rows: 4, style: "height:auto" });
      ta.value = it.merged || it.theirs || "";
      body.append(h("div", { class: "field" }, h("label", {}, "Итоговый текст"), ta));
      get = () => ({ src: ta.value.trim() });
    } else {
      const ed = new ChipEditor(tokens, { onChip: chipClick });
      body.append(h("div", { class: "field" }, h("label", {}, "Итоговый текст (вставки — как в редакторе)"), ed.el), insertToolbar(ed));
      get = () => ({ template, tokens: ed.tokens() });
    }
    foot.append(h("button", { class: "btn", onclick: close }, "Отмена"), h("button", { class: "btn primary", id: "rv-own-save", onclick: async () => {
      try { await api("/api/review/own", { id: S.rv.id, block: it.block, ...get() }); it.decision = "partial"; close(); toast("Записан совмещённый вариант"); refreshSoon();
        const nxt = S.rv.items.findIndex((x) => !x.decision); if (nxt >= 0) rvSelect(nxt); else renderSide(); }
      catch (e) { fail(e); }
    } }, "Записать"));
  });
}
async function rvFinish() {
  try {
    const r = await api("/api/share/finish", { id: S.rv.id });
    toast(`Разбор завершён. Версия «${r.version}» сохранена.`, { ms: 7000 });
    rvExit(true);
  } catch (e) { fail(e); }
}
async function rvExit(finished) {
  if (!finished) { try { await api("/api/review/stop", {}); } catch (e) { /* */ } }
  S.rv = null;
  document.body.classList.remove("reviewing");
  $("#pages-r").hidden = true;
  $("#pages-r").replaceChildren();
  S.tab = "outline";
  $$("#tabs button").forEach((x) => x.classList.toggle("on", x.dataset.tab === "outline"));
  S.hl = null;
  renderSide();
  renderPages(S.pages);
}


/* ------------------------------------------------- обращения агентов ---- */
async function feedbackDialog() {
  let rows;
  try { rows = await api("/api/feedback"); } catch (e) { return fail(e); }
  const ST = { open: ["warn", "открыто"], forwarded: ["", "передано"], fixed: ["ok", "исправлено"], duplicate: ["", "дубль"], wontfix: ["", "не будет"] };
  dialog("Обращения агентов", (body, foot, close) => {
    body.append(h("div", { class: "hint" }, "Агенты пишут сюда, когда LDE ошибается, нет нужного инструмента или приходится делать рутину вручную (lde feedback). Это материал для доработки LDE."));
    if (!rows.length) body.append(h("div", { class: "muted" }, "Обращений нет."));
    rows.forEach((r) => {
      const det = h("details", { class: "fb" }, h("summary", {}, h("b", {}, r.title), " ",
        h("span", { class: "badge " + (ST[r.status] || [""])[0] }, (ST[r.status] || ["", r.status])[1]),
        h("span", { class: "muted small" }, ` ${r.kind_ru} · ${r.severity} · ${r.date}` + (r.repeats > 1 ? ` · повторов: ${r.repeats}` : ""))));
      det.addEventListener("toggle", async () => {
        if (!det.open || det.dataset.loaded) return;
        det.dataset.loaded = "1";
        const d = await api(`/api/feedback?id=${encodeURIComponent(r.id)}`);
        const sel = h("select", { class: "inp", style: "max-width:220px" }, Object.entries(ST).map(([k, v]) => h("option", { value: k, selected: k === r.status || undefined }, v[1])));
        sel.onchange = async () => { await api("/api/feedback/status", { id: r.id, status: sel.value }); toast("Статус изменён"); };
        det.append(h("pre", { class: "fb-text" }, d.text), h("div", { class: "row" }, h("span", { class: "muted small" }, "Статус:"), sel));
      });
      body.append(det);
    });
    foot.append(h("button", { class: "btn", onclick: close }, "Закрыть"));
  });
}
