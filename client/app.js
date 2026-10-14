"use strict";
const $ = (id) => document.getElementById(id);
const PHASE_NAMES = {
  setup: "Preparación", reinforce: "Refuerzos", attack: "Ataque",
  conquer_move: "Ocupar territorio", fortify: "Fortificar", finished: "Fin de la partida",
};

let ws = null, me = null, roomCode = null;
let lobby = null, game = null, log = [];
let mapData = null, mapId = null, mapLoading = null;         // {json, svgText}
let selected = null;                       // selected source territory
let pendingFortify = null;                 // {from,to}
let selectedCards = new Set();
let placeCount = 1;

/* ---------- landing ---------- */
async function init() {
  $("name").value = localStorage.getItem("risk-name") || "";
  const maps = await (await fetch("/api/maps")).json();
  $("mapSelect").innerHTML = maps.filter(m => m.has_svg)
    .map(m => `<option value="${m.id}">${m.name} (${m.territories})</option>`).join("");
  $("createBtn").onclick = createRoom;
  $("joinBtn").onclick = () => join($("codeInput").value.trim().toUpperCase());
  $("startBtn").onclick = () => send({ type: "start" });
  $("batchSelect").onchange = (e) => send({ type: "settings", setup_batch: +e.target.value });
  if (location.hash.length === 5) { $("codeInput").value = location.hash.slice(1); }
}
function landingError(msg) { $("landingError").textContent = msg || ""; }

async function createRoom() {
  const r = await fetch("/api/rooms", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ map: $("mapSelect").value }),
  });
  if (!r.ok) return landingError((await r.json()).detail);
  join((await r.json()).code);
}

function join(code) {
  if (code.length !== 4) return landingError("Código inválido");
  const name = $("name").value.trim();
  if (!name && !localStorage.getItem("risk-token-" + code)) return landingError("Escribe tu nombre");
  localStorage.setItem("risk-name", name);
  roomCode = code;
  const token = localStorage.getItem("risk-token-" + code) || "";
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${code}?name=${encodeURIComponent(name)}&token=${token}`);
  ws.onmessage = (e) => onMessage(JSON.parse(e.data));
  ws.onclose = () => { if (me) toast("Conexión perdida. Recarga la página para reconectar."); };
}
const send = (m) => ws && ws.readyState === 1 && ws.send(JSON.stringify(m));

function onMessage(m) {
  if (m.type === "fatal") { landingError(m.message); ws = null; return; }
  if (m.type === "welcome") {
    me = m.pid; localStorage.setItem("risk-token-" + roomCode, m.token);
    location.hash = roomCode;
  } else if (m.type === "error") {
    toast(m.message);
  } else if (m.type === "state") {
    lobby = m.lobby; game = m.game; log = m.log;
    render();
  }
}

/* ---------- rendering ---------- */
function show(id) { for (const s of ["landing", "lobby", "game"]) $(s).hidden = s !== id; }

let lastKey = "";
async function render() {
  if (!game) return renderLobby();
  // reset transient selection whenever the turn/phase changes
  const key = game.current + game.phase;
  if (key !== lastKey) { selected = null; pendingFortify = null; lastKey = key; }
  if (mapId !== lobby.map) {
    mapId = lobby.map;
    mapLoading = Promise.all([
      fetch(`/maps/${mapId}/map.json`).then(r => r.json()),
      fetch(`/maps/${mapId}/map.svg`).then(r => r.text()),
    ]).then(([json, svgText]) => {
      mapData = { json };
      $("mapWrap").innerHTML = svgText;
      buildMap();
    });
  }
  await mapLoading;
  show("game");
  paintMap(); renderSide();
}

function renderLobby() {
  show("lobby");
  $("lobbyCode").textContent = lobby.code;
  $("shareLink").value = `${location.origin}/#${lobby.code}`;
  $("lobbyMembers").innerHTML = lobby.members.map(m =>
    `<li style="border-color:${m.color}">${esc(m.name)}${m.pid === lobby.host ? " ★" : ""}${m.pid === me ? " (tú)" : ""}${m.connected ? "" : " — desconectado"}</li>`).join("");
  const isHost = me === lobby.host;
  $("startBtn").hidden = !isHost;
  $("batchSelect").disabled = !isHost;
  $("batchSelect").value = String(lobby.setup_batch);
  $("startBtn").disabled = lobby.members.length < 2;
  $("lobbyHint").textContent = lobby.members.length < 2
    ? "Se necesitan al menos 2 jugadores." : (isHost ? "Todo listo." : "Esperando al anfitrión…");
}

const NEUTRAL_COLOR = "#7a7f87";
const colorOf = (pid) => pid === "neutral" ? NEUTRAL_COLOR : lobby.members.find(m => m.pid === pid).color;
const nameOf = (pid) => pid === "neutral" ? "Neutral" : lobby.members.find(m => m.pid === pid).name;

function buildMap() {
  const svg = $("mapWrap").querySelector("svg");
  const ns = "http://www.w3.org/2000/svg";
  const g = document.createElementNS(ns, "g"); g.setAttribute("class", "armies"); svg.appendChild(g);
  for (const p of svg.querySelectorAll("#territories path")) {
    const a = document.createElementNS(ns, "g"); a.setAttribute("class", "army"); a.dataset.t = p.id;
    a.innerHTML = `<circle cx="${p.dataset.cx}" cy="${p.dataset.cy}" r="12"/><text x="${p.dataset.cx}" y="${p.dataset.cy}"></text>`;
    g.appendChild(a);
    p.addEventListener("click", () => onTerritoryClick(p.id));
    const title = document.createElementNS(ns, "title"); title.textContent = p.dataset.name; p.appendChild(title);
  }
}

function neighbors(t) { return mapData.json.territories[t].neighbors; }
const iAmCurrent = () => game.current === me && game.phase !== "finished";

function legalTargets() {
  if (!selected) return [];
  if (game.phase === "attack") return neighbors(selected).filter(n => game.owner[n] !== me);
  return [];
}

function paintMap() {
  const svg = $("mapWrap").querySelector("svg");
  const targets = new Set(legalTargets());
  for (const p of svg.querySelectorAll("#territories path")) {
    const t = p.id, own = game.owner[t] === me;
    p.style.fill = colorOf(game.owner[t]);
    p.classList.toggle("sel", t === selected || (pendingFortify && t === pendingFortify.to));
    p.classList.toggle("target", targets.has(t));
    p.classList.toggle("dim", game.phase === "attack" && selected && !targets.has(t) && t !== selected);
    p.classList.toggle("clickable", iAmCurrent() && clickableNow(t));
    const txt = svg.querySelector(`.army[data-t="${t}"] text`);
    txt.textContent = game.armies[t];
  }
}

function clickableNow(t) {
  const own = game.owner[t] === me, ph = game.phase;
  if (ph === "setup" || ph === "reinforce") return own;
  if (ph === "attack") {
    if (game.reinforcements > 0) return own;
    return selected ? (own || legalTargets().includes(t)) : own && game.armies[t] > 1;
  }
  if (ph === "fortify") return own;
  return false;
}

function onTerritoryClick(t) {
  if (!iAmCurrent()) return;
  const own = game.owner[t] === me, ph = game.phase;
  if (ph === "setup") return own && send({ type: "place", territory: t });
  if (ph === "reinforce" || (ph === "attack" && game.reinforcements > 0)) {
    return own && send({ type: "place", territory: t, count: Math.min(placeCount, game.reinforcements) });
  }
  if (ph === "attack") {
    if (own) { selected = (selected === t || game.armies[t] < 2) ? null : t; }
    else if (selected && legalTargets().includes(t)) { showAttackControls(selected, t); return; }
    renderSide(); paintMap();
  } else if (ph === "fortify") {
    if (!own) return;
    if (!selected) selected = t;
    else if (selected === t) selected = null;
    else { pendingFortify = { from: selected, to: t }; }
    renderSide(); paintMap();
  }
}

function showAttackControls(from, to) {
  const max = Math.min(3, game.armies[from] - 1);
  const c = $("controls");
  c.innerHTML = `<div style="width:100%">${from} → ${to}</div>` +
    Array.from({ length: max }, (_, i) => `<button data-d="${i + 1}">🎲 ${i + 1}</button>`).join("") +
    `<button class="ghost" id="blitzBtn">Hasta ganar</button><button class="ghost" id="cancelBtn">Cancelar</button>`;
  c.querySelectorAll("[data-d]").forEach(b => b.onclick = () =>
    send({ type: "attack", from, to, dice: +b.dataset.d }));
  $("blitzBtn").onclick = () => send({ type: "blitz", from, to });
  $("cancelBtn").onclick = () => { selected = null; renderSide(); paintMap(); };
}

function renderSide() {
  // players
  $("players").innerHTML = game.players.concat(game.neutral ? [game.neutral] : []).map(p => {
    const terr = Object.values(game.owner).filter(o => o === p).length;
    const arm = Object.entries(game.owner).filter(([, o]) => o === p).reduce((s, [t]) => s + game.armies[t], 0);
    const cls = ["player", p === game.current && game.phase !== "finished" ? "current" : "", game.eliminated.includes(p) ? "out" : ""].join(" ");
    return `<div class="${cls}"><span class="dot" style="background:${colorOf(p)}"></span>${esc(nameOf(p))}${p === me ? " (tú)" : ""}
      <span class="nums">${terr}T · ${arm}⚔${p in game.card_counts ? ` · ${game.card_counts[p]}🂠` : ""}</span></div>`;
  }).join("");

  // phase
  const mine = iAmCurrent();
  const who = mine ? "Tu turno" : `Turno de ${esc(nameOf(game.current))}`;
  $("phaseTitle").textContent = game.phase === "finished"
    ? `🏆 Ganó ${nameOf(game.winner)}` : `${who} — ${PHASE_NAMES[game.phase]}`;
  const c = $("controls"); c.innerHTML = "";
  let help = "";
  const ph = game.phase;
  if (ph === "setup") {
    help = mine
      ? `Coloca ejércitos: ${game.setup_turn_left} en este turno (te quedan ${game.setup_left[me]} en total).`
      : "Esperando colocación inicial…";
  } else if (ph === "reinforce" || (ph === "attack" && game.reinforcements > 0 && mine)) {
    if (mine) {
      help = `Refuerzos por colocar: ${game.reinforcements}. Haz clic en un territorio tuyo.`;
      placeCount = Math.max(1, Math.min(placeCount, game.reinforcements));
      c.innerHTML = `<label>Cantidad <input type="number" id="pc" min="1" max="${game.reinforcements}" value="${placeCount}"></label>
        <button class="ghost" id="pAll">Todos</button>`;
      $("pc").oninput = (e) => { placeCount = Math.max(1, +e.target.value || 1); };
      $("pAll").onclick = () => { placeCount = game.reinforcements; $("pc").value = placeCount; };
      if (game.cards.length >= 5) help += " Debes canjear cartas antes de colocar.";
    }
  } else if (ph === "attack" && mine) {
    help = selected ? `Origen: ${selected}. Elige un territorio enemigo adyacente (rojo).` : "Elige un territorio tuyo con 2+ ejércitos para atacar.";
    c.innerHTML = `<button id="endAtk">Terminar ataque</button>`;
    $("endAtk").onclick = () => { selected = null; send({ type: "end_attack" }); };
  } else if (ph === "conquer_move" && mine) {
    const pc = game.pending_conquest;
    help = `Conquistaste ${pc.to}. Mueve entre ${pc.min} y ${pc.max} ejércitos desde ${pc.from}.`;
    c.innerHTML = `<input type="number" id="mv" min="${pc.min}" max="${pc.max}" value="${pc.max}">
      <button id="mvBtn">Mover</button>`;
    $("mvBtn").onclick = () => send({ type: "move_in", count: +$("mv").value });
  } else if (ph === "fortify" && mine) {
    if (pendingFortify) {
      const { from, to } = pendingFortify, max = game.armies[from] - 1;
      help = `Mover de ${from} a ${to}.`;
      c.innerHTML = `<input type="number" id="fc" min="1" max="${max}" value="${max}">
        <button id="fBtn">Mover y terminar turno</button><button class="ghost" id="fCancel">Cancelar</button>`;
      $("fBtn").onclick = () => { send({ type: "fortify", from, to, count: +$("fc").value }); pendingFortify = selected = null; };
      $("fCancel").onclick = () => { pendingFortify = selected = null; renderSide(); paintMap(); };
    } else {
      help = selected ? `Origen: ${selected}. Elige el destino (conectado por tus territorios).` : "Elige un territorio de origen, o termina el turno.";
      c.innerHTML = `<button id="endT" class="ghost">Terminar turno sin mover</button>`;
      $("endT").onclick = () => { selected = pendingFortify = null; send({ type: "end_turn" }); };
    }
  }
  $("phaseHelp").textContent = help;

  // cards
  const box = $("cardsBox");
  if (game.cards.length) {
    box.innerHTML = `<b>Tus cartas</b> (siguiente canje: ${game.next_set_value})` +
      game.cards.map((cd, i) => `<div class="card ${selectedCards.has(i) ? "sel" : ""}" data-i="${i}">
        <input type="checkbox" ${selectedCards.has(i) ? "checked" : ""}> ${cd.symbol}${cd.territory ? " · " + mapData.json.territories[cd.territory].name : ""}</div>`).join("") +
      `<button id="tradeBtn" ${selectedCards.size === 3 && mine ? "" : "disabled"}>Canjear 3 cartas</button>`;
    box.querySelectorAll(".card").forEach(el => el.onclick = () => {
      const i = +el.dataset.i;
      selectedCards.has(i) ? selectedCards.delete(i) : selectedCards.add(i);
      renderSide();
    });
    $("tradeBtn").onclick = () => { send({ type: "trade", indices: [...selectedCards] }); selectedCards.clear(); };
  } else { box.innerHTML = ""; selectedCards.clear(); }

  // last attack
  const lastAtk = [...log].reverse().find(e => e.event === "attack");
  $("lastAttack").innerHTML = lastAtk ? `<b>Último ataque</b><div>${esc(nameOf(lastAtk.attacker))} ${lastAtk.from} → ${lastAtk.to}</div>
    <div>${lastAtk.attacker_rolls.map(d => `<span class="dice a">${d}</span>`).join("")} vs
    ${lastAtk.defender_rolls.map(d => `<span class="dice d">${d}</span>`).join("")}
    — bajas: ${lastAtk.attacker_lost} / ${lastAtk.defender_lost}${lastAtk.conquered ? " ✔ conquistado" : ""}</div>` : "";

  // log
  $("logBox").innerHTML = [...log].reverse().slice(0, 25).map(e => {
    if (e.event === "attack") return `<div>⚔ ${esc(nameOf(e.attacker))}: ${e.from}→${e.to} (${e.attacker_lost}/${e.defender_lost})${e.conquered ? " conquista" : ""}${e.eliminated ? ` — elimina a ${esc(nameOf(e.eliminated))}` : ""}</div>`;
    if (e.event === "trade") return `<div>🂠 ${esc(nameOf(e.player))} canjea por ${e.armies}</div>`;
    return "";
  }).join("");
}

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
let toastTimer;
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

init();
