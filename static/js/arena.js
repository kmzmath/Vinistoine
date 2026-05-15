const token = localStorage.getItem("auth_token");
const nickname = localStorage.getItem("nickname");
if (!token) window.location.href = "/";

const qs = new URLSearchParams(window.location.search);
let matchId = qs.get("match") || "";
let ws = null;
let cards = {};
let images = {};
let picking = false;
let preview = null;
let goingToGame = false;
const BUCKETS = ["0", "1", "2", "3", "4", "5", "6", "7+"];

function esc(v) {
  return String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}
function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}
async function api(path, opts = {}) {
  opts.headers = { ...(opts.headers || {}), "X-Auth-Token": token, "Content-Type": "application/json" };
  const r = await fetch(path, opts);
  if (r.status === 401) { localStorage.clear(); window.location.href = "/"; return null; }
  return r;
}
function cost(card) {
  const n = parseInt(card?.cost ?? 0, 10);
  return Number.isFinite(n) ? Math.max(0, n) : 0;
}
async function loadAssets() {
  const c = await (await fetch("/api/cards")).json();
  cards = Object.fromEntries(c.map(x => [x.id, x]));
  try { images = (await (await fetch("/api/card-images")).json()).cards || {}; } catch { images = {}; }
}

function choiceCard(cardId) {
  const c = cards[cardId] || { id: cardId, name: cardId, cost: 0, type: "MINION", text: "" };
  const el = document.createElement("button");
  el.type = "button";
  el.className = "arena-choice-card" + (images[cardId] ? " full-art" : "");
  el.title = `${c.name || cardId}\n${c.text || ""}`;
  if (images[cardId]) el.style.backgroundImage = `url('${images[cardId]}')`;
  else el.innerHTML = `<div class="fallback"><div class="cost">${cost(c)}</div><div class="name">${esc(c.name || cardId)}</div><div class="text">${esc(c.text || "")}</div>${c.type === "MINION" ? `<div class="stats"><span>${c.attack ?? 0}</span><span>${c.health ?? 0}</span></div>` : ""}</div>`;
  el.onclick = () => pick(cardId, el);
  return el;
}

function renderCurve(curve) {
  const root = document.getElementById("arena-curve");
  if (!root) return;
  const max = Math.max(1, ...BUCKETS.map(k => curve?.[k] || 0));
  root.innerHTML = "";
  for (const b of BUCKETS) {
    const v = curve?.[b] || 0;
    const w = document.createElement("div");
    w.className = "arena-bar-wrap";
    w.innerHTML = `<div class="arena-bar" style="height:${Math.max(2, Math.round((v / max) * 160))}px" title="${b}: ${v} cartas"></div><div class="arena-cost-label">${b}</div><div class="faint">${v}</div>`;
    root.appendChild(w);
  }
}

function removePreview() {
  if (preview) preview.remove();
  preview = null;
}
function movePreview(ev) {
  if (!preview || !ev) return;
  const W = 260, H = 377, m = 18;
  let x = ev.clientX + m, y = ev.clientY + m;
  if (x + W > innerWidth - 8) x = ev.clientX - W - m;
  if (y + H > innerHeight - 8) y = innerHeight - H - 8;
  preview.style.left = `${Math.max(8, x)}px`;
  preview.style.top = `${Math.max(8, y)}px`;
}
function showPreview(cardId, ev) {
  removePreview();
  const c = cards[cardId] || {};
  preview = document.createElement("div");
  preview.className = "arena-card-preview";
  if (images[cardId]) preview.style.backgroundImage = `url('${images[cardId]}')`;
  else preview.innerHTML = `<div class="fallback-preview"><div class="preview-cost">${cost(c)}</div><div class="preview-name">${esc(c.name || cardId)}</div><div class="preview-text">${esc(c.text || "")}</div></div>`;
  document.body.appendChild(preview);
  movePreview(ev);
}
function rowArt(row, card) {
  const url = images[card.id];
  if (!url) return;
  const y = card.type === "SPELL" ? "24%" : "22%";
  const size = card.type === "SPELL" ? "128% auto" : "136% auto";
  row.classList.add("has-art");
  row.style.setProperty("--deck-row-art", `url('${url}')`);
  row.style.setProperty("--deck-row-art-x", "50%");
  row.style.setProperty("--deck-row-art-y", y);
  row.style.setProperty("--deck-row-art-size", size);
}
function renderDeck(selected, copies) {
  const root = document.getElementById("arena-selected-list");
  if (!root) return;
  root.innerHTML = "";
  if (!selected?.length) { root.innerHTML = '<div class="faint" style="padding:.75rem 0;">Nenhuma carta escolhida ainda.</div>'; return; }
  const counts = new Map();
  selected.forEach(x => counts.set(x.card_id, (counts.get(x.card_id) || 0) + copies));
  const rows = [...counts.entries()].map(([id, qty]) => ({ id, qty, card: cards[id] || { id, name: id, cost: 0 } }));
  rows.sort((a, b) => cost(a.card) - cost(b.card) || String(a.card.name || a.id).localeCompare(String(b.card.name || b.id)));
  for (const r of rows) {
    const row = document.createElement("div");
    row.className = "arena-deck-row";
    rowArt(row, r.card);
    row.innerHTML = `<div class="mana">${cost(r.card)}</div><div class="nm">${esc(r.card.name || r.id)}</div><div class="qty">${r.qty > 1 ? `×${r.qty}` : ""}</div>`;
    row.addEventListener("mouseenter", e => showPreview(r.id, e));
    row.addEventListener("mousemove", movePreview);
    row.addEventListener("mouseleave", removePreview);
    root.appendChild(row);
  }
}

function renderDraft(draft) {
  if (goingToGame) return;
  picking = false;
  document.getElementById("arena-draft").hidden = false;
  document.getElementById("arena-setup").hidden = true;
  document.getElementById("arena-waiting").hidden = true;
  document.getElementById("arena-status").textContent = "";
  document.getElementById("arena-room-code").textContent = qs.get("code") || localStorage.getItem("current_code") || "";
  const made = draft.choices_made || 0, total = draft.total || 30, copies = draft.copies_per_choice || 1;
  document.getElementById("arena-progress").textContent = `Escolha ${Math.min(made + 1, total)}/${total}`;
  document.getElementById("arena-progress-detail").textContent = `${made * copies}/30 cartas no deck · cada escolha adiciona ${copies} carta${copies > 1 ? "s" : ""}`;
  const options = document.getElementById("arena-options");
  options.innerHTML = "";
  if (draft.done) options.innerHTML = '<div class="panel" style="text-align:center; max-width:620px; margin:0 auto;"><h2>Deck finalizado</h2><div class="muted">Aguarde o oponente terminar. A partida iniciará automaticamente.</div></div>';
  else (draft.options || []).forEach(opt => options.appendChild(choiceCard(opt.card_id)));
  renderCurve(draft.cost_curve || {});
  renderDeck(draft.selected || [], copies);
}

function goToGame() {
  if (goingToGame || !matchId) return;
  goingToGame = true;
  removePreview();
  const setup = document.getElementById("arena-setup");
  const draft = document.getElementById("arena-draft");
  const waiting = document.getElementById("arena-waiting");
  if (setup) setup.hidden = true;
  if (draft) draft.hidden = true;
  if (waiting) {
    waiting.hidden = false;
    waiting.innerHTML = '<h2>Iniciando partida</h2><p class="muted">Decks finalizados. Carregando a mesa...</p><div class="pulse-dots" aria-hidden="true"><span></span><span></span><span></span></div>';
  }
  window.location.replace(`/play?match=${encodeURIComponent(matchId)}&arena_started=1&v=${Date.now()}`);
}

function connectArena() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/match/${encodeURIComponent(matchId)}?token=${encodeURIComponent(token)}`);
  ws.onmessage = ev => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "joined") {
      if (msg.code) {
        localStorage.setItem("current_code", msg.code);
        const a = document.getElementById("arena-room-code");
        const b = document.getElementById("arena-created-code");
        if (a) a.textContent = msg.code;
        if (b) b.textContent = msg.code;
      }
      if (msg.mode && msg.mode !== "arena") goToGame();
    } else if (msg.type === "arena_draft") renderDraft(msg.draft || {});
    else if (msg.type === "state") goToGame();
    else if (msg.type === "error") {
      toast(msg.message || msg.msg || "Erro");
      picking = false;
      document.querySelectorAll(".arena-choice-card").forEach(b => b.disabled = false);
    } else if (msg.type === "opponent_disconnected") toast("Oponente desconectou");
  };
  ws.onclose = () => { if (!goingToGame) toast("Conexão encerrada"); };
}
function pick(cardId, el) {
  if (picking || !ws || ws.readyState !== WebSocket.OPEN) return;
  picking = true;
  document.querySelectorAll(".arena-choice-card").forEach(b => b.disabled = true);
  if (el) el.classList.add("selected");
  ws.send(JSON.stringify({ action: "arena_pick", card_id: cardId }));
}
async function createArenaRoom() {
  const r = await api("/api/match/create", { method: "POST", body: JSON.stringify({ mode: "arena" }) });
  if (!r) return;
  const data = await r.json();
  if (!r.ok) { toast(data.detail || "Erro ao criar Arena"); return; }
  matchId = data.match_id;
  localStorage.setItem("current_match", data.match_id);
  localStorage.setItem("current_code", data.code);
  history.replaceState({}, "", `/arena?match=${encodeURIComponent(data.match_id)}&code=${encodeURIComponent(data.code)}`);
  document.getElementById("arena-setup").hidden = true;
  document.getElementById("arena-waiting").hidden = false;
  document.getElementById("arena-created-code").textContent = data.code;
  connectArena();
}
async function joinArenaRoom() {
  const code = document.getElementById("arena-join-code").value.trim().toUpperCase();
  if (!code) return;
  const r = await api("/api/match/join", { method: "POST", body: JSON.stringify({ code }) });
  if (!r) return;
  const data = await r.json();
  if (!r.ok) { toast(data.detail || "Erro ao entrar"); return; }
  if (data.mode !== "arena") { location.href = `/play?match=${encodeURIComponent(data.match_id)}`; return; }
  matchId = data.match_id;
  localStorage.setItem("current_match", data.match_id);
  localStorage.setItem("current_code", data.code);
  history.replaceState({}, "", `/arena?match=${encodeURIComponent(data.match_id)}&code=${encodeURIComponent(data.code)}`);
  document.getElementById("arena-setup").hidden = true;
  document.getElementById("arena-waiting").hidden = true;
  connectArena();
}
async function initArena() {
  await loadAssets();
  document.getElementById("arena-user").textContent = nickname || "?";
  document.getElementById("arena-create-btn").onclick = createArenaRoom;
  document.getElementById("arena-join-btn").onclick = joinArenaRoom;
  document.getElementById("arena-back-btn").onclick = () => location.href = "/lobby";
  document.getElementById("arena-logout-btn").onclick = () => { localStorage.clear(); location.href = "/"; };
  if (matchId) {
    document.getElementById("arena-setup").hidden = true;
    document.getElementById("arena-waiting").hidden = false;
    connectArena();
  }
}
window.addEventListener("scroll", removePreview, { passive: true });
window.addEventListener("blur", removePreview);
initArena().catch(err => { console.error(err); toast("Falha ao carregar Arena"); });
