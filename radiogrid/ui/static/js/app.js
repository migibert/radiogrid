/* ===================================================================
   RadioGrid — Game setup, replay and canvas rendering
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
let availableTeams = [];   // [{key, name, description}]
let chosenTeams    = [];   // [key, key, ...]
let history        = null; // JSON from server
let currentTurn    = 0;    // 0 = initial state
let playing        = false;
let playTimer      = null;

/* ---- DOM ---- */
const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

/* ============================================================
   1. STARTUP
   ============================================================ */
document.addEventListener("DOMContentLoaded", async () => {
  bindSetupEvents();
  await loadTeams();
  // Start with 2 team slots
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
  // Range sliders → display %
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

  $("#speed-slider").addEventListener("input", () => {
    const v = +$("#speed-slider").value;
    $("#speed-val").textContent = v + " t/s";
    if (playing) { clearInterval(playTimer); playTimer = setInterval(stepForward, 1000 / v); }
  });
}

/* ============================================================
   3. TEAM LIST management
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
    drawTurn();

    // Auto-play from the start
    startAutoplay();
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
  drawTurn();
}

function togglePlay() {
  if (!history) return;
  // If we're at the end, restart before playing
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
  drawTurn();
}

/* ============================================================
   8. CANVAS RENDERING
   ============================================================ */
const CELL = 24;          // px per tile
const BOT_R = 5;          // bot circle radius

let canvasW = 0, canvasH = 0;

function resizeCanvas() {
  const cvs = $("#grid-canvas");
  canvasW = history.map.width  * CELL;
  canvasH = history.map.height * CELL;
  cvs.width  = canvasW;
  cvs.height = canvasH;
  cvs.style.width  = canvasW + "px";
  cvs.style.height = canvasH + "px";
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
      ctx.fillRect(x * CELL, y * CELL, CELL, CELL);
    }
  }

  // 2) Grid lines
  ctx.strokeStyle = "#30363d";
  ctx.lineWidth = 0.5;
  for (let x = 0; x <= mapData.width; x++) {
    ctx.beginPath(); ctx.moveTo(x * CELL, 0); ctx.lineTo(x * CELL, canvasH); ctx.stroke();
  }
  for (let y = 0; y <= mapData.height; y++) {
    ctx.beginPath(); ctx.moveTo(0, y * CELL); ctx.lineTo(canvasW, y * CELL); ctx.stroke();
  }

  // Build cumulative visited set per team up to currentTurn
  const visitedSets = {};
  history.teams.forEach((t) => { visitedSets[t.id] = new Set(); });

  // Initial visited
  if (history.initial && history.initial.visited) {
    for (const [tid, coords] of Object.entries(history.initial.visited)) {
      coords.forEach(([x, y]) => visitedSets[tid].add(`${x},${y}`));
    }
  }
  // Accumulate turn-by-turn
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
      ctx.fillRect(x * CELL, y * CELL, CELL, CELL);
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
  bots.forEach((b) => {
    const teamIdx = history.teams.findIndex((t) => t.id === b.team_id);
    const colour = TEAM_COLOURS[teamIdx % TEAM_COLOURS.length];
    const cx = b.x * CELL + CELL / 2;
    const cy = b.y * CELL + CELL / 2;

    ctx.beginPath();
    ctx.arc(cx, cy, BOT_R, 0, Math.PI * 2);
    ctx.fillStyle = b.frozen > 0 ? "#555" : colour;
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1;
    ctx.stroke();

    // Frozen indicator
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
