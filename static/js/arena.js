const token = localStorage.getItem("auth_token");
const nickname = localStorage.getItem("nickname");
if (!token) window.location.href = "/";

const params = new URLSearchParams(window.location.search);
let matchId = params.get("match") || "";
let ws = null;
let allCards = {};
let cardImages = {};
let currentDraft = null;
let selecting = false;

const COST_BUCKETS = ["0", "1", "2", "3", "4", "5", "6", "7+"];

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

async function api(path, opts = {}) {
  opts.headers = {
    ...(opts.headers || {}),
    "X-Auth-Token": token,
    "Content-Type": "application/json",
  };
  const r = await fetch(path, opts);
  if (r.status === 401) {
    localStorage.clear();
    window.location.href = "/";
    return null;
  }
  return r;
}

async function loadAssets() {
  const cardsResp = await fetch("/api/cards");
  const cards = await cardsResp.json();
  allCards = Object.fromEntries(cards.map(c => [c.id, c]));
  try {
    const imgResp = await fetch("/api/card-images");
    const imgData = await imgResp.json();
    cardImages = imgData.cards || {};
  } catch (e) {
    cardImages = {};
  }
}

function cardCost(card) {
  const n = parseInt(card?.cost ?? 0, 10);
  return Number.isFinite(n) ? Math.max(0, n) : 0;
}

function renderChoiceCard(cardId, index) {
  const card = allCards[cardId] || { id: cardId, name: cardId, cost: 0, type: "MINION", text: "" };
  const el = document.createElement("button");
  el.type = "button";
  el.className = "arena-choice-card" + (cardImages[cardId] ? " full-art" : "");
  el.setAttribute("aria-label", `Escolher ${card.name || cardId}`);
  el.title = `${card.name || cardId}\n${card.text || ""}`;
  if (cardImages[cardId]) {
    el.style.backgroundImage = `url('${cardImages[cardId]}')`;
  } else {
    el.innerHTML = `
      <div class="fallback">
        <div class="cost">${cardCost(card)}</div>
        <div class="name">${escapeHtml(card.name || cardId)}</div>
        <div class="text">${escapeHtml(card.text || "")}</div>
        ${card.type === "MINION" ? `<div class="stats"><span>${card.attack ?? 0}</span><span>${card.health ?? 0}</span></div>` : ""}
      </div>
    `;
  }
  el.onclick = () => pickArenaCard(cardId, el);
  return el;
}

function renderCurve(curve) {
  const root = document.getElementById("arena-curve");
  if (!root) return;
  const max = Math.max(1, ...COST_BUCKETS.map(k => curve?.[k] || 0));
  root.innerHTML = "";
  for (const bucket of COST_BUCKETS) {
    const value = curve?.[bucket] || 0;
    const wrap = document.createElement("div");
    wrap.className = "arena-bar-wrap";
    const bar = document.createElement("div");
    bar.className = "arena-bar";
    bar.style.height = `${Math.max(2, Math.round((value / max) * 160))}px`;
    bar.title = `${bucket}: ${value} cartas`;
    const label = document.createElement("div");
    label.className = "arena-cost-label";
    label.textContent = bucket;
    const count = document.createElement("div");
    count.className = "faint";
    count.textContent = String(value);
    wrap.appendChild(bar);
    wrap.appendChild(label);
    wrap.appendChild(count);
    root.appendChild(wrap);
  }
}

function renderSelected(selected, copiesPerChoice) {
  const root = document.getElementById("arena-selected-list");
  if (!root) return;
  root.innerHTML = "";
  if (!selected?.length) {
    root.innerHTML = '<div class="faint" style="padding:.75rem 0;">Nenhuma carta escolhida ainda.</div>';
    return;
  }
  for (const item of selected) {
    const card = allCards[item.card_id] || {};
    const row = document.createElement("div");
    row.className = "arena-selected-row";
    row.innerHTML = `
      <div class="mana">${cardCost(card)}</div>
      <div>${escapeHtml(card.name || item.card_id)}</div>
      <div class="qty">${copiesPerChoice > 1 ? `x${copiesPerChoice}` : "✓"}</div>
    `;
    root.appendChild(row);
  }
}

function renderDraft(draft) {
  currentDraft = draft;
  selecting = false;

  document.getElementById("arena-status").textContent = "";
  document.getElementById("arena-draft").hidden = false;
  document.getElementById("arena-setup").hidden = true;
  document.getElementById("arena-waiting").hidden = true;
  document.getElementById("arena-room-code").textContent = params.get("code") || localStorage.getItem("current_code") || "";

  const made = draft.choices_made || 0;
  const total = draft.total || 30;
  const copies = draft.copies_per_choice || 1;
  const deckCount = made * copies;
  document.getElementById("arena-progress").textContent = `Escolha ${Math.min(made + 1, total)}/${total}`;
  document.getElementById("arena-progress-detail").textContent = `${deckCount}/30 cartas no deck · cada escolha adiciona ${copies} carta${copies > 1 ? "s" : ""}`;

  const cardsRoot = document.getElementById("arena-options");
  cardsRoot.innerHTML = "";

  if (draft.done) {
    cardsRoot.innerHTML = `
      <div class="panel" style="text-align:center; max-width:620px; margin:0 auto;">
        <h2>Deck finalizado</h2>
        <div class="muted">Aguarde o oponente terminar. A partida iniciará automaticamente.</div>
      </div>
    `;
  } else {
    (draft.options || []).forEach((opt, idx) => cardsRoot.appendChild(renderChoiceCard(opt.card_id, idx)));
  }

  renderCurve(draft.cost_curve || {});
  renderSelected(draft.selected || [], copies);
}

function connectArena() {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${window.location.host}/ws/match/${encodeURIComponent(matchId)}?token=${encodeURIComponent(token)}`);
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "joined") {
      if (msg.code) {
        localStorage.setItem("current_code", msg.code);
        const codeEl = document.getElementById("arena-room-code");
        if (codeEl) codeEl.textContent = msg.code;
        const waitingCodeEl = document.getElementById("arena-created-code");
        if (waitingCodeEl) waitingCodeEl.textContent = msg.code;
      }
      if (msg.mode && msg.mode !== "arena") {
        window.location.href = `/play?match=${encodeURIComponent(matchId)}`;
      }
    } else if (msg.type === "arena_draft") {
      renderDraft(msg.draft || {});
    } else if (msg.type === "state") {
      window.location.href = `/play?match=${encodeURIComponent(matchId)}`;
    } else if (msg.type === "error") {
      toast(msg.message || msg.msg || "Erro");
      selecting = false;
      document.querySelectorAll(".arena-choice-card").forEach(b => b.disabled = false);
    } else if (msg.type === "opponent_disconnected") {
      toast("Oponente desconectou");
    }
  };
  ws.onclose = () => toast("Conexão encerrada");
}

function pickArenaCard(cardId, el) {
  if (selecting || !ws || ws.readyState !== WebSocket.OPEN) return;
  selecting = true;
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
  if (data.mode !== "arena") {
    window.location.href = `/play?match=${encodeURIComponent(data.match_id)}`;
    return;
  }
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
  document.getElementById("arena-back-btn").onclick = () => window.location.href = "/lobby";
  document.getElementById("arena-logout-btn").onclick = () => { localStorage.clear(); window.location.href = "/"; };
  if (matchId) {
    document.getElementById("arena-setup").hidden = true;
    document.getElementById("arena-waiting").hidden = false;
    connectArena();
  }
}

initArena().catch(err => {
  console.error(err);
  toast("Falha ao carregar Arena");
});
