"""Rondas simultáneas: la variante digital de las reglas.

Todos los jugadores actúan a la vez; no hay turnos. Una ronda tiene tres fases:

  plan     refuerzos y declaración de ataques (en secreto) hasta que todos
           pulsan "listo" o se acaba el tiempo;
  battle   todas las batallas declaradas se resuelven a la vez, una tirada
           tras otra (tick). Entre tick y tick cada jugador puede decidir en
           qué batallas retirarse;
  fortify  un movimiento por jugador, también simultáneo.

Un ataque declarado saca `n` ejércitos del territorio de origen ("ejército
comprometido"). Si dos territorios se atacan entre sí, sus ejércitos libran una
batalla de campo abierto (ambos tiran como atacantes y el empate cuesta una baja
a cada lado). Al terminar, el ganador sigue contra el territorio del perdedor
con las reglas normales (batalla de ciudad). Un territorio atacado por varios
frentes reparte sus ejércitos entre las batallas.

Este módulo es un mixin de `Game`: comparte su estado (owner, armies, cards...).
"""
from __future__ import annotations

from .errors import RuleError

MUST_TRADE_AT = 5  # igual que en game.py (no se importa para evitar el ciclo)


class RoundsMixin:
    # ------------------------------------------------------------ estado
    def _init_rounds(self) -> None:
        self.round = 0
        self.reinf: dict[str, int] = {p: 0 for p in self.players}
        self.plan_added: dict[str, dict[str, int]] = {p: {} for p in self.players}
        self.declared: dict[str, list[dict]] = {p: [] for p in self.players}
        self.ready: dict[str, bool] = {p: False for p in self.players}
        self.battles: list[dict] = []
        self.next_battle_id = 1
        self.retreat_votes: set[tuple[int, str]] = set()
        self.battle_go: set[str] = set()
        self.last_tick: list[dict] = []
        self.round_conquerors: set[str] = set()
        self.last_taker: dict[str, str] = {}
        self.serial = 0  # sube con cada cambio de fase o tick (para el temporizador)

    def _bump(self) -> None:
        self.serial += 1

    def _alive_humans(self) -> list[str]:
        return [p for p in self.players if p not in self.eliminated]

    # ------------------------------------------------------------ planificación
    def _begin_round(self) -> None:
        self.round += 1
        self.phase = "plan"
        for p in self.players:
            dead = p in self.eliminated
            if not dead:
                self.reinf[p] += self.reinforcement_income(p)  # lo no colocado se acumula
            self.plan_added[p] = {}
            self.declared[p] = []
            self.ready[p] = dead
        self.round_conquerors = set()
        self.last_taker = {}
        self.last_tick = []
        self._bump()

    def _require_plan(self, player: str) -> None:
        if self.phase == "finished":
            raise RuleError("the game is over")
        if self.phase != "plan":
            raise RuleError(f"not allowed during the {self.phase} phase")
        if player not in self.players or player in self.eliminated:
            raise RuleError("you are not playing")
        if self.ready[player]:
            raise RuleError("you are ready; cancel it first to change your plan")

    def _hide_added(self, player: str, territory: str, n: int) -> None:
        pend = self.plan_added[player]
        pend[territory] = pend.get(territory, 0) + n

    def _plan_place(self, player: str, territory: str, count: int) -> None:
        self._require_plan(player)
        self._require_own(player, territory)
        if count < 1:
            raise RuleError("must place at least one army")
        if len(self.cards[player]) >= MUST_TRADE_AT:
            raise RuleError("you must trade in cards first")
        if count > self.reinf[player]:
            raise RuleError(f"only {self.reinf[player]} armies left to place")
        self.armies[territory] += count
        self.reinf[player] -= count
        self._hide_added(player, territory, count)

    def _committed_from(self, player: str, source: str, skip: str | None = None) -> int:
        return sum(d["n"] for d in self.declared[player]
                   if d["from"] == source and d["to"] != skip)

    def declare_attack(self, player: str, source: str, target: str, n: int) -> None:
        """Declara (o cambia) un ataque de `n` ejércitos de `source` a `target`."""
        self._require_plan(player)
        if self.reinf[player] > 0:
            raise RuleError("place your reinforcements before declaring attacks")
        if len(self.cards[player]) > MUST_TRADE_AT:
            raise RuleError("you must trade in cards first")
        self._require_own(player, source)
        if target not in self.owner:
            raise RuleError(f"unknown territory {target!r}")
        if self.owner[target] == player:
            raise RuleError("you cannot attack your own territory")
        if target not in self.map.territories[source].neighbors:
            raise RuleError(f"{source} is not adjacent to {target}")
        free = self.armies[source] - 1 - self._committed_from(player, source, skip=target)
        if free < 1:
            raise RuleError("you need at least 2 armies to attack")
        if not 1 <= n <= free:
            raise RuleError(f"you can commit 1 to {free} armies from {source}")
        for d in self.declared[player]:
            if d["from"] == source and d["to"] == target:
                d["n"] = n
                return
        self.declared[player].append({"from": source, "to": target, "n": n})

    def cancel_attack(self, player: str, source: str, target: str) -> None:
        self._require_plan(player)
        before = len(self.declared[player])
        self.declared[player] = [d for d in self.declared[player]
                                 if not (d["from"] == source and d["to"] == target)]
        if len(self.declared[player]) == before:
            raise RuleError("no such attack declared")

    def set_ready(self, player: str, value: bool = True) -> None:
        """'Terminar turno': marca al jugador como listo en plan o fortificar."""
        if self.phase == "finished":
            raise RuleError("the game is over")
        if self.phase not in ("plan", "fortify"):
            raise RuleError(f"not allowed during the {self.phase} phase")
        if player not in self.players or player in self.eliminated:
            raise RuleError("you are not playing")
        if value and self.phase == "plan" and len(self.cards[player]) > MUST_TRADE_AT:
            raise RuleError("you must trade in cards first")
        self.ready[player] = value
        if value and all(self.ready.values()):
            self._phase_complete()

    def _phase_complete(self) -> None:
        if self.phase == "plan":
            self._start_battles()
        elif self.phase == "fortify":
            self._begin_round()

    def force_advance(self) -> None:
        """Se acabó el tiempo: avanza lo que esté pendiente (lo llama el servidor)."""
        if self.phase in ("plan", "fortify"):
            for p in self.players:
                self.ready[p] = True
            self._phase_complete()
        elif self.phase == "battle":
            self._tick()

    # ------------------------------------------------------------ batallas
    def _start_battles(self) -> None:
        self.phase = "battle"
        self.battles = []
        self.retreat_votes = set()
        self.battle_go = set()
        self.last_tick = []
        pending = [(p, d) for p in self.players for d in self.declared[p]]
        by_pair = {(d["from"], d["to"]): (p, d) for p, d in pending}
        used: set[tuple[str, str]] = set()
        for p, d in pending:
            key = (d["from"], d["to"])
            if key in used:
                continue
            self.armies[d["from"]] -= d["n"]  # el ejército sale a combatir
            side = {"owner": p, "src": d["from"], "dst": d["to"], "n": d["n"]}
            mirror = by_pair.get((d["to"], d["from"]))
            battle = {"id": self.next_battle_id, "kind": "city", "sides": [side]}
            self.next_battle_id += 1
            if mirror and mirror[0] != p:
                q, e = mirror
                self.armies[e["from"]] -= e["n"]
                battle["kind"] = "field"
                battle["sides"].append(
                    {"owner": q, "src": e["from"], "dst": e["to"], "n": e["n"]})
                used.add((e["from"], e["to"]))
            used.add(key)
            self.battles.append(battle)
            self.log.append({"event": "battle", "id": battle["id"], "kind": battle["kind"],
                             "sides": [dict(s) for s in battle["sides"]]})
        self.declared = {p: [] for p in self.players}
        self.plan_added = {p: {} for p in self.players}
        self._bump()
        if not self.battles:
            self._end_battles()

    def _can_retreat(self, side: dict) -> bool:
        # Sin territorio al que volver no hay retirada.
        return self.owner[side["src"]] == side["owner"]

    def battle_participants(self) -> set[str]:
        return {s["owner"] for b in self.battles for s in b["sides"]}

    def _find_battle(self, bid: int) -> dict:
        for b in self.battles:
            if b["id"] == bid:
                return b
        raise RuleError("no such battle")

    def set_retreat(self, player: str, bid: int, retreat: bool = True) -> None:
        """Decide (para el próximo tick) si te retiras de la batalla `bid`."""
        if self.phase != "battle":
            raise RuleError("there are no battles right now")
        b = self._find_battle(bid)
        side = next((s for s in b["sides"] if s["owner"] == player), None)
        if side is None:
            raise RuleError("you are not fighting in that battle")
        if retreat and not self._can_retreat(side):
            raise RuleError("your home territory was taken: you cannot retreat")
        (self.retreat_votes.add if retreat else self.retreat_votes.discard)((bid, player))

    def battle_continue(self, player: str) -> None:
        """Confirma tu decisión del tick; si todos confirmaron, se tira ya."""
        if self.phase != "battle":
            raise RuleError("there are no battles right now")
        if player not in self.battle_participants():
            raise RuleError("you are not fighting")
        self.battle_go.add(player)
        if self.battle_participants() <= self.battle_go:
            self._tick()

    @staticmethod
    def _compare(att: list[int], dfn: list[int], tie: str) -> tuple[int, int]:
        """Compara dados por parejas, de mayor a menor. -> (bajas atac., bajas def.)

        tie="defender": gana el defensor (ciudad). tie="both": ambos pierden uno
        (campo abierto, los dos tiran como atacantes).
        """
        a_lost = d_lost = 0
        for a, d in zip(att, dfn):
            if a > d:
                d_lost += 1
            elif a < d:
                a_lost += 1
            elif tie == "both":
                a_lost += 1
                d_lost += 1
            else:
                a_lost += 1
        return a_lost, d_lost

    def _roll(self, n: int) -> list[int]:
        return sorted((self.rng.randint(1, 6) for _ in range(n)), reverse=True)

    def _merge_own(self) -> None:
        """Un ejército cuyo destino ya es de su dueño se une a esa guarnición."""
        keep = []
        for b in self.battles:
            if b["kind"] == "city" and self.owner[b["sides"][0]["dst"]] == b["sides"][0]["owner"]:
                s = b["sides"][0]
                self.armies[s["dst"]] += s["n"]
            else:
                keep.append(b)
        self.battles = keep

    def _tick(self) -> None:
        """Una tirada simultánea de todas las batallas activas."""
        self._merge_own()
        # Reparto de la guarnición entre los frentes que la atacan.
        fronts: dict[str, list[dict]] = {}
        for b in self.battles:
            if b["kind"] == "city":
                fronts.setdefault(b["sides"][0]["dst"], []).append(b)
        share: dict[int, int] = {}
        for dst, bs in fronts.items():
            g, k = self.armies[dst], len(bs)
            for i, b in enumerate(bs):
                share[b["id"]] = g // k + (1 if i < g % k else 0)

        retreating = {(b["id"], s["owner"]) for b in self.battles for s in b["sides"]
                      if (b["id"], s["owner"]) in self.retreat_votes and self._can_retreat(s)}
        results, garrison_loss = [], {}
        for b in self.battles:
            res = {"id": b["id"], "kind": b["kind"], "rolls": [], "lost": [],
                   "retreat": [s["owner"] for s in b["sides"] if (b["id"], s["owner"]) in retreating]}
            if b["kind"] == "field":
                s1, s2 = b["sides"]
                r1, r2 = (b["id"], s1["owner"]) in retreating, (b["id"], s2["owner"]) in retreating
                l1 = l2 = 0
                if not (r1 and r2):  # si ambos se retiran no hay tiro de retirada
                    d1, d2 = self._roll(min(3, s1["n"])), self._roll(min(3, s2["n"]))
                    l1, l2 = self._compare(d1, d2, "both")
                    if r1:
                        l2 = 0  # quien se retira no elimina unidades del rival
                    if r2:
                        l1 = 0
                    res["rolls"] = [d1, d2]
                s1["n"] -= l1
                s2["n"] -= l2
                res["lost"] = [l1, l2]
            else:
                s = b["sides"][0]
                dst = s["dst"]
                a = self._roll(min(3, s["n"]))
                d = self._roll(min(2, max(1, share[b["id"]])))
                a_lost, d_lost = self._compare(a, d, "defender")
                if (b["id"], s["owner"]) in retreating:
                    d_lost = 0
                s["n"] -= a_lost
                garrison_loss[dst] = garrison_loss.get(dst, 0) + d_lost
                res["rolls"] = [a, d]
                res["lost"] = [a_lost, d_lost]
                res["defender"] = self.owner[dst]
            results.append(res)
        for dst, n in garrison_loss.items():
            self.armies[dst] = max(0, self.armies[dst] - n)

        survivors = []
        for b in self.battles:
            for s in b["sides"]:
                if (b["id"], s["owner"]) in retreating and s["n"] > 0:
                    self.armies[s["src"]] += s["n"]  # vuelve a casa
                    self.log.append({"event": "retreat", "player": s["owner"],
                                     "to": s["src"], "armies": s["n"]})
                    s["n"] = 0
            live = [s for s in b["sides"] if s["n"] > 0]
            if b["kind"] == "field" and len(live) == 1:
                b["kind"], b["sides"] = "city", live  # el ganador sigue contra la ciudad
                survivors.append(b)
            elif b["kind"] == "field" and len(live) == 2:
                survivors.append(b)
            elif b["kind"] == "city" and live:
                survivors.append(b)
        self.battles = survivors
        self._resolve_conquests(fronts)
        self._merge_own()

        self.last_tick = results
        self.retreat_votes, self.battle_go = set(), set()
        if self.battles:
            self._bump()
        else:
            self._end_battles()

    def _resolve_conquests(self, fronts: dict[str, list[dict]]) -> None:
        for dst, group in fronts.items():
            if self.armies[dst] > 0:
                continue
            cands = [b for b in group if b in self.battles]
            if not cands:
                self.armies[dst] = 1  # cayeron todos a la vez: la guarnición resiste
                continue
            win = max(cands, key=lambda b: (b["sides"][0]["n"], -b["id"]))
            w = win["sides"][0]
            prev = self.owner[dst]
            self.owner[dst] = w["owner"]
            self.armies[dst] = w["n"]
            self.battles.remove(win)
            self.round_conquerors.add(w["owner"])
            self.last_taker[prev] = w["owner"]
            self.log.append({"event": "conquest", "player": w["owner"], "from": w["src"],
                             "to": dst, "previous": prev, "armies": w["n"]})

    def _end_battles(self) -> None:
        """Fin de todas las batallas: eliminaciones, cartas y comprobar victoria."""
        for p in self._alive_humans():
            if not self.territories_of(p):
                self.eliminated.append(p)
                taker = self.last_taker.get(p)
                if taker and taker in self.cards:
                    self.cards[taker].extend(self.cards[p])
                    self.cards[p] = []
                self.log.append({"event": "eliminated", "player": p, "by": taker})
        for p in sorted(self.round_conquerors):
            if p not in self.eliminated:
                self._draw_card(p)
        self.battles, self.retreat_votes, self.battle_go = [], set(), set()
        alive = self._alive_humans()
        owners = set(self.owner.values())
        if len(alive) == 1 or len(owners) == 1:
            self.winner = alive[0] if len(alive) == 1 else next(iter(owners))
            self.phase = "finished"
        else:
            self.phase = "fortify"
            self.ready = {p: p in self.eliminated for p in self.players}
        self._bump()

    # ------------------------------------------------------------ fortificar
    def _round_fortify(self, player: str, source: str, target: str, count: int) -> None:
        if self.phase != "fortify":
            raise RuleError(f"not allowed during the {self.phase} phase")
        if player not in self.players or player in self.eliminated:
            raise RuleError("you are not playing")
        if self.ready[player]:
            raise RuleError("you already finished this phase")
        self._require_own(player, source)
        self._require_own(player, target)
        if source == target:
            raise RuleError("choose two different territories")
        if not 1 <= count <= self.armies[source] - 1:
            raise RuleError(f"you can move 1 to {self.armies[source] - 1} armies")
        if not self._connected(player, source, target):
            raise RuleError("no path of your own territories connects them")
        self.armies[source] -= count
        self.armies[target] += count
        self.set_ready(player)

    # ------------------------------------------------------------ vista
    def _plan_armies_for(self, viewer: str | None) -> dict[str, int]:
        """Durante la planificación nadie ve los refuerzos de los demás."""
        out = dict(self.armies)
        for p, added in self.plan_added.items():
            if p != viewer:
                for t, n in added.items():
                    out[t] -= n
        return out

    def _battle_view(self, b: dict, viewer: str | None) -> dict:
        return {
            "id": b["id"], "kind": b["kind"],
            "sides": [{**s, "can_retreat": self._can_retreat(s),
                       "retreating": (b["id"], s["owner"]) in self.retreat_votes
                       if s["owner"] == viewer else None}
                      for s in b["sides"]],
        }

    def _rounds_dict(self, viewer: str | None) -> dict:
        if not self.simultaneous_play:
            return {"simultaneous_play": False}
        return {
            "simultaneous_play": True,
            "round": self.round,
            "current": None,
            "reinforcements": self.reinf.get(viewer, 0),
            "ready": self.ready,
            "declared": list(self.declared.get(viewer, [])) if self.phase == "plan" else [],
            "battles": [self._battle_view(b, viewer) for b in self.battles],
            "battle_go": sorted(self.battle_go),
            "last_tick": self.last_tick,
            "serial": self.serial,
        }
