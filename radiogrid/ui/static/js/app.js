/* ===================================================================
   RadioGrid — Game setup, replay and canvas rendering
   With zoom / pan / minimap / turn-scrubber
   =================================================================== */

"use strict";

const TEAM_COLOURS = [
  "#58a6ff", "#f0883e", "#3fb950", "#bc8cff",
  "#f778ba", "#79c0ff", "#d2a8ff", "#ffa657",
];

const TILE = {
  EMPTY:        "#1a1e24",
  OBSTACLE:     "#484f58",
  TRAP:         "#6e3630",
  SPAWN:        "#1c2d3a",
  OUT_OF_BOUNDS:"#000",
};

/* ---- state ---- */
let availableTeams = [];
let chosenTeams    = [];
let history        = null;
let currentTurn    = 0;
let playing        = false;
let playTimer      = null;

/* ---- zoom / pan state ---- */
const BASE_CELL = 24;            // logical cell size on the canvas
let zoom     = 1;                // current zoom scale
let panX     = 0;                // CSS-pixel offset of canvas
let panY     = 0;
let dragging = false;
let dragStartX = 0, dragStartY = 0;
let panStartX  = 0, panStartY  = 0;

const MIN_ZOOM = 0.15;
const MAX_ZOOM = 4;

/* ---- DOM ---- */
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

/* ============================================================
   1. STARTUP
   ============================================================ */
document.addEventListener("DOMContentLoaded", async () => {
  bindSetupEvents();
  bindViewportEvents();
  await loadTeams();
  if (chosenTeams.length === 0) { addTeamSlot(); addTeamSlot(); }
});

async function loadTeams() {
  const resp = await fetch("/api/teams");
  availableTeams = await resp.json();
  if (availableTeams.length >= 2) {
    chosenTeams = [availableTeams[0].key, availableTeams[1].key];
  }
  renderTeamList();
}

/* ============================================================
   2. SETUP PANEL — events
   ============================================================ */
function bindSetupEvents() {
  const obsSlider  = $("#cfg-obstacles");
  const trapSlider = $("#cfg-traps");
  obsSlider.addEventListener("input",  () => { $("#cfg-obstacles-val").textContent = Math.round(obsSlider.value  * 100) + "%"; });
  trapSlider.addEventListener("input", () => { $("#cfg-traps-val").textContent     = Math.round(trapSlider.value * 100) + "%"; });

  $("#btn-add-team").addEventListener("click", addTeamSlot);
  $("#btn-run").addEventListener("click", runGame);
  $("#btn-back").addEventListener("click", showSetup);

  // Playback
  $("#btn-start").addEventListener("click", () => goToTurn(0));
  $("#btn-prev").addEventListener("click",  () => goToTurn(currentTurn - 1));
  $("#btn-play").addEventListener("click",  togglePlay);
  $("#btn-next").addEventListener("click",  () => goToTurn(currentTurn + 1));
  $("#btn-end").addEventListener("click",   () => goToTurn(history.turns.length));

  // Turn scrub slider
  $("#turn-slider").addEventListener("input", (e) => {
    goToTurn(+e.target.value);
  });

  // Speed
  $("#speed-slider").addEventListener("input", () => {
    const v = +$("#speed-slider").value;
    $("#speed-val").textContent = v + " t/s";
    if (playing) { clearInterval(playTimer); playTimer = setInterval(stepForward, 1000 / v); }
  });

  // Zoom buttons
  $("#btn-zoom-in").addEventListener("click",  () => applyZoom(zoom * 1.3));
  $("#btn-zoom-out").addEventListener("click", () => applyZoom(zoom / 1.3));
  $("#btn-zoom-fit").addEventListener("click", fitToView);
}

/* ============================================================
   3. VIEWPORT — zoom / pan / drag / keyboard
   ============================================================ */
function bindViewportEvents() {
  const wrap = $("#canvas-wrap");

  // --- mouse wheel zoom ---
  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    zoomAtPoint(mx, my, factor);
  }, { passive: false });

  // --- drag to pan ---
  wrap.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    dragging = true;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
    panStartX  = panX;
    panStartY  = panY;
    wrap.classList.add("grabbing");
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    panX = panStartX + (e.clientX - dragStartX);
    panY = panStartY + (e.clientY - dragStartY);
    applyTransform();
    drawMinimap();
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    $("#canvas-wrap").classList.remove("grabbing");
  });

  // --- touch to pan ---
  let lastTouchDist = 0;
  wrap.addEventListener("touchstart", (e) => {
    if (e.touches.length === 1) {
      dragging = true;
      dragStartX = e.touches[0].clientX;
      dragStartY = e.touches[0].clientY;
      panStartX  = panX;
      panStartY  = panY;
    }
    if (e.touches.length === 2) {
      lastTouchDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }
  }, { passive: true });
  wrap.addEventListener("touchmove", (e) => {
    if (e.touches.length === 1 && dragging) {
      panX = panStartX + (e.touches[0].clientX - dragStartX);
      panY = panStartY + (e.touches[0].clientY - dragStartY);
      applyTransform();
      drawMinimap();
    }
    if (e.touches.length === 2) {
      const dist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      if (lastTouchDist > 0) {
        const factor = dist / lastTouchDist;
        const rect = wrap.getBoundingClientRect();
        const cx = (e.touches[0].clientX + e.touches[1].clientX) / 2 - rect.left;
        const cy = (e.touches[0].clientY + e.touches[1].clientY) / 2 - rect.top;
        zoomAtPoint(cx, cy, factor);
      }
      lastTouchDist = dist;
    }
  }, { passive: true });
  wrap.addEventListener("touchend", () => { dragging = false; lastTouchDist = 0; });

  // --- keyboard shortcuts ---
  window.addEventListener("keydown", (e) => {
    if (!history) return;
    if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
    switch (e.key) {
      case "ArrowRight": case "l": goToTurn(currentTurn + 1); break;
      case "ArrowLeft":  case "h": goToTurn(currentTurn - 1); break;
      case " ": e.preventDefault(); togglePlay(); break;
      case "+": case "=": applyZoom(zoom * 1.3); break;
      case "-": applyZoom(zoom / 1.3); break;
      case "0": fitToView(); break;
      case "Home": goToTurn(0); break;
      case "End":  goToTurn(history.turns.length); break;
    }
  });

  // --- minimap click to jump viewport ---
  const mm = $("#minimap-canvas");
  function minimapJump(e) {
    if (!history) return;
    const rect = mm.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const mmW = mm.width, mmH = mm.height;
    const fullW = history.map.width  * BASE_CELL;
    const fullH = history.map.height * BASE_CELL;
    const wrapRect = $("#canvas-wrap").getBoundingClientRect();
    // Map minimap coords to world coords
    const worldX = (mx / mmW) * fullW;
    const worldY = (my / mmH) * fullH;
    // Center the viewport on that world point
    panX = wrapRect.width  / 2 - worldX * zoom;
    panY = wrapRect.height / 2 - worldY * zoom;
    applyTransform();
    drawMinimap();
  }
  let mmDragging = false;
  mm.addEventListener("mousedown", (e) => { e.stopPropagation(); mmDragging = true; minimapJump(e); });
  window.addEventListener("mousemove", (e) => { if (mmDragging) minimapJump(e); });
  window.addEventListener("mouseup", () => { mmDragging = false; });
}

/* ---- zoom helpers ---- */
function zoomAtPoint(px, py, factor) {
  const newZoom = clampZoom(zoom * factor);
  if (newZoom === zoom) return;
  // Adjust pan so the point under the cursor stays fixed
  panX = px - (px - panX) * (newZoom / zoom);
  panY = py - (py - panY) * (newZoom / zoom);
  zoom = newZoom;
  applyTransform();
  drawMinimap();
}

function applyZoom(newZ) {
  const wrap = $("#canvas-wrap");
  const rect = wrap.getBoundingClientRect();
  zoomAtPoint(rect.width / 2, rect.height / 2, newZ / zoom);
}

function clampZoom(z) {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}

function fitToView() {
  if (!history) return;
  const wrap = $("#canvas-wrap");
  const rect = wrap.getBoundingClientRect();
  const fullW = history.map.width  * BASE_CELL;
  const fullH = history.map.height * BASE_CELL;
  const pad = 16;
  zoom = clampZoom(Math.min((rect.width - pad) / fullW, (rect.height - pad) / fullH));
  panX = (rect.width  - fullW * zoom) / 2;
  panY = (rect.height - fullH * zoom) / 2;
  applyTransform();
  drawMinimap();
}

function applyTransform() {
  const cvs = $("#grid-canvas");
  cvs.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
  $("#zoom-level").textContent = Math.round(zoom * 100) + "%";
}

/* ============================================================
   3b. TEAM LIST management
   ============================================================ */
function addTeamSlot() {
  chosenTeams.push(availableTeams.length ? availableTeams[0].key : "");
  renderTeamList();
}

function removeTeamSlot(idx) {
  chosenTeams.splice(idx, 1);
  renderTeamList();
}

function renderTeamList() {
  const container = $("#team-list");
  container.innerHTML = "";
  chosenTeams.forEach((key, idx) => {
    const row = document.createElement("div");
    row.className = "team-row";

    const swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = TEAM_COLOURS[idx % TEAM_COLOURS.length];

    const sel = document.createElement("select");
    availableTeams.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.key;
      opt.textContent = `${t.name}`;
      opt.title = t.description;
      if (t.key === key) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.addEventListener("change", () => { chosenTeams[idx] = sel.value; });

    const btn = document.createElement("button");
    btn.className = "btn-remove";
    btn.textContent = "✕";
    btn.title = "Remove team";
    btn.addEventListener("click", () => removeTeamSlot(idx));

    row.append(swatch, sel, btn);
    container.appendChild(row);
  });
}

/* ============================================================
   4. RUN GAME
   ============================================================ */
async function runGame() {
  const statusEl = $("#status-msg");
  statusEl.className = "";
  statusEl.textContent = "Running game…";

  const body = {
    teams:          chosenTeams,
    width:          +$("#cfg-width").value,
    height:         +$("#cfg-height").value,
    max_turns:      +$("#cfg-turns").value,
    obstacle_ratio: +$("#cfg-obstacles").value,
    trap_ratio:     +$("#cfg-traps").value,
    seed:           $("#cfg-seed").value || null,
  };

  try {
    const resp = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) { throw new Error(data.error || "Server error"); }

    history = data;
    currentTurn = 0;
    playing = false;
    clearInterval(playTimer);

    showVis();
    renderScoreboard();
    resizeCanvas();

    // Setup turn slider
    const slider = $("#turn-slider");
    slider.max = history.turns.length;
    slider.value = 0;

    // Fit to view on first load
    requestAnimationFrame(() => {
      fitToView();
      drawTurn();
      startAutoplay();
    });
  } catch (err) {
    statusEl.className = "error";
    statusEl.textContent = err.message;
  }
}

/* ============================================================
   5. PANEL SWITCHING
   ============================================================ */
function showSetup() {
  stopAutoplay();
  $("#setup-panel").classList.remove("hidden");
  $("#vis-panel").classList.add("hidden");
}

function showVis() {
  $("#setup-panel").classList.add("hidden");
  $("#vis-panel").classList.remove("hidden");
  $("#status-msg").textContent = "";
}

/* ============================================================
   6. SCOREBOARD
   ============================================================ */
function renderScoreboard() {
  const sb = $("#scoreboard");
  sb.innerHTML = "";
  history.teams.forEach((t, i) => {
    const item = document.createElement("div");
    item.className = "score-item";
    item.id = `score-team-${t.id}`;

    const sw = document.createElement("span");
    sw.className = "swatch";
    sw.style.background = TEAM_COLOURS[i % TEAM_COLOURS.length];

    const lbl = document.createElement("span");
    lbl.textContent = t.name;

    const val = document.createElement("span");
    val.className = "score-value";
    val.textContent = "0";

    item.append(sw, lbl, val);
    sb.appendChild(item);
  });
}

function updateScores(scores) {
  history.teams.forEach((t) => {
    const el = $(`#score-team-${t.id} .score-value`);
    if (el) el.textContent = scores[String(t.id)] ?? 0;
  });
}

/* ============================================================
   7. PLAYBACK
   ============================================================ */
function goToTurn(n) {
  if (!history) return;
  if (playing) stopAutoplay();
  currentTurn = Math.max(0, Math.min(n, history.turns.length));
  $("#turn-slider").value = currentTurn;
  drawTurn();
}

function togglePlay() {
  if (!history) return;
  if (!playing && currentTurn >= history.turns.length) {
    currentTurn = 0;
    drawTurn();
  }
  playing = !playing;
  if (playing) {
    startAutoplay();
  } else {
    stopAutoplay();
  }
}

function startAutoplay() {
  if (!history || history.turns.length === 0) return;
  playing = true;
  clearInterval(playTimer);
  const fps = +$("#speed-slider").value;
  playTimer = setInterval(stepForward, 1000 / fps);
  updatePlayButton();
}

function stopAutoplay() {
  playing = false;
  clearInterval(playTimer);
  updatePlayButton();
}

function updatePlayButton() {
  const btn = $("#btn-play");
  btn.textContent = playing ? "⏸" : "⏯";
  btn.title = playing ? "Pause" : "Play";
}

function stepForward() {
  if (currentTurn >= history.turns.length) { stopAutoplay(); return; }
  currentTurn++;
  $("#turn-slider").value = currentTurn;
  drawTurn();
}

/* ============================================================
   8. CANVAS RENDERING
   ============================================================ */
let canvasW = 0, canvasH = 0;

function resizeCanvas() {
  const cvs = $("#grid-canvas");
  canvasW = history.map.width  * BASE_CELL;
  canvasH = history.map.height * BASE_CELL;
  cvs.width  = canvasW;
  cvs.height = canvasH;
  // Don't set style width/height — we use CSS transform for zoom
}

function drawTurn() {
  const cvs = $("#grid-canvas");
  const ctx = cvs.getContext("2d");
  const mapData = history.map;

  ctx.clearRect(0, 0, canvasW, canvasH);

  // 1) Draw tiles
  for (let x = 0; x < mapData.width; x++) {
    for (let y = 0; y < mapData.height; y++) {
      const tileValue = mapData.tiles[x][y];
      ctx.fillStyle = TILE[tileValue] || TILE.EMPTY;
      ctx.fillRect(x * BASE_CELL, y * BASE_CELL, BASE_CELL, BASE_CELL);
    }
  }

  // 2) Grid lines
  ctx.strokeStyle = "#30363d";
  ctx.lineWidth = 0.5;
  for (let x = 0; x <= mapData.width; x++) {
    ctx.beginPath(); ctx.moveTo(x * BASE_CELL, 0); ctx.lineTo(x * BASE_CELL, canvasH); ctx.stroke();
  }
  for (let y = 0; y <= mapData.height; y++) {
    ctx.beginPath(); ctx.moveTo(0, y * BASE_CELL); ctx.lineTo(canvasW, y * BASE_CELL); ctx.stroke();
  }

  // Build cumulative visited set per team up to currentTurn
  const visitedSets = {};
  history.teams.forEach((t) => { visitedSets[t.id] = new Set(); });

  if (history.initial && history.initial.visited) {
    for (const [tid, coords] of Object.entries(history.initial.visited)) {
      coords.forEach(([x, y]) => visitedSets[tid].add(`${x},${y}`));
    }
  }
  for (let t = 0; t < currentTurn && t < history.turns.length; t++) {
    const snap = history.turns[t];
    if (snap.new_visits) {
      for (const [tid, coords] of Object.entries(snap.new_visits)) {
        coords.forEach(([x, y]) => visitedSets[tid].add(`${x},${y}`));
      }
    }
  }

  // 3) Paint visited tiles with translucent team colour
  history.teams.forEach((team, idx) => {
    const colour = TEAM_COLOURS[idx % TEAM_COLOURS.length];
    ctx.fillStyle = hexAlpha(colour, 0.18);
    const set = visitedSets[team.id];
    if (!set) return;
    set.forEach((key) => {
      const [x, y] = key.split(",").map(Number);
      ctx.fillRect(x * BASE_CELL, y * BASE_CELL, BASE_CELL, BASE_CELL);
    });
  });

  // Pick the snapshot for bots & scores
  let bots, scores;
  if (currentTurn === 0) {
    bots   = history.initial.bots;
    scores = history.initial.scores;
  } else {
    const snap = history.turns[currentTurn - 1];
    bots   = snap.bots;
    scores = snap.scores;
  }

  // 4) Draw bots
  const botR = Math.max(3, BASE_CELL * 0.22);
  bots.forEach((b) => {
    const teamIdx = history.teams.findIndex((t) => t.id === b.team_id);
    const colour = TEAM_COLOURS[teamIdx % TEAM_COLOURS.length];
    const cx = b.x * BASE_CELL + BASE_CELL / 2;
    const cy = b.y * BASE_CELL + BASE_CELL / 2;

    ctx.beginPath();
    ctx.arc(cx, cy, botR, 0, Math.PI * 2);
    ctx.fillStyle = b.frozen > 0 ? "#555" : colour;
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1;
    ctx.stroke();

    if (b.frozen > 0) {
      ctx.fillStyle = "#fff";
      ctx.font = "bold 8px monospace";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("❄", cx, cy);
    }
  });

  // 5) Update UI
  updateScores(scores);
  const total = history.turns.length;
  $("#turn-info").textContent = `Turn ${currentTurn} / ${total}`;

  // 6) Update minimap
  drawMinimap();
}

/* ============================================================
   9. MINIMAP
   ============================================================ */
const MINIMAP_MAX = 140; // max dimension in CSS pixels

function drawMinimap() {
  if (!history) return;
  const mm = $("#minimap-canvas");
  const mapW = history.map.width;
  const mapH = history.map.height;

  // Scale minimap proportionally
  const aspect = mapW / mapH;
  let mmW, mmH;
  if (aspect >= 1) {
    mmW = MINIMAP_MAX;
    mmH = Math.round(MINIMAP_MAX / aspect);
  } else {
    mmH = MINIMAP_MAX;
    mmW = Math.round(MINIMAP_MAX * aspect);
  }
  mm.width  = mmW;
  mm.height = mmH;
  mm.style.width  = mmW + "px";
  mm.style.height = mmH + "px";

  const ctx = mm.getContext("2d");
  const sx = mmW / mapW;
  const sy = mmH / mapH;

  // Draw tiles
  for (let x = 0; x < mapW; x++) {
    for (let y = 0; y < mapH; y++) {
      const tv = history.map.tiles[x][y];
      ctx.fillStyle = TILE[tv] || TILE.EMPTY;
      ctx.fillRect(x * sx, y * sy, Math.ceil(sx), Math.ceil(sy));
    }
  }

  // Draw bots as dots
  let bots;
  if (currentTurn === 0) {
    bots = history.initial.bots;
  } else {
    bots = history.turns[currentTurn - 1].bots;
  }
  bots.forEach((b) => {
    const teamIdx = history.teams.findIndex((t) => t.id === b.team_id);
    ctx.fillStyle = TEAM_COLOURS[teamIdx % TEAM_COLOURS.length];
    ctx.fillRect(b.x * sx, b.y * sy, Math.max(2, sx), Math.max(2, sy));
  });

  // Draw visible viewport rectangle
  const wrap = $("#canvas-wrap");
  const wrapRect = wrap.getBoundingClientRect();
  const fullW = mapW * BASE_CELL;
  const fullH = mapH * BASE_CELL;

  // Convert viewport corners from screen space to world space
  const worldLeft   = -panX / zoom;
  const worldTop    = -panY / zoom;
  const worldRight  = worldLeft + wrapRect.width  / zoom;
  const worldBottom = worldTop  + wrapRect.height / zoom;

  // Convert world space to minimap space
  const vx = (worldLeft   / fullW) * mmW;
  const vy = (worldTop    / fullH) * mmH;
  const vw = ((worldRight  - worldLeft) / fullW) * mmW;
  const vh = ((worldBottom - worldTop)  / fullH) * mmH;

  ctx.strokeStyle = "#58a6ff";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(
    Math.max(0, vx), Math.max(0, vy),
    Math.min(mmW - Math.max(0, vx), vw),
    Math.min(mmH - Math.max(0, vy), vh)
  );
}

/* ============================================================
   UTILITY
   ============================================================ */
function hexAlpha(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
