/* «Сервер» Studio в браузере рецензента: тот же интерфейс LDE Studio, но вместо проекта — расшифрованный
   снимок документа, а правки и замечания уходят в зашифрованную копию рецензента на сервере LDE.
   Ключ — из части ссылки после # (на сервер не уходит). Вставки — непрозрачными номерами (@vN). */
"use strict";

window.LDE_BACKEND = (() => {
  const SID = location.pathname.split("/")[2];
  const BASE = `/r/${SID}/api`;
  const B = { key: null, snap: null, copy: null, ver: 0, owner: null, perms: "edit", dirty: false, saving: false, blobs: {}, saveState: "" };

  async function raw(path, opts = {}) {
    return fetch(BASE + path, { credentials: "same-origin", ...opts, headers: { "X-LDE-Request": "1", ...(opts.headers || {}) } });
  }
  function linkKey() {
    let k = location.hash.slice(1);
    try {
      if (k) sessionStorage.setItem("lde-key-" + SID, k);
      else k = sessionStorage.getItem("lde-key-" + SID) || sessionStorage.getItem("lde-key-pending") || "";
      if (k) { sessionStorage.setItem("lde-key-" + SID, k); sessionStorage.removeItem("lde-key-pending"); }
    } catch (e) { /* без хранилища */ }
    if (location.hash) history.replaceState(null, "", location.pathname);
    return k;
  }
  const blockById = (id) => B.snap.blocks.find((b) => b.id === id);
  const KIND_RU = { heading: "Заголовок", para: "Абзац", formula: "Формула", figure: "Рисунок", table: "Таблица", list: "Перечисление",
    code: "Листинг", title_field: "Реквизит титула", title_line: "Строка титула", abbr: "Сокращение", bib: "Источник", toc: "Содержание", symbol: "Обозначение" };

  /* ---------------------------------------------- вставки: снимок ↔ Studio ---- */
  function chipSrc(kind, id, text) {
    return { q: `{@${id}}`, val: `{=@${id}}`, ref: `@@${id}`, cite: `[@@${id}]`, math: `$@${id}$`, b: `**${text}**`, i: `*${text}*` }[kind] || `@@${id}`;
  }
  function toStudio(tokens) {
    return (tokens || []).map((t) => {
      if (t.t === "text") return { t: "text", v: t.v, src: t.v };
      if (t.src) {   // правка рецензента: вставка уже в виде Studio
        const m = /@(v\d+)/.exec(t.src), c = m ? B.snap.chips[m[1]] || catalogById(m[1]) : null;
        return { t: "chip", src: t.src, kind: c ? c.kind : /^\$/.test(t.src) ? "math" : "x", text: c ? c.text : t.src, latex: c ? c.latex : (/^\$(.*)\$$/.exec(t.src) || [])[1], title: c ? c.title : "" };
      }
      const c = B.snap.chips[t.id] || { kind: "x", text: "?" };
      return { t: "chip", src: chipSrc(c.kind, t.id, c.text), kind: c.kind, text: c.text, latex: c.latex, title: c.title || "" };
    });
  }
  const catalogById = (id) => (B.snap.catalog || []).find((x) => x.id === id);
  const key = (toks) => JSON.stringify((toks || []).map((t) => (t.t === "chip" ? "c:" + (t.src || t.id) : "t:" + t.v)).join("").replace(/\s+/g, " ").trim());

  /* ------------------------------------------------- страницы и места ---- */
  const MARGIN = 56;
  const mTop = (m) => (m.kind === "formula" ? m.y - 4 : m.y + 17);
  function floats() {
    const out = {};
    for (const m of B.snap.marks) if (m.float) (out[m.id] = out[m.id] || {})[m.part || "start"] = m;
    return out;
  }
  function rects(id) {
    const marks = B.snap.marks, pages = B.snap.pages;
    const ph = (n) => (pages[n - 1] ? pages[n - 1].h : 842);
    const ms = marks.filter((m) => m.id === id);
    if (!ms.length) return [];
    const fl = floats()[id];
    if (fl && fl.start && fl.end && fl.start.page === fl.end.page)
      return [{ page: fl.start.page, top: ph(fl.start.page) - fl.start.y, bottom: ph(fl.start.page) - fl.end.y + 4 }];
    const m = ms[0];
    const flow = marks.filter((x) => !x.float).sort((a, b) => a.page - b.page || mTop(b) - mTop(a));
    const i = flow.indexOf(m), nxt = i >= 0 ? flow[i + 1] : null;
    if (nxt && nxt.page === m.page) return [{ page: m.page, top: ph(m.page) - mTop(m), bottom: ph(m.page) - mTop(nxt) }];
    const r = [{ page: m.page, top: ph(m.page) - mTop(m), bottom: ph(m.page) - MARGIN }];
    if (nxt && nxt.page === m.page + 1 && ph(nxt.page) - mTop(nxt) > MARGIN + 20) r.push({ page: nxt.page, top: MARGIN, bottom: ph(nxt.page) - mTop(nxt) });
    return r;
  }
  function locate(page, y) {
    const pg = B.snap.pages[page - 1];
    if (!pg) return null;
    const yb = pg.h - y;
    for (const [id, d] of Object.entries(floats())) {
      const s = d.start, e = d.end;
      if (s && e && s.page === page && e.page === page && e.y - 6 <= yb && yb <= s.y + 6) return id;
    }
    let best = null;
    for (const m of B.snap.marks) {
      if (m.float) continue;
      if (m.page < page || (m.page === page && mTop(m) >= yb)) {
        if (!best || m.page > best.page || (m.page === best.page && mTop(m) < mTop(best))) best = m;
      }
    }
    return best ? best.id : null;
  }
  function groupOf(id) {
    const b = blockById(id);
    if (!b) return id;
    if (B.snap.marks.some((m) => m.id === id)) return id;
    if (b.kind === "title_line" || b.kind === "title_field") return "@title";
    if (b.kind === "abbr" || b.kind === "symbol") return "@abbr";
    if (b.kind === "bib") return "@bib";
    return id;
  }
  function groupBlocks(id) {
    const all = B.snap.blocks;
    if (id === "@title") return all.filter((b) => b.kind === "title_field" || (b.kind === "title_line" && b.editable));
    if (id === "@abbr") return all.filter((b) => b.kind === "abbr" || b.kind === "symbol");
    if (id === "@bib") return all.filter((b) => b.kind === "bib");
    return null;
  }
  const whereOf = (id) => (id && id.startsWith("@") ? { "@title": "титульный лист", "@abbr": "перечень сокращений", "@bib": "список источников" }[id] : (blockById(id) || {}).where || "");

  /* ---------------------------------------------------------- описание ---- */
  function describe(id) {
    const grp = groupBlocks(id);
    if (grp) {
      return { uid: id, kind: "group", kind_ru: whereOf(id), title: whereOf(id), rects: rects(id), editable: R_EDIT(),
        items: grp.map((b) => ({ uid: b.id, label: b.label || b.term || b.number || "", tokens: toStudio((B.copy.edits || {})[b.id] || b.tokens || [{ t: "text", v: b.text || "" }]),
          editable: !!b.editable && R_EDIT(), latex: b.kind === "symbol" ? b.latex : undefined, text: b.kind === "symbol" ? b.text : undefined })) };
    }
    const b = blockById(id);
    if (!b) throw new Error("элемент не найден");
    const d = { uid: id, kind: b.kind, kind_ru: KIND_RU[b.kind] || "Элемент", title: b.where || KIND_RU[b.kind], base: "", src: "", label: "",
      style_keys: [], style_on: false, rects: rects(groupOf(id)), editable: !!b.editable && R_EDIT() };
    const ed = (B.copy.edits || {})[id];
    if (["para", "heading", "figure", "table", "title_field", "abbr", "bib", "title_line"].includes(b.kind)) d.tokens = toStudio(Array.isArray(ed) ? ed : b.tokens || [{ t: "text", v: b.text || "" }]);
    if (["title_field", "abbr", "bib", "title_line"].includes(b.kind)) d.kind = "para";
    if (b.kind === "heading") { d.level = b.level; d.unnumbered = !!b.structural; }
    if (b.kind === "formula") { d.latex = ed && ed.latex !== undefined ? ed.latex : b.ml || b.latex; d.numbered = !!b.numbered; d.number = b.number; d.label = b.numbered ? "eq:x" : ""; }
    if (b.kind === "figure") { d.path = "@img:" + (b.img || ""); d.width = "0.9"; d.calc = ""; d.number = b.number; d.subfigs = []; }
    if (b.kind === "table" || b.kind === "list" || b.kind === "code" || b.kind === "toc" || b.kind === "symbol") d.readonly = true;
    return d;
  }
  const R_EDIT = () => B.perms === "edit";

  /* --------------------------------------------------------- состояние ---- */
  function marksForPages() {
    const edited = new Set(Object.keys(B.copy.edits || {}).map(groupOf));
    const commented = new Set((B.copy.comments || []).map((c) => c.block).filter(Boolean).map(groupOf));
    const out = [];
    for (const id of new Set([...edited, ...commented])) out.push({ uid: id, rects: rects(id), edit: edited.has(id), cm: commented.has(id) });
    return out;
  }
  function state() {
    const s = B.snap;
    const outline = s.blocks.filter((b) => b.kind === "heading").map((b) => ({ uid: b.id, level: b.level, number: b.structural ? "" : b.number,
      text: (b.tokens || []).map((t) => (t.t === "text" ? t.v : (s.chips[t.id] || {}).text || "")).join("") }));
    let theme = "light";
    try { theme = localStorage.getItem("lde-review-theme") || "light"; } catch (e) { /* */ }
    return {
      project: { title: s.title, name: s.title, doc: "main", documents: [], root: "", folder: "", profile: "" },
      outline, objects: [], calcs: [], quantities: [], sources: [], abbreviations: [],
      status: { stale: [], errors: 0, warnings: 0 }, review: 0, changes: 0, history: [],
      build: { state: "ok", duration: 0, errors: [], warnings: [] },
      pages: s.pages.map((p, i) => ({ n: i + 1, hash: "p" + (i + 1), w: p.w, h: p.h })),
      prefs: { theme }, view: {}, recent: [],
      reviewer: { name: B.copy.name || "", perms: B.perms, version: s.version_name, created: s.created, saveState: B.saveState,
        marks: marksForPages(), hasPdf: !!s.pdf, count: Object.keys(B.copy.edits || {}).length + (B.copy.comments || []).length },
    };
  }
  function mine() {
    const dec = (B.owner && B.owner.decisions) || {}, rep = (B.owner && B.owner.replies) || {}, st = (B.owner && B.owner.status) || {};
    const order = (id) => { const i = B.snap.blocks.findIndex((x) => x.id === id); return i < 0 ? 1e9 : i; };
    return {
      edits: Object.keys(B.copy.edits || {}).sort((a, b) => order(a) - order(b)).map((id) => {
        const b = blockById(id);
        const txt = b ? (b.tokens || []).map((t) => (t.t === "text" ? t.v : (B.snap.chips[t.id] || {}).text || "")).join("").replace(/\s+/g, " ").trim() : "";
        return { uid: id, where: whereOf(id), snippet: txt.slice(0, 50) + (txt.length > 50 ? "…" : ""), decision: dec[id] || null };
      }),
      comments: (B.copy.comments || []).map((c) => ({ ...c, where: c.block ? whereOf(c.block) : "общее", status: st[c.id] || null, replies: rep[c.id] || [] })),
    };
  }

  /* ---------------------------------------------------------- сохранение ---- */
  let saveT = null;
  function markDirty() {
    B.dirty = true;
    B.saveState = "не сохранено";
    clearTimeout(saveT);
    saveT = setTimeout(save, 900);
  }
  async function save() {
    if (B.saving) { saveT = setTimeout(save, 400); return; }
    B.saving = true;
    try {
      B.copy.updated = Date.now() / 1000;
      const blob = await LDE.sealJSON(B.key, B.copy, "share-copy");
      const r = await raw("/copy", { method: "PUT", body: blob, headers: { "Content-Type": "application/octet-stream", "If-Match": String(B.ver) } });
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || r.status);
      B.ver = d.version;
      B.dirty = false;
      B.saveState = "сохранено";
    } catch (e) {
      B.saveState = "не сохранено: " + e.message;
    } finally {
      B.saving = false;
      if (B.onChange) B.onChange();
    }
  }
  addEventListener("beforeunload", (e) => { if (B.dirty) { e.preventDefault(); e.returnValue = ""; } });

  function setEdit(id, value, orig) {
    B.copy.edits = B.copy.edits || {};
    if (value === null || key(value) === key(orig)) delete B.copy.edits[id];
    else B.copy.edits[id] = value;
    markDirty();
  }

  /* --------------------------------------------------------------- API ---- */
  async function api(path, body) {
    const u = new URL(path, location.origin);
    const p = u.pathname, q = Object.fromEntries(u.searchParams);
    if (body === undefined) {
      if (p === "/api/state") return state();
      if (p === "/api/element") return describe(q.uid);
      if (p === "/api/locate") { const id = locate(+q.page, +q.y); return id ? { uid: groupOf(id), rects: rects(groupOf(id)) } : {}; }
      if (p === "/api/search") {
        const ql = (q.q || "").toLowerCase();
        return (B.snap.catalog || []).filter((x) => (!q.kind || x.kind === q.kind) && (!ql || [x.text, x.title].some((f) => (f || "").toLowerCase().includes(ql))))
          .slice(0, 200).map((x) => ({ kind: x.kind, src: chipSrc(x.kind, x.id, x.text), latex: x.latex, text: x.text, title: x.title }));
      }
      if (p === "/api/issues") return [];
      if (p === "/api/mine") return mine();
      if (p === "/api/prefs") return state().prefs;
      throw new Error("при проверке документа это недоступно");
    }
    if (p === "/api/element/save") {
      const id = body.uid, b = blockById(id);
      if (!b || !b.editable || !R_EDIT()) throw new Error("этот элемент можно только прокомментировать");
      if (b.kind === "formula") {
        const m = /^\$\$([\s\S]*?)\$\$/.exec(body.src || "");
        const tex = m ? m[1].trim() : "";
        if ((b.ml || b.latex) === tex) setEdit(id, null);
        else B.copy.edits = { ...(B.copy.edits || {}), [id]: { latex: tex } }, markDirty();
      } else {
        const toks = (body.tokens || []).map((t) => (t.t === "chip" ? { t: "chip", src: t.src } : { t: "text", v: t.v }));
        const orig = toStudio(b.tokens || []).map((t) => (t.t === "chip" ? { t: "chip", src: t.src } : { t: "text", v: t.v }));
        setEdit(id, toks, orig);
      }
      return { changed: true, uid: id };
    }
    if (p === "/api/comment") {
      if (!(body.text || "").trim()) throw new Error("пустое замечание");
      const c = { id: "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 5), block: body.uid || null, quote: body.quote || "", text: body.text.trim(), time: Date.now() / 1000 };
      if (body.id) { const old = (B.copy.comments || []).find((x) => x.id === body.id); if (old) { old.text = c.text; markDirty(); return { ok: true }; } }
      (B.copy.comments = B.copy.comments || []).push(c);
      markDirty();
      return { ok: true, id: c.id };
    }
    if (p === "/api/comment/delete") { B.copy.comments = (B.copy.comments || []).filter((c) => c.id !== body.id); markDirty(); return { ok: true }; }
    if (p === "/api/edit/drop") { setEdit(body.uid, null); return { ok: true }; }
    if (p === "/api/name") { B.copy.name = (body.name || "").trim() || "Рецензент"; markDirty(); return { ok: true }; }
    if (p === "/api/prefs") { try { if (body.theme) localStorage.setItem("lde-review-theme", body.theme); } catch (e) { /* */ } return { ok: true }; }
    if (p === "/api/view") return { ok: true };
    throw new Error("при проверке документа это недоступно");
  }

  function pageSrc(n) {
    if (!B.blobs[n]) B.blobs[n] = URL.createObjectURL(new Blob([LDE.b64d(B.snap.pages[n - 1].img)], { type: "image/png" }));
    return B.blobs[n];
  }
  function fileSrc(path) {
    const im = B.snap.images[(path || "").replace(/^@img:/, "")];
    return im ? `data:${im.type};base64,${im.data}` : "";
  }
  function pdf() {
    if (!B.snap.pdf) return false;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([LDE.b64d(B.snap.pdf)], { type: "application/pdf" }));
    a.download = (B.snap.title || "document") + ".pdf";
    document.body.append(a); a.click(); a.remove();
    return true;
  }

  const ready = (async () => {
    const k = linkKey();
    if (!k) throw new Error("В ссылке нет ключа (части после #). Откройте ссылку от автора целиком.");
    B.key = LDE.b64urlD(k);
    const mr = await raw("/meta");
    const meta = await mr.json();
    if (!mr.ok) throw new Error(meta.error || "ссылка недействительна");
    B.perms = meta.perms;
    B.snap = await LDE.openJSON(B.key, await (await raw("/snapshot")).arrayBuffer(), "share-snapshot");
    if (!B.snap.pages || !B.snap.pages.length) throw new Error("Эта ссылка выдана старой версией LDE (без страниц документа). Попросите автора выдать ссылку заново.");
    const cr = await raw("/copy");
    if (cr.ok) { B.copy = await LDE.openJSON(B.key, await cr.arrayBuffer(), "share-copy"); B.ver = Number(cr.headers.get("X-LDE-Version") || meta.copy_ver); }
    else { B.copy = { v: 2, name: "", edits: {}, comments: [] }; B.ver = meta.copy_ver; }
    const orr = await raw("/owner");
    if (orr.ok) { try { B.owner = await LDE.openJSON(B.key, await orr.arrayBuffer(), "share-owner"); } catch (e) { B.owner = null; } }
    B.saveState = B.ver ? "сохранено" : "";
    document.title = `${B.snap.title} — проверка`;
  })();
  return { ready, api, pageSrc, fileSrc, pdf, B, set onChange(f) { B.onChange = f; } };
})();
