"use strict";
const $ = (id) => document.getElementById(id);
const PHASE_NAMES = {
  setup: "Preparación", reinforce: "Refuerzos", attack: "Ataque",
  conquer_move: "Ocupar territorio", plan: "Planificación", battle: "Batallas", fortify: "Fortificar", finished: "Fin de la partida",
};

let ws = null, me = null, roomCode = null;
let lobby = null, game = null, log = [];
let mapData = null, mapId = null, mapLoading = null;         // {json, svgText}
let selected = null;                       // selected source territory
let pendingFortify = null;                 // {from,to}
let selectedCards = new Set();
let placeCount = 1;
let pendingDeclare = null;                 // {from,to}: ataque que se está configurando
let timer = null;                          // {remaining,total,at}
let centers = {};                          // id -> [cx,cy]

/* ---------- landing ---------- */
async function init() {
  $("name").value = localStorage.getItem("risk-name") || "";
  const maps = await (await fetch("/api/maps")).json();
  $("mapSelect").innerHTML = maps.filter(m => m.has_svg)
    .map(m => `<option value="${m.id}">${m.name} (${m.territories})</option>`).join("");
  $("createBtn").onclick = createRoom;
  $("joinBtn").onclick = () => join($("codeInput").value.trim().toUpperCase());
  $("startBtn").onclick = () => send({ type: "start" });
  $("copyBtn").onclick = copyShareLink;
  $("batchSelect").onchange = (e) => send({ type: "settings", setup_batch: +e.target.value });
  $("turnSelect").onchange = (e) => send({ type: "settings", turn_time: +e.target.value });
  $("rulesSelect").onchange = (e) => send({ type: "settings", rules: e.target.value });
  setInterval(tickTimer, 250);
  if (location.hash.length === 5) { $("codeInput").value = location.hash.slice(1); }
}
function landingError(msg) { $("landingError").textContent = msg || ""; }

async function createRoom() {
  const r = await fetch("/api/rooms", {
    method: "POST", headers: { "Content-Type": "application/json" },
    // Si el creador escribió un código, la sala se crea con ese mismo código.
    body: JSON.stringify({ map: $("mapSelect").value, code: $("codeInput").value.trim() || null }),
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
    timer = m.timer ? { ...m.timer, at: Date.now() } : null;
    render();
  }
}

/* ---------- rendering ---------- */
function show(id) { for (const s of ["landing", "lobby", "game"]) $(s).hidden = s !== id; }

let lastKey = "";
async function render() {
  if (!game) return renderLobby();
  // reset transient selection whenever the turn/phase changes
  const key = game.current + game.phase + (game.round || "");
  if (key !== lastKey) { selected = null; pendingFortify = null; pendingDeclare = null; lastKey = key; }
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

async function copyShareLink() {
  const input = $("shareLink");
  try {
    await navigator.clipboard.writeText(input.value);
  } catch {
    input.select();
    document.execCommand("copy");
  }
  const btn = $("copyBtn");
  btn.textContent = "¡Copiado!";
  setTimeout(() => { btn.textContent = "Copiar"; }, 1500);
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
  $("turnSelect").disabled = $("rulesSelect").disabled = !isHost;
  $("turnSelect").value = String(lobby.turn_time);
  $("rulesSelect").value = lobby.rules;
  $("turnRow").hidden = lobby.rules !== "simultaneous";
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
  const ar = document.createElementNS(ns, "g"); ar.setAttribute("class", "arrows"); svg.appendChild(ar);
  const g = document.createElementNS(ns, "g"); g.setAttribute("class", "armies"); svg.appendChild(g);
  for (const p of svg.querySelectorAll("#territories path")) {
    centers[p.id] = [+p.dataset.cx, +p.dataset.cy];
    const a = document.createElementNS(ns, "g"); a.setAttribute("class", "army"); a.dataset.t = p.id;
    a.innerHTML = `<circle cx="${p.dataset.cx}" cy="${p.dataset.cy}" r="12"/><text x="${p.dataset.cx}" y="${p.dataset.cy}"></text>`;
    g.appendChild(a);
    p.addEventListener("click", () => onTerritoryClick(p.id));
    const title = document.createElementNS(ns, "title"); title.textContent = p.dataset.name; p.appendChild(title);
  }
}

function neighbors(t) { return mapData.json.territories[t].neighbors; }
// En la preparación simultánea no hay turnos: puedes actuar hasta confirmar la ronda.
const setupSim = () => game.phase === "setup" && game.setup_simultaneous;
const simPlay = () => game.simultaneous_play;
const iAmCurrent = () => setupSim()
  ? !game.setup_ready[me]
  : simPlay()
    ? (game.phase === "plan" || game.phase === "fortify") && !game.ready[me] && !game.eliminated.includes(me)
    : game.current === me && game.phase !== "finished";
// ejércitos de `t` que aún no están comprometidos en un ataque declarado
const freeArmies = (t) => game.armies[t] - 1 - game.declared.filter(d => d.from === t).reduce((s, d) => s + d.n, 0);
const attackPhase = () => game.phase === "attack" || (simPlay() && game.phase === "plan" && game.reinforcements === 0);

function legalTargets() {
  if (!selected) return [];
  if (attackPhase()) return neighbors(selected).filter(n => game.owner[n] !== me);
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
    p.classList.toggle("dim", attackPhase() && selected && !targets.has(t) && t !== selected);
    p.classList.toggle("clickable", iAmCurrent() && clickableNow(t));
    const txt = svg.querySelector(`.army[data-t="${t}"] text`);
    txt.textContent = game.armies[t];
  }
  drawArrows(svg);
}

function drawArrows(svg) {
  const ns = "http://www.w3.org/2000/svg", g = svg.querySelector(".arrows");
  g.innerHTML = "";
  const line = (src, dst, n, color, off) => {
    const [x1, y1] = centers[src], [x2, y2] = centers[dst];
    const l = document.createElementNS(ns, "line");
    for (const [k, v] of Object.entries({ x1, y1, x2: x1 + (x2 - x1) * 0.9, y2: y1 + (y2 - y1) * 0.9, stroke: color })) l.setAttribute(k, v);
    l.setAttribute("transform", `translate(${off},${off})`);
    g.appendChild(l);
    const t = document.createElementNS(ns, "text");
    t.setAttribute("x", x1 + (x2 - x1) * 0.55 + off); t.setAttribute("y", y1 + (y2 - y1) * 0.55 + off);
    t.textContent = n; g.appendChild(t);
  };
  if (!simPlay()) return;
  if (game.phase === "plan") for (const d of game.declared) line(d.from, d.to, d.n, colorOf(me), 0);
  if (game.phase === "battle") for (const b of game.battles) b.sides.forEach((s, i) => line(s.src, s.dst, s.n, colorOf(s.owner), b.kind === "field" ? (i ? 4 : -4) : 0));
}

function tickTimer() {
  const el = $("timer");
  if (!game || !timer || !simPlay() || game.phase === "finished") { el.hidden = true; return; }
  const left = Math.max(0, Math.ceil(timer.remaining - (Date.now() - timer.at) / 1000));
  el.hidden = false;
  el.classList.toggle("low", left <= 10);
  el.textContent = game.phase === "battle" ? `Próxima tirada en ${left}s`
    : `⏱ ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

function clickableNow(t) {
  const own = game.owner[t] === me, ph = game.phase;
  if (ph === "setup" || ph === "reinforce") return own;
  if (ph === "plan" && game.reinforcements === 0) {
    return selected ? (own || legalTargets().includes(t)) : own && freeArmies(t) > 0;
  }
  if (ph === "plan") return own;
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
  if (ph === "reinforce" || ((ph === "attack" || ph === "plan") && game.reinforcements > 0)) {
    return own && send({ type: "place", territory: t, count: Math.min(placeCount, game.reinforcements) });
  }
  if (ph === "plan") {
    if (own) { selected = (selected === t || freeArmies(t) < 1) ? null : t; pendingDeclare = null; }
    else if (selected && legalTargets().includes(t)) { pendingDeclare = { from: selected, to: t }; }
    renderSide(); paintMap();
  } else if (ph === "attack") {
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
    const simWait = simPlay() && (game.phase === "plan" || game.phase === "fortify") && p !== game.neutral
      && !game.eliminated.includes(p);
    const isCur = setupSim() ? game.setup_ready[p] === false
      : simPlay() ? (simWait && !game.ready[p]) : p === game.current && game.phase !== "finished";
    const cls = ["player", isCur ? "current" : "", game.eliminated.includes(p) ? "out" : ""].join(" ");
    const ready = setupSim() && p !== game.neutral ? (game.setup_ready[p] ? " ✔ listo" : " …colocando")
      : simWait ? (game.ready[p] ? " ✔ listo" : " …") : "";
    return `<div class="${cls}"><span class="dot" style="background:${colorOf(p)}"></span>${esc(nameOf(p))}${p === me ? " (tú)" : ""}${ready}
      <span class="nums">${terr}T · ${arm}⚔${p in game.card_counts ? ` · ${game.card_counts[p]}🂠` : ""}</span></div>`;
  }).join("");

  // phase
  const mine = iAmCurrent();
  const who = setupSim() ? `Ronda ${game.setup_round}`
    : simPlay() ? `Ronda ${game.round}`
    : mine ? "Tu turno" : `Turno de ${esc(nameOf(game.current))}`;
  $("phaseTitle").textContent = game.phase === "finished"
    ? `🏆 Ganó ${nameOf(game.winner)}` : `${who} — ${PHASE_NAMES[game.phase]}`;
  const c = $("controls"); c.innerHTML = "";
  let help = "";
  const ph = game.phase;
  if (setupSim()) {
    const quota = game.setup_quota[me], placed = game.setup_placed;
    if (mine) {
      help = `Coloca ${quota} ejércitos en secreto: ${placed}/${quota}. Los demás no ven lo que pones hasta el final de la preparación (te quedan ${game.setup_left[me]} en total).`;
      c.innerHTML = `<button id="suUndo" class="ghost" ${placed ? "" : "disabled"}>Deshacer</button>`;
      $("suUndo").onclick = () => send({ type: "setup_undo" });
    } else {
      help = "Ya colocaste esta ronda. Esperando a que los demás terminen…";
    }
  } else if (simPlay() && ph === "plan") {
    renderPlan(c);
    help = $("phaseHelp").dataset.next;
  } else if (simPlay() && ph === "battle") {
    help = "Todas las batallas se tiran a la vez. Antes de cada tirada puedes retirarte de las tuyas.";
  } else if (ph === "setup") {
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
      $("endT").onclick = () => { selected = pendingFortify = null; send({ type: simPlay() ? "ready" : "end_turn" }); };
    }
  }
  if (simPlay() && (ph === "fortify") && !mine && !game.eliminated.includes(me)) help = "Listo. Esperando a los demás…";
  $("phaseHelp").textContent = help;
  renderBattles();

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
    if (e.event === "conquest") return `<div>🏴 ${esc(nameOf(e.player))} conquista ${e.to}</div>`;
    if (e.event === "retreat") return `<div>↩ ${esc(nameOf(e.player))} se retira a ${e.to}</div>`;
    if (e.event === "eliminated") return `<div>☠ ${esc(nameOf(e.player))} eliminado</div>`;
    if (e.event === "battle") return `<div>⚔ Batalla ${e.kind === "field" ? "de campo" : "por ciudad"}: ${e.sides.map(x => `${esc(nameOf(x.owner))} ${x.src}→${x.dst} (${x.n})`).join(" ⇄ ")}</div>`;
    if (e.event === "trade") return `<div>🂠 ${esc(nameOf(e.player))} canjea por ${e.armies}</div>`;
    return "";
  }).join("");
}

/* ---------- rondas simultáneas ---------- */
function renderPlan(c) {
  const help = $("phaseHelp");
  if (game.eliminated.includes(me)) { help.dataset.next = "Estás eliminado."; return; }
  if (game.ready[me]) {
    help.dataset.next = "Listo. Esperando a los demás (o a que se acabe el tiempo)…";
    c.innerHTML = `<button id="unready" class="ghost">Cancelar listo</button>`;
    $("unready").onclick = () => send({ type: "ready", value: false });
    return;
  }
  const rows = game.declared.map(d => `<div class="decl">${d.from} → ${d.to} ×${d.n}
    <button class="ghost" data-f="${d.from}" data-t="${d.to}">✕</button></div>`).join("");
  let form = "";
  if (pendingDeclare) {
    const { from, to } = pendingDeclare;
    const cur = game.declared.find(d => d.from === from && d.to === to);
    const max = freeArmies(from) + (cur ? cur.n : 0);
    form = `<div class="decl" style="flex-wrap:wrap">${from} → ${to}
      <input type="number" id="dn" min="1" max="${max}" value="${cur ? cur.n : max}">
      <button id="dOk">Declarar ataque</button><button class="ghost" id="dNo">Cancelar</button></div>`;
  }
  if (game.reinforcements > 0) {
    placeCount = Math.max(1, Math.min(placeCount, game.reinforcements));
    help.dataset.next = `Refuerzos por colocar: ${game.reinforcements}. Haz clic en un territorio tuyo.` +
      (game.cards.length >= 5 ? " Debes canjear cartas antes de colocar." : "");
    c.innerHTML = `<label>Cantidad <input type="number" id="pc" min="1" max="${game.reinforcements}" value="${placeCount}"></label>
      <button class="ghost" id="pAll">Todos</button>`;
    $("pc").oninput = (e) => { placeCount = Math.max(1, +e.target.value || 1); };
    $("pAll").onclick = () => { placeCount = game.reinforcements; $("pc").value = placeCount; };
  } else {
    help.dataset.next = selected
      ? `Origen: ${selected}. Elige un territorio enemigo adyacente (rojo). Los ataques son secretos hasta la batalla.`
      : "Elige un territorio tuyo para declarar un ataque (los ataques son secretos hasta que todos estén listos).";
    c.innerHTML = rows + form;
  }
  c.insertAdjacentHTML("beforeend", `<button id="readyBtn" style="width:100%">✔ Terminar turno</button>`);
  $("readyBtn").onclick = () => { selected = pendingDeclare = null; send({ type: "ready" }); };
  c.querySelectorAll("[data-f]").forEach(b => b.onclick = () =>
    send({ type: "undeclare", from: b.dataset.f, to: b.dataset.t }));
  if (pendingDeclare && $("dOk")) {
    $("dOk").onclick = () => {
      send({ type: "declare", from: pendingDeclare.from, to: pendingDeclare.to, n: +$("dn").value });
      pendingDeclare = selected = null;
    };
    $("dNo").onclick = () => { pendingDeclare = null; renderSide(); paintMap(); };
  }
}

function diceHtml(res) {
  if (!res || !res.rolls.length) return res && res.retreat.length === 2 ? "ambos se retiran" : "";
  const [a, b] = res.rolls;
  return `${a.map(d => `<span class="dice a">${d}</span>`).join("")} vs ${b.map(d => `<span class="dice d">${d}</span>`).join("")}
    — bajas ${res.lost[0]} / ${res.lost[1]}`;
}

function renderBattles() {
  const box = $("battleBox");
  if (!simPlay() || game.phase !== "battle") {
    // el resultado de la última tirada se queda visible hasta la siguiente ronda
    box.innerHTML = "";
    return;
  }
  const inBattle = game.battles.some(b => b.sides.some(s => s.owner === me));
  const goBtn = inBattle ? `<button id="goBtn" ${game.battle_go.includes(me) ? "disabled" : ""}>Continuar ▶</button>` : "";
  box.innerHTML = `<b>Batallas</b> ${goBtn}` + game.battles.map(b => {
    const res = game.last_tick.find(r => r.id === b.id);
    const mineB = b.sides.some(s => s.owner === me);
    const sides = b.sides.map(s => {
      const btn = s.owner === me && s.can_retreat
        ? `<button class="ghost ${s.retreating ? "on" : ""}" data-b="${b.id}" data-on="${s.retreating ? 0 : 1}">${s.retreating ? "↩ Retirándose" : "Retirarse"}</button>`
        : (s.owner === me ? `<i>(sin retirada)</i>` : "");
      return `<div class="sd"><span class="dot" style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${colorOf(s.owner)}"></span>
        ${esc(nameOf(s.owner))} ${s.src}→${s.dst} <b>×${s.n}</b> ${btn}</div>`;
    }).join("");
    return `<div class="battle ${mineB ? "mine" : ""}"><div>${b.kind === "field" ? "⚔ Campo abierto" : "🏰 Ciudad"} #${b.id}</div>${sides}
      <div>${diceHtml(res)}</div></div>`;
  }).join("");
  box.querySelectorAll("[data-b]").forEach(btn => btn.onclick = () =>
    send({ type: "retreat", battle: +btn.dataset.b, value: btn.dataset.on === "1" }));
  if ($("goBtn")) $("goBtn").onclick = () => send({ type: "continue" });
}

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
let toastTimer;
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
}

init();
