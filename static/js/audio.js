// =========================================================================
// Trilha sonora + painel de configuração global do Vinístone.
//
// Carrega faixas de /api/music (arquivos em /static/audio/), embaralha e
// toca em modo "drain queue" (sem repetir até todas tocarem). Volume e mute
// persistem em localStorage. Ao entrar/sair de uma partida (transição entre
// /play e o resto do site) força a próxima da fila tocando do início.
//
// Também monta um painel de configurações flutuante no canto inferior
// direito (sempre visível) com slider de volume, mute, "próxima faixa" e,
// quando dentro de uma partida, o botão de "Render-se".
//
// O botão de render-se dispara um evento `audio-surrender-request` no
// window e uma função window.requestSurrender() (se definida pela página).
// game.html escuta esse evento.
// =========================================================================
(function () {
  if (window.__vinistoneAudioInit) return;
  window.__vinistoneAudioInit = true;

  const KEYS = {
    volume: "music.volume",
    muted: "music.muted",
    queue: "music.queue",
    fingerprint: "music.fingerprint",
    track: "music.currentTrack",
    time: "music.currentTime",
    updated: "music.lastUpdate",
    context: "music.lastContext",
  };
  const DEFAULT_VOLUME = 0.5;
  // Se passar muito tempo sem tocar (ex: usuário deixou aba aberta dias),
  // descartamos o "resume" e começamos uma nova faixa.
  const MAX_RESUME_GAP_MS = 30 * 60 * 1000;

  // ---------- localStorage helpers ----------
  function ls(key, def) {
    try {
      const v = localStorage.getItem(key);
      return v === null ? def : v;
    } catch (e) {
      return def;
    }
  }
  function lsSet(key, value) {
    try {
      localStorage.setItem(key, String(value));
    } catch (e) {}
  }
  function lsJson(key, def) {
    try {
      const v = localStorage.getItem(key);
      if (!v) return def;
      return JSON.parse(v);
    } catch (e) {
      return def;
    }
  }
  function lsSetJson(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {}
  }

  function shuffle(arr) {
    const a = arr.slice();
    for (let i = a.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      const tmp = a[i];
      a[i] = a[j];
      a[j] = tmp;
    }
    return a;
  }

  function fingerprint(urls) {
    return urls.slice().sort().join("|");
  }

  function detectContext() {
    const path = window.location.pathname || "";
    return path === "/play" || path.startsWith("/play") ? "match" : "idle";
  }

  function getVolume() {
    const raw = parseFloat(ls(KEYS.volume, String(DEFAULT_VOLUME)));
    if (!isFinite(raw)) return DEFAULT_VOLUME;
    return Math.min(1, Math.max(0, raw));
  }
  function isMuted() {
    return ls(KEYS.muted, "0") === "1";
  }

  // ---------- estado global ----------
  let tracks = [];
  let queue = [];
  let audio = null;
  let currentTrack = null;
  let panelEl = null;
  let buttonEl = null;

  // ---------- discover ----------
  async function fetchTracks() {
    try {
      const r = await fetch("/api/music");
      if (!r.ok) return [];
      const data = await r.json();
      return Array.isArray(data.tracks) ? data.tracks : [];
    } catch (e) {
      return [];
    }
  }

  function ensureQueue() {
    const fp = fingerprint(tracks);
    const stored = ls(KEYS.fingerprint, "");
    if (stored !== fp) {
      queue = shuffle(tracks);
      lsSet(KEYS.fingerprint, fp);
      lsSetJson(KEYS.queue, queue);
      return;
    }
    const persisted = lsJson(KEYS.queue, null);
    if (Array.isArray(persisted) && persisted.length) {
      // Mantém só faixas que ainda existem (defesa contra remoção manual).
      queue = persisted.filter((u) => tracks.indexOf(u) !== -1);
    }
    if (!queue.length) {
      queue = shuffle(tracks);
      lsSetJson(KEYS.queue, queue);
    }
  }

  function popNext() {
    if (!queue.length) {
      queue = shuffle(tracks);
    }
    const next = queue.shift();
    lsSetJson(KEYS.queue, queue);
    return next || null;
  }

  // ---------- playback ----------
  function applyVolume() {
    if (!audio) return;
    const vol = getVolume();
    const muted = isMuted();
    audio.volume = muted ? 0 : vol;
    audio.muted = muted;
    syncPanelControls();
  }

  function persistPosition() {
    if (!audio || !currentTrack) return;
    lsSet(KEYS.time, String(audio.currentTime || 0));
    lsSet(KEYS.updated, String(Date.now()));
  }

  function play(url, startAt) {
    if (!url || !audio) return;
    currentTrack = url;
    lsSet(KEYS.track, url);
    lsSet(KEYS.time, String(startAt || 0));
    lsSet(KEYS.updated, String(Date.now()));
    audio.src = url;
    const startSafe = Math.max(0, startAt || 0);
    const startPlayback = () => {
      try {
        if (startSafe > 0 && isFinite(audio.duration) && startSafe < audio.duration) {
          audio.currentTime = startSafe;
        }
      } catch (e) {}
      const p = audio.play();
      if (p && typeof p.then === "function") p.catch(armGestureFallback);
    };
    if (audio.readyState >= 1) {
      startPlayback();
    } else {
      audio.addEventListener("loadedmetadata", startPlayback, { once: true });
    }
  }

  let gestureArmed = false;
  function armGestureFallback() {
    if (gestureArmed) return;
    gestureArmed = true;
    const start = () => {
      gestureArmed = false;
      audio.play().catch(() => {});
      window.removeEventListener("pointerdown", start);
      window.removeEventListener("keydown", start);
    };
    window.addEventListener("pointerdown", start, { once: true });
    window.addEventListener("keydown", start, { once: true });
  }

  function decideInitialTrack() {
    const ctx = detectContext();
    const lastCtx = ls(KEYS.context, "");
    lsSet(KEYS.context, ctx);

    if (lastCtx && lastCtx !== ctx) {
      // Entrou ou saiu de partida: próxima faixa, do início.
      return { url: popNext(), startAt: 0 };
    }

    const savedTrack = ls(KEYS.track, "");
    if (savedTrack && tracks.indexOf(savedTrack) !== -1) {
      const lastTime = parseFloat(ls(KEYS.time, "0")) || 0;
      const lastUpdate = parseInt(ls(KEYS.updated, "0"), 10) || 0;
      const now = Date.now();
      const gap = lastUpdate ? now - lastUpdate : 0;
      if (gap > MAX_RESUME_GAP_MS) {
        return { url: popNext(), startAt: 0 };
      }
      const elapsedSecs = gap / 1000;
      return { url: savedTrack, startAt: lastTime + elapsedSecs };
    }

    return { url: popNext(), startAt: 0 };
  }

  // ---------- settings panel ----------
  const SVG_GEAR =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M19.14 12.94c.04-.31.06-.62.06-.94s-.02-.63-.06-.94l2.03-1.58a.5.5 0 00.12-.64l-1.92-3.32a.5.5 0 00-.61-.22l-2.39.96a7.03 7.03 0 00-1.62-.94l-.36-2.54a.5.5 0 00-.5-.42h-3.84a.5.5 0 00-.5.42l-.36 2.54a7.03 7.03 0 00-1.62.94l-2.39-.96a.5.5 0 00-.61.22L2.65 8.84a.5.5 0 00.12.64l2.03 1.58c-.04.31-.06.62-.06.94s.02.63.06.94l-2.03 1.58a.5.5 0 00-.12.64l1.92 3.32a.5.5 0 00.61.22l2.39-.96c.5.38 1.04.7 1.62.94l.36 2.54a.5.5 0 00.5.42h3.84a.5.5 0 00.5-.42l.36-2.54c.58-.24 1.12-.56 1.62-.94l2.39.96a.5.5 0 00.61-.22l1.92-3.32a.5.5 0 00-.12-.64l-2.03-1.58zM12 15.5A3.5 3.5 0 1112 8.5a3.5 3.5 0 010 7z"/>' +
    "</svg>";
  const SVG_VOL_ON =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10v4h4l5 5V5L7 10H3zm13.5 2a4.5 4.5 0 00-2.5-4.03v8.05A4.5 4.5 0 0016.5 12zm-2.5-7.18v2.06a7 7 0 010 10.24v2.06a9 9 0 000-14.36z"/></svg>';
  const SVG_VOL_OFF =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.21.05-.41.05-.63zM19 12c0 .94-.2 1.83-.55 2.64l1.51 1.51A8.94 8.94 0 0021 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06a8.99 8.99 0 003.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>';
  const SVG_SKIP =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l8.5 6L6 18V6zm10 0h2v12h-2V6z"/></svg>';

  function buildPanel() {
    const wrap = document.createElement("div");
    wrap.className = "audio-settings";
    wrap.innerHTML =
      '<button type="button" class="audio-settings-btn" id="audio-settings-btn" aria-label="Configurações">' +
      SVG_GEAR +
      "</button>" +
      '<div class="audio-settings-panel hidden" id="audio-settings-panel" role="dialog" aria-label="Configurações de áudio">' +
      '  <div class="aud-title">Trilha sonora</div>' +
      '  <div class="aud-track" id="audio-track-name">—</div>' +
      '  <div class="aud-row aud-volume-row">' +
      '    <button type="button" class="aud-icon-btn aud-mute-btn" id="audio-mute-btn" aria-label="Mudo">' +
      SVG_VOL_ON +
      "    </button>" +
      '    <input type="range" min="0" max="100" step="1" id="audio-vol-slider" aria-label="Volume" />' +
      '    <span class="aud-vol-value" id="audio-vol-value">50</span>' +
      "  </div>" +
      '  <div class="aud-row aud-controls-row">' +
      '    <button type="button" class="aud-icon-btn" id="audio-skip-btn" title="Próxima faixa">' +
      SVG_SKIP +
      '      <span>Próxima</span>' +
      "    </button>" +
      "  </div>" +
      '  <div class="aud-row aud-game-only" id="audio-game-row" hidden>' +
      '    <button type="button" class="aud-danger-btn" id="audio-surrender-btn">Render-se</button>' +
      "  </div>" +
      "</div>";
    document.body.appendChild(wrap);
    panelEl = wrap.querySelector(".audio-settings-panel");
    buttonEl = wrap.querySelector(".audio-settings-btn");
    return wrap;
  }

  function syncPanelControls() {
    if (!panelEl) return;
    const slider = panelEl.querySelector("#audio-vol-slider");
    const valEl = panelEl.querySelector("#audio-vol-value");
    const muteBtn = panelEl.querySelector("#audio-mute-btn");
    const trackName = panelEl.querySelector("#audio-track-name");
    const vol = getVolume();
    const muted = isMuted();
    if (slider) slider.value = String(Math.round(vol * 100));
    if (valEl) valEl.textContent = String(Math.round(vol * 100));
    if (muteBtn) {
      muteBtn.innerHTML = muted ? SVG_VOL_OFF : SVG_VOL_ON;
      muteBtn.setAttribute("aria-label", muted ? "Ativar som" : "Mudo");
      muteBtn.classList.toggle("muted", muted);
    }
    if (trackName) {
      if (currentTrack) {
        const file = decodeURIComponent(currentTrack.split("/").pop() || "");
        const stem = file.replace(/\.[^.]+$/, "");
        trackName.textContent = stem || "—";
      } else {
        trackName.textContent = tracks.length ? "—" : "Sem faixas";
      }
    }
  }

  function setNoTracksUI() {
    if (!panelEl) return;
    const slider = panelEl.querySelector("#audio-vol-slider");
    const muteBtn = panelEl.querySelector("#audio-mute-btn");
    const skipBtn = panelEl.querySelector("#audio-skip-btn");
    if (slider) slider.disabled = true;
    if (muteBtn) muteBtn.disabled = true;
    if (skipBtn) skipBtn.disabled = true;
    const trackName = panelEl.querySelector("#audio-track-name");
    if (trackName) trackName.textContent = "Sem faixas em /static/audio";
  }

  function setupHandlers() {
    if (!panelEl || !buttonEl) return;
    const slider = panelEl.querySelector("#audio-vol-slider");
    const muteBtn = panelEl.querySelector("#audio-mute-btn");
    const skipBtn = panelEl.querySelector("#audio-skip-btn");
    const surrenderBtn = panelEl.querySelector("#audio-surrender-btn");
    const gameRow = panelEl.querySelector("#audio-game-row");

    buttonEl.addEventListener("click", function (e) {
      e.stopPropagation();
      panelEl.classList.toggle("hidden");
    });
    document.addEventListener("click", function (e) {
      if (panelEl.classList.contains("hidden")) return;
      if (panelEl.contains(e.target)) return;
      if (buttonEl.contains(e.target)) return;
      panelEl.classList.add("hidden");
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !panelEl.classList.contains("hidden")) {
        panelEl.classList.add("hidden");
      }
    });

    if (slider) {
      slider.addEventListener("input", function (e) {
        const v = Math.min(1, Math.max(0, parseFloat(e.target.value) / 100));
        lsSet(KEYS.volume, String(v));
        // Mexer no slider acima de 0 sai automaticamente do mute.
        if (v > 0 && isMuted()) lsSet(KEYS.muted, "0");
        applyVolume();
      });
    }
    if (muteBtn) {
      muteBtn.addEventListener("click", function () {
        const newMuted = !isMuted();
        lsSet(KEYS.muted, newMuted ? "1" : "0");
        applyVolume();
      });
    }
    if (skipBtn) {
      skipBtn.addEventListener("click", function () {
        const next = popNext();
        if (next) play(next, 0);
      });
    }

    if (detectContext() === "match" && gameRow) {
      gameRow.hidden = false;
      if (surrenderBtn) {
        surrenderBtn.addEventListener("click", function () {
          panelEl.classList.add("hidden");
          if (typeof window.requestSurrender === "function") {
            window.requestSurrender();
          } else {
            window.dispatchEvent(new CustomEvent("audio-surrender-request"));
          }
        });
      }
    }
  }

  function setupAudio() {
    audio = document.createElement("audio");
    audio.preload = "auto";
    audio.style.display = "none";
    document.body.appendChild(audio);
    audio.addEventListener("ended", function () {
      const next = popNext();
      if (next) play(next, 0);
    });
    audio.addEventListener("timeupdate", function () {
      // Throttle: salva no máximo a cada ~1s.
      const last = parseInt(ls(KEYS.updated, "0"), 10) || 0;
      const now = Date.now();
      if (now - last >= 1000) persistPosition();
    });
    audio.addEventListener("error", function () {
      // Faixa quebrada: pula pra próxima sem travar.
      const next = popNext();
      if (next) play(next, 0);
    });
    window.addEventListener("beforeunload", persistPosition);
    window.addEventListener("pagehide", persistPosition);
  }

  async function init() {
    setupAudio();
    buildPanel();
    setupHandlers();

    tracks = await fetchTracks();
    applyVolume();
    if (!tracks.length) {
      setNoTracksUI();
      syncPanelControls();
      return;
    }
    ensureQueue();
    const initial = decideInitialTrack();
    if (initial.url) play(initial.url, initial.startAt);
    syncPanelControls();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
