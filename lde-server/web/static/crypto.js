/* Сквозное шифрование LDE в браузере — тот же формат, что lde/crypto.py (см. описание там).
   Только WebCrypto: AES-256-GCM, X25519, HKDF-SHA256, PBKDF2-SHA256. */
"use strict";

const LDE = (() => {
  const enc = new TextEncoder(), dec = new TextDecoder();
  const C = crypto.subtle;
  const cat = (...a) => { const n = a.reduce((s, x) => s + x.length, 0), out = new Uint8Array(n); let o = 0; for (const x of a) { out.set(x, o); o += x.length; } return out; };
  const b64e = (b) => { let s = ""; const u = new Uint8Array(b); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000)); return btoa(s); };
  const b64d = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const b64url = (b) => b64e(b).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const b64urlD = (s) => b64d(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));

  async function gzip(data, how) {
    const s = new Blob([data]).stream().pipeThrough(how === "c" ? new CompressionStream("gzip") : new DecompressionStream("gzip"));
    return new Uint8Array(await new Response(s).arrayBuffer());
  }
  const aes = (raw, use) => C.importKey("raw", raw, "AES-GCM", false, use);

  async function seal(key, data, purpose, compress = true) {
    const body = compress ? await gzip(data, "c") : data;
    const head = cat(enc.encode("LDE1"), new Uint8Array([compress ? 1 : 0]));
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const ct = new Uint8Array(await C.encrypt({ name: "AES-GCM", iv: nonce, additionalData: cat(head, enc.encode(purpose)) }, await aes(key, ["encrypt"]), body));
    return cat(head, nonce, ct);
  }
  async function open(key, blob, purpose) {
    blob = new Uint8Array(blob);
    if (blob.length < 33 || dec.decode(blob.subarray(0, 4)) !== "LDE1") throw new Error("не пакет LDE1");
    const head = blob.subarray(0, 5), nonce = blob.subarray(5, 17), ct = blob.subarray(17);
    let body;
    try { body = new Uint8Array(await C.decrypt({ name: "AES-GCM", iv: nonce, additionalData: cat(head, enc.encode(purpose)) }, await aes(key, ["decrypt"]), ct)); }
    catch (e) { throw new Error("не удалось расшифровать: неверная ссылка (часть после #) или повреждённые данные"); }
    return head[4] & 1 ? gzip(body, "d") : body;
  }
  async function hkdf(secret, info, salt = new Uint8Array(0), n = 32) {
    const k = await C.importKey("raw", secret, "HKDF", false, ["deriveBits"]);
    return new Uint8Array(await C.deriveBits({ name: "HKDF", hash: "SHA-256", salt, info: enc.encode(info) }, k, n * 8));
  }
  async function genKeypair() {
    const kp = await C.generateKey({ name: "X25519" }, true, ["deriveBits"]);
    return { priv: new Uint8Array(await C.exportKey("pkcs8", kp.privateKey)), pub: new Uint8Array(await C.exportKey("raw", kp.publicKey)) };
  }
  async function pubOf(privPkcs8) {
    const k = await C.importKey("pkcs8", privPkcs8, { name: "X25519" }, true, ["deriveBits"]);
    const jwk = await C.exportKey("jwk", k);
    return b64urlD(jwk.x);
  }
  async function wrapKey(recipientPub, key, purpose) {
    const eph = await C.generateKey({ name: "X25519" }, true, ["deriveBits"]);
    const ephPub = new Uint8Array(await C.exportKey("raw", eph.publicKey));
    const rp = await C.importKey("raw", recipientPub, { name: "X25519" }, false, []);
    const shared = new Uint8Array(await C.deriveBits({ name: "X25519", public: rp }, eph.privateKey, 256));
    const k = await hkdf(shared, "lde-wrap", cat(ephPub, recipientPub));
    const nonce = crypto.getRandomValues(new Uint8Array(12));
    const ct = new Uint8Array(await C.encrypt({ name: "AES-GCM", iv: nonce, additionalData: enc.encode("wrap:" + purpose) }, await aes(k, ["encrypt"]), key));
    return cat(enc.encode("LDK1"), ephPub, nonce, ct);
  }
  async function unwrapKey(myPrivPkcs8, blob, purpose) {
    blob = new Uint8Array(blob);
    if (dec.decode(blob.subarray(0, 4)) !== "LDK1") throw new Error("не обёртка ключа LDK1");
    const me = await C.importKey("pkcs8", myPrivPkcs8, { name: "X25519" }, false, ["deriveBits"]);
    const myPub = await pubOf(myPrivPkcs8);
    const ephPub = blob.subarray(4, 36), nonce = blob.subarray(36, 48), ct = blob.subarray(48);
    const ep = await C.importKey("raw", ephPub, { name: "X25519" }, false, []);
    const shared = new Uint8Array(await C.deriveBits({ name: "X25519", public: ep }, me, 256));
    const k = await hkdf(shared, "lde-wrap", cat(ephPub, myPub));
    return new Uint8Array(await C.decrypt({ name: "AES-GCM", iv: nonce, additionalData: enc.encode("wrap:" + purpose) }, await aes(k, ["decrypt"]), ct));
  }
  async function passwordKeys(password, kdfSaltB64, iterations = 600000) {
    const base = await C.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
    const master = new Uint8Array(await C.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: b64d(kdfSaltB64), iterations }, base, 256));
    return { auth: await hkdf(master, "lde-auth"), enc: await hkdf(master, "lde-enc") };
  }
  const sealJSON = async (key, obj, purpose) => seal(key, enc.encode(JSON.stringify(obj)), purpose);
  const openJSON = async (key, blob, purpose) => JSON.parse(dec.decode(await open(key, blob, purpose)));
  return { seal, open, sealJSON, openJSON, hkdf, genKeypair, pubOf, wrapKey, unwrapKey, passwordKeys, b64e, b64d, b64url, b64urlD, enc, dec };
})();
