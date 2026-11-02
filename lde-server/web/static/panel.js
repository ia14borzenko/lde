"use strict";
const $ = (s) => document.querySelector(s);
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
};
const P = { priv: null, me: null };
const when = (t) => (t ? new Date(t * 1000).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
function toast(m, err) { const t = h("div", { class: "toast" + (err ? " err" : "") }, m); $("#toasts").append(t); setTimeout(() => t.remove(), 5000); }
async function api(path, body, method) {
  const opt = { credentials: "same-origin", headers: { "X-LDE-Request": "1" } };
  if (body !== undefined) { opt.method = method || "POST"; opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  else if (method) opt.method = method;
  const r = await fetch(path, opt);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.error || r.status); e.status = r.status; throw e; }
  return d;
}
async function unlock(encKey, me) {
  P.priv = await LDE.open(encKey, LDE.b64d(me.enc_priv), "user-priv");
}
async function login(e) {
  e.preventDefault();
  $("#lmsg").textContent = ""; $("#lgo").disabled = true;
  try {
    const u = $("#lu").value.trim().toLowerCase();
    const pre = await api("/api/prelogin?username=" + encodeURIComponent(u));
    const k = await LDE.passwordKeys($("#lp").value, pre.kdf_salt, pre.iterations);
    const d = await api("/api/login", { username: u, auth: LDE.b64e(k.auth), web: true, device: "веб-панель" });
    await unlock(k.enc, d);
    try { sessionStorage.setItem("lde-enc", LDE.b64e(k.enc)); } catch (er) { /* */ }
    await show();
  } catch (er) { $("#lmsg").textContent = er.message; } finally { $("#lgo").disabled = false; }
}
async function show() {
  let me;
  try { me = await api("/api/me"); } catch (e) { $("#login").hidden = false; $("#main").hidden = true; return; }
  P.me = me;
  if (!P.priv) {
    const s = sessionStorage.getItem("lde-enc");
    if (!s) { $("#login").hidden = false; $("#main").hidden = true; return; }
    try { await unlock(LDE.b64d(s), me); } catch (e) { $("#login").hidden = false; return; }
  }
  $("#login").hidden = true; $("#main").hidden = false; $("#logout").hidden = false;
  $("#who").textContent = me.user.username + (me.user.is_admin ? " (администратор)" : "");
  $("#admin").hidden = !me.user.is_admin;
  $("#devices").replaceChildren(...me.devices.map((d) => h("div", { class: "list-row" }, h("span", { class: "t" }, d.name, h("span", { class: "badge" }, d.kind)), h("span", { class: "muted small" }, "последний раз: " + when(d.last_used)))));
  await Promise.all([loadShares(), loadProjects()]);
}
async function loadShares() {
  const box = $("#shares");
  const list = await api("/api/shares");
  box.replaceChildren();
  if (!list.length) { box.append("Ссылок пока нет."); return; }
  for (const s of list) {
    let meta = {};
    try { const key = await LDE.unwrapKey(P.priv, LDE.b64d(s.key_wrap), "share"); meta = await LDE.openJSON(key, LDE.b64d(s.meta_enc), "share-meta"); meta.key = key; }
    catch (e) { meta = { label: "(не расшифровать)" }; }
    const link = meta.token && meta.key ? `${location.origin}/s/${meta.token}#${LDE.b64url(meta.key)}` : null;
    const state = s.revoked ? h("span", { class: "badge err" }, "отозвана") : s.expires && s.expires < Date.now() / 1000 ? h("span", { class: "badge warn" }, "истекла") : h("span", { class: "badge ok" }, "действует");
    box.append(h("div", { class: "list-row" },
      h("div", { class: "t" }, h("b", {}, meta.label || "—"), state, s.pin ? h("span", { class: "badge" }, "PIN") : null, h("span", { class: "badge" }, s.perms === "edit" ? "правка" : "замечания"),
        h("div", { class: "muted small" }, `${meta.doc_title || ""} · версия «${meta.version_name || ""}» · создана ${when(s.created)} · до ${s.expires ? when(s.expires) : "бессрочно"}`),
        h("div", { class: "muted small" }, `открывали: ${s.opens} (последний раз ${when(s.opened)}) · копия рецензента: ${s.copy_ver ? "сохранена " + when(s.copy_updated) : "пока нет"}`)),
      link ? h("button", { class: "btn small", onclick: () => navigator.clipboard.writeText(link).then(() => toast("Ссылка скопирована")) }, "Копировать ссылку") : null,
      h("button", { class: "btn small", onclick: async () => { await api(`/api/shares/${s.id}`, { revoked: !s.revoked }); loadShares(); } }, s.revoked ? "Вернуть" : "Отозвать"),
      h("button", { class: "btn small danger", onclick: async () => { if (confirm("Удалить ссылку и копию рецензента с сервера?")) { await api(`/api/shares/${s.id}`, undefined, "DELETE"); loadShares(); } } }, "Удалить")));
  }
}
async function loadProjects() {
  const box = $("#projects");
  const list = await api("/api/projects");
  box.replaceChildren();
  if (!list.length) { box.append("Проектов пока нет."); return; }
  for (const p of list) {
    let meta = {}, key = null;
    try { key = await LDE.unwrapKey(P.priv, LDE.b64d(p.key_wrap), "project"); meta = await LDE.openJSON(key, LDE.b64d(p.meta_enc), "project-meta"); }
    catch (e) { meta = { title: "(не расшифровать)" }; }
    box.append(h("div", { class: "list-row" },
      h("div", { class: "t" }, h("b", {}, meta.title || p.id), h("span", { class: "badge" }, { owner: "владелец", editor: "соавтор", reviewer: "рецензент", reader: "читатель" }[p.role]),
        h("div", { class: "muted small" }, `владелец: ${p.owner} · обновлён ${when(p.updated)}`)),
      h("button", { class: "btn small", onclick: () => projectDialog(p, meta, key) }, "Открыть")));
  }
}
async function projectDialog(p, meta, key) {
  const d = await api(`/api/projects/${p.id}`);
  const views = await api(`/api/projects/${p.id}/views`);
  const wrap = $("#dlg-wrap"), dlg = $("#dlg");
  const body = h("div", { class: "dlg-body" }), foot = h("div", { class: "dlg-foot" });
  dlg.replaceChildren(h("div", { class: "dlg-head" }, meta.title || p.id), body, foot);
  body.append(h("h3", {}, "Версии"));
  if (!views.length) body.append(h("div", { class: "muted small" }, "Опубликованных версий нет."));
  for (const v of views.slice().reverse()) {
    body.append(h("div", { class: "list-row" }, h("span", { class: "t" }, `#${v.seq} · ${when(v.created)}`), h("button", { class: "btn small", onclick: async () => {
      const raw = await (await fetch(`/api/projects/${p.id}/blobs/${v.blob}`, { credentials: "same-origin" })).arrayBuffer();
      const view = await LDE.openJSON(key, raw, "project-view");
      const docs = view.documents || [];
      if (!docs.length) return toast("В этой версии нет PDF", true);
      const pdf = docs[0].pdf;
      window.open(URL.createObjectURL(new Blob([LDE.b64d(pdf)], { type: "application/pdf" })), "_blank");
    } }, "Открыть PDF")));
  }
  body.append(h("h3", {}, "Участники"));
  d.members.forEach((m) => body.append(h("div", { class: "list-row" }, h("span", { class: "t" }, m.username), h("span", { class: "badge" }, m.role),
    d.role === "owner" && m.username !== P.me.user.username ? h("button", { class: "btn small danger", onclick: async () => { await api(`/api/projects/${p.id}/members`, { username: m.username, role: "remove" }); wrap.hidden = true; toast("Удалён (новые версии ему недоступны после смены ключа в LDE)"); } }, "Убрать") : null)));
  if (d.role === "owner") {
    const u = h("input", { class: "inp", placeholder: "имя пользователя" });
    const role = h("select", { class: "inp" }, ...["reader", "reviewer", "editor"].map((r) => h("option", { value: r }, { reader: "читатель", reviewer: "рецензент", editor: "соавтор" }[r])));
    body.append(h("div", { class: "row" }, u, role, h("button", { class: "btn", onclick: async () => {
      try {
        const pk = await api(`/api/users/${encodeURIComponent(u.value.trim().toLowerCase())}/pubkey`);
        const wrapKey = await LDE.wrapKey(LDE.b64d(pk.pubkey), key, "project");
        await api(`/api/projects/${p.id}/members`, { username: pk.username, role: role.value, key_wrap: LDE.b64e(wrapKey) });
        toast("Доступ выдан"); wrap.hidden = true;
      } catch (e) { toast(e.message, true); }
    } }, "Добавить")));
  }
  foot.append(h("button", { class: "btn", onclick: () => (wrap.hidden = true) }, "Закрыть"));
  wrap.hidden = false;
}
$("#lf").addEventListener("submit", login);
$("#logout").onclick = async () => { await api("/api/logout", {}); sessionStorage.removeItem("lde-enc"); location.reload(); };
$("#inew").onclick = async () => {
  try {
    const d = await api("/api/invites", { note: $("#inote").value, admin: $("#iadm").checked });
    const link = `${location.origin}/join#${d.invite}`;
    $("#ilink").replaceChildren(h("p", {}, "Ссылка-приглашение (одноразовая, 7 дней): ", h("code", { class: "k" }, link), " ",
      h("button", { class: "btn small", onclick: () => navigator.clipboard.writeText(link).then(() => toast("Скопировано")) }, "Копировать")));
  } catch (e) { toast(e.message, true); }
};
show();
