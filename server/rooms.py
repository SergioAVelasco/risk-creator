"""Room/lobby logic, independent of the web framework (testable without sockets)."""
from __future__ import annotations

import random
import secrets
import string
from dataclasses import dataclass, field
from pathlib import Path

from risk import Game, GameMap, RuleError

ROOT = Path(__file__).resolve().parent.parent
MAPS_DIR = ROOT / "maps"
COLORS = ["#e6194b", "#3b6fd4", "#2fa84f", "#f2b705", "#9b59b6", "#17b6b6"]
MAX_PLAYERS = 6


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

    # -- membership
    def join(self, name: str, token: str | None) -> Member:
        if token:
            for m in self.members.values():
                if m.token == token:
                    return m
        if self.game is not None:
            raise RuleError("the game already started")
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

    def start(self, pid: str) -> None:
        if pid != self.host:
            raise RuleError("only the host can start the game")
        if self.game is not None:
            raise RuleError("the game already started")
        if len(self.members) < 2:
            raise RuleError("you need at least 2 players")
        gm = GameMap.load(MAPS_DIR / self.map_id / "map.json")
        self.game = Game(gm, list(self.members))

    # -- actions
    def act(self, pid: str, msg: dict) -> None:
        g = self.game
        if g is None:
            raise RuleError("the game has not started")
        t = msg.get("type")
        i = lambda k: int(msg.get(k, 0))  # noqa: E731
        if t == "place":
            g.place(pid, msg["territory"], i("count") or 1)
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
            "members": [{"pid": m.pid, "name": m.name, "color": m.color, "connected": m.connected}
                        for m in self.members.values()],
        }

    def view_for(self, pid: str) -> dict:
        v = {"lobby": self.lobby_view(), "you": pid, "game": None, "log": []}
        if self.game:
            v["game"] = self.game.to_dict(viewer=pid)
            v["log"] = self.game.log[-40:]
        return v


class Rooms:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    def create(self, map_id: str) -> Room:
        if map_id not in {m["id"] for m in list_maps()}:
            raise RuleError("unknown map")
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
