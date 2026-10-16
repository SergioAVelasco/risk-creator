"""Room/lobby logic, independent of the web framework (testable without sockets)."""
from __future__ import annotations

import random
import secrets
import string
import time
from dataclasses import dataclass, field
from pathlib import Path

from risk import Game, GameMap, RuleError

ROOT = Path(__file__).resolve().parent.parent
MAPS_DIR = ROOT / "maps"
# gouache apagado: se lee bien sobre la carta y con contorno de tinta
COLORS = ["#b8433b", "#3f6fa8", "#5f9a56", "#d9a62e", "#8a5fa0", "#3f9b9b"]
MAX_PLAYERS = 6
SETUP_BATCH_OPTIONS = (5, 10, 20, 0)  # ejércitos por ronda; 0 = todos de una vez
DEFAULT_SETUP_BATCH = 10
TURN_TIME_OPTIONS = (0, 60, 120, 180)  # segundos de planificación; 0 = sin límite
DEFAULT_TURN_TIME = 120
BATTLE_TICK_SECONDS = 5  # ventana para decidir retiradas entre tiradas
RULES = ("simultaneous", "classic")


def list_maps() -> list[dict]:
    out = []
    for d in sorted(MAPS_DIR.iterdir()):
        f = d / "map.json"
        if f.is_file():
            try:
                m = GameMap.load(f)
            except Exception:  # broken maps are simply not offered
                continue
            out.append({"id": d.name, "name": m.name, "territories": len(m.territories),
                        "has_svg": (d / "map.svg").is_file()})
    return out


@dataclass
class Member:
    pid: str
    name: str
    token: str
    color: str
    connected: bool = False


@dataclass
class Room:
    code: str
    map_id: str
    members: dict[str, Member] = field(default_factory=dict)  # pid -> Member
    host: str | None = None
    game: Game | None = None
    setup_batch: int = DEFAULT_SETUP_BATCH
    turn_time: int = DEFAULT_TURN_TIME
    rules: str = "simultaneous"
    deadline: float | None = None
    _timer_key: tuple | None = None
    _timer_total: float = 0

    # -- membership
    def join(self, name: str, token: str | None) -> Member:
        if token:
            for m in self.members.values():
                if m.token == token:
                    return m
        # Sin token válido (otro navegador, datos borrados…): se puede recuperar
        # el asiento de un jugador DESCONECTADO usando el mismo nombre con el
        # que entró. El asiento conserva su token, que el cliente guardará.
        wanted = (name or "").strip()[:20].lower()
        if wanted:
            for m in self.members.values():
                if not m.connected and m.name.lower() == wanted:
                    return m
            # Nombre ya tomado por alguien conectado: los nombres son únicos
            # en la sala (también es lo que permite reconectar por nombre).
            if any(m.name.lower() == wanted for m in self.members.values()):
                raise RuleError("that name is already taken in this room")
        if self.game is not None:
            raise RuleError("the game already started; to rejoin use the same name "
                            "you had (and only while that player is disconnected)")
        if len(self.members) >= MAX_PLAYERS:
            raise RuleError("the room is full")
        name = (name or "Player").strip()[:20] or "Player"
        used = {m.color for m in self.members.values()}
        color = next(c for c in COLORS if c not in used)
        pid = f"p{len(self.members) + 1}"
        m = Member(pid, name, secrets.token_urlsafe(12), color)
        self.members[pid] = m
        if self.host is None:
            self.host = pid
        return m

    def set_setup_batch(self, pid: str, value: int) -> None:
        if pid != self.host:
            raise RuleError("only the host can change the settings")
        if self.game is not None:
            raise RuleError("the game already started")
        if value not in SETUP_BATCH_OPTIONS:
            raise RuleError("invalid setup batch")
        self.setup_batch = value

    def set_option(self, pid: str, msg: dict) -> None:
        if pid != self.host:
            raise RuleError("only the host can change the settings")
        if self.game is not None:
            raise RuleError("the game already started")
        if "setup_batch" in msg:
            self.set_setup_batch(pid, int(msg["setup_batch"]))
        if "turn_time" in msg:
            if int(msg["turn_time"]) not in TURN_TIME_OPTIONS:
                raise RuleError("invalid turn time")
            self.turn_time = int(msg["turn_time"])
        if "rules" in msg:
            if msg["rules"] not in RULES:
                raise RuleError("invalid rules")
            self.rules = msg["rules"]

    # -- temporizador (el motor no conoce el tiempo)
    def _phase_seconds(self) -> float:
        g = self.game
        if g is None or not g.simultaneous_play:
            return 0
        if g.phase == "plan":
            return self.turn_time
        if g.phase == "fortify":
            return max(15, self.turn_time // 3) if self.turn_time else 0
        if g.phase == "battle":
            return BATTLE_TICK_SECONDS
        return 0

    def sync_timer(self, now: float) -> None:
        """Reinicia el plazo cuando cambia la fase, la ronda o el tick."""
        g = self.game
        if g is None or not g.simultaneous_play:
            self.deadline = None
            return
        key = (g.phase, g.round, g.serial)
        if key != self._timer_key:
            self._timer_key = key
            self._timer_total = self._phase_seconds()
            self.deadline = now + self._timer_total if self._timer_total else None

    def poll(self, now: float) -> bool:
        """Llamado periódicamente por el servidor; True si el estado cambió."""
        self.sync_timer(now)
        if self.deadline is not None and now >= self.deadline:
            self.game.force_advance()
            self.sync_timer(now)
            return True
        return False

    def start(self, pid: str) -> None:
        if pid != self.host:
            raise RuleError("only the host can start the game")
        if self.game is not None:
            raise RuleError("the game already started")
        if len(self.members) < 2:
            raise RuleError("you need at least 2 players")
        gm = GameMap.load(MAPS_DIR / self.map_id / "map.json")
        self.game = Game(gm, list(self.members), setup_batch=self.setup_batch,
                         simultaneous_setup=True,
                         simultaneous_play=self.rules == "simultaneous")

    # -- actions
    def act(self, pid: str, msg: dict) -> None:
        g = self.game
        if g is None:
            raise RuleError("the game has not started")
        t = msg.get("type")
        i = lambda k: int(msg.get(k, 0))  # noqa: E731
        if t == "place":
            g.place(pid, msg["territory"], i("count") or 1)
        elif t == "setup_undo":
            g.undo_setup(pid)
        elif t == "trade":
            g.trade_cards(pid, [int(x) for x in msg.get("indices", [])])
        elif t == "attack":
            g.attack(pid, msg["from"], msg["to"], i("dice"))
        elif t == "blitz":
            self._blitz(pid, msg["from"], msg["to"])
        elif t == "move_in":
            g.move_in(pid, i("count"))
        elif t == "end_attack":
            g.end_attack(pid)
        elif t == "fortify":
            g.fortify(pid, msg["from"], msg["to"], i("count"))
        elif t == "end_turn":
            g.end_turn(pid)
        elif t == "declare":
            g.declare_attack(pid, msg["from"], msg["to"], i("n"))
        elif t == "undeclare":
            g.cancel_attack(pid, msg["from"], msg["to"])
        elif t == "ready":
            g.set_ready(pid, bool(msg.get("value", True)))
        elif t == "retreat":
            g.set_retreat(pid, i("battle"), bool(msg.get("value", True)))
        elif t == "continue":
            g.battle_continue(pid)
        else:
            raise RuleError(f"unknown action {t!r}")

    def _blitz(self, pid: str, src: str, dst: str) -> None:
        g = self.game
        while True:
            dice = min(3, g.armies[src] - 1)
            r = g.attack(pid, src, dst, dice)
            if r["conquered"] or g.armies[src] < 2 or g.phase != "attack":
                return

    # -- views
    def lobby_view(self) -> dict:
        return {
            "code": self.code, "map": self.map_id, "host": self.host,
            "started": self.game is not None,
            "setup_batch": self.setup_batch,
            "turn_time": self.turn_time, "rules": self.rules,
            "members": [{"pid": m.pid, "name": m.name, "color": m.color, "connected": m.connected}
                        for m in self.members.values()],
        }

    def view_for(self, pid: str) -> dict:
        v = {"lobby": self.lobby_view(), "you": pid, "game": None, "log": []}
        now = time.time()
        self.sync_timer(now)
        v["timer"] = None
        if self.deadline is not None:
            v["timer"] = {"remaining": max(0.0, self.deadline - now), "total": self._timer_total}
        if self.game:
            v["game"] = self.game.to_dict(viewer=pid)
            v["log"] = self.game.log[-40:]
        return v


class Rooms:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def create(self, map_id: str, code: str | None = None) -> Room:
        if map_id not in {m["id"] for m in list_maps()}:
            raise RuleError("unknown map")
        code = (code or "").strip().upper()
        if code:
            # Código elegido por el creador: se conserva tal cual si es válido.
            if len(code) != 4 or not all(c in string.ascii_uppercase + string.digits for c in code):
                raise RuleError("the code must be 4 letters or digits")
            if code in self.rooms:
                raise RuleError("that code is already in use")
        else:
            while True:
                code = "".join(random.choices(string.ascii_uppercase, k=4))
                if code not in self.rooms:
                    break
        room = self.rooms[code] = Room(code, map_id)
        return room

    def get(self, code: str) -> Room:
        room = self.rooms.get(code.upper())
        if room is None:
            raise RuleError("room not found")
        return room
