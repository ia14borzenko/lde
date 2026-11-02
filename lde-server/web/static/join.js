"use strict";
(async () => {
  const inv = location.hash.slice(1);
  history.replaceState(null, "", location.pathname);
  const msg = document.getElementById("msg"), f = document.getElementById("f");
  if (!inv) { msg.textContent = "В ссылке нет приглашения."; return; }
  const r = await fetch("/api/invite?invite=" + encodeURIComponent(inv));
  const d = await r.json();
  if (!r.ok) { msg.textContent = d.error; msg.className = "err"; return; }
  msg.textContent = d.is_admin ? "Приглашение администратора." : "Приглашение действительно.";
  f.hidden = false;
  f.onsubmit = async (e) => {
    e.preventDefault();
    const u = document.getElementById("u").value.trim().toLowerCase(), p = document.getElementById("p").value;
    if (p !== document.getElementById("p2").value) { msg.textContent = "Пароли не совпадают"; msg.className = "err"; return; }
    document.getElementById("go").disabled = true;
    msg.textContent = "Создаю ключи…"; msg.className = "muted";
    try {
      const salt = LDE.b64e(crypto.getRandomValues(new Uint8Array(16)));
      const k = await LDE.passwordKeys(p, salt);
      const kp = await LDE.genKeypair();
      const encPriv = await LDE.seal(k.enc, kp.priv, "user-priv", false);
      const rr = await fetch("/api/register", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invite: inv, username: u, kdf_salt: salt, auth: LDE.b64e(k.auth), pubkey: LDE.b64e(kp.pub), enc_priv: LDE.b64e(encPriv) }) });
      const dd = await rr.json();
      if (!rr.ok) throw new Error(dd.error);
      f.hidden = true;
      msg.innerHTML = "";
      msg.append("Готово. Теперь можно ", Object.assign(document.createElement("a"), { href: "/panel/", textContent: "войти в панель" }),
        " и подключить LDE: ", Object.assign(document.createElement("code"), { textContent: `lde server login ${location.origin} ${u}` }));
      msg.className = "";
    } catch (er) { msg.textContent = er.message; msg.className = "err"; document.getElementById("go").disabled = false; }
  };
})();
