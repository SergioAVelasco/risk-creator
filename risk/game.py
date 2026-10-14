"""Classic Risk rules as a state machine.

Phases: setup -> (reinforce -> attack [-> conquer_move] -> fortify)* -> finished.
Every action takes the acting player's id and raises RuleError if illegal.
"""
from __future__ import annotations

import random
from collections import deque

from .cards import Card, build_deck, is_valid_set, set_value
from .errors import RuleError
from .gamemap import GameMap

STARTING_ARMIES = {2: 40, 3: 35, 4: 30, 5: 25, 6: 20}
NEUTRAL = "neutral"  # id del ejército fantasma de las partidas de 2 jugadores
MUST_TRADE_AT = 5  # holding this many cards forces a trade at reinforcement


class Game:
    def __init__(self, gamemap: GameMap, players: list[str], rng: random.Random | None = None,
                 setup_batch: int = 1, simultaneous_setup: bool = False):
        if not 2 <= len(players) <= 6:
            raise RuleError("Risk needs 2 to 6 players")
        if len(set(players)) != len(players):
            raise RuleError("player ids must be unique")
        if len(gamemap.territories) < len(players):
            raise RuleError("map has fewer territories than players")
        if setup_batch < 0:
            raise RuleError("setup_batch cannot be negative")
        # Ejércitos que cada jugador coloca seguidos en su turno de preparación
        # (1 = reglas clásicas). 0 significa "todos de una vez".
        self.setup_batch = setup_batch or 10**9
        self.setup_turn_left = 0      # cuota restante del turno de setup actual
        # Preparación simultánea y oculta: todos colocan a la vez, por rondas de
        # `setup_batch` ejércitos, sin ver lo que colocan los demás. Se revela
        # todo al terminar la fase. (Si es False: turnos clásicos uno a uno.)
        self.simultaneous_setup = simultaneous_setup
        self.setup_round = 0
        self.setup_quota: dict[str, int] = {}     # ejércitos a colocar esta ronda
        self.setup_pending: dict[str, dict[str, int]] = {}  # colocados, sin confirmar
        self.setup_ready: dict[str, bool] = {}    # ya confirmó esta ronda
        self.map = gamemap
        self.players = list(players)  # solo quienes juegan turnos
        # Con 2 jugadores entra un tercer bando neutral (regla oficial): ocupa
        # territorios y defiende con dados, pero nunca juega turno, no refuerza
        # y no roba cartas. Por eso NO está en self.players ni en self.cards.
        self.neutral: str | None = NEUTRAL if len(players) == 2 else None
        self.rng = rng or random.Random()

        self.owner: dict[str, str] = {}
        self.armies: dict[str, int] = {}
        self.cards: dict[str, list[Card]] = {p: [] for p in players}
        self.eliminated: list[str] = []
        self.winner: str | None = None

        self.deck: list[Card] = []
        self.discard: list[Card] = []
        self.sets_traded = 0

        self.current: str = players[0]
        self.phase = "setup"
        self.reinforcements = 0       # armies the current player still has to place
        self.setup_left: dict[str, int] = {}
        self.conquered_this_turn = False
        self.pending_conquest: dict | None = None  # {"from","to","min","max"}
        self.log: list[dict] = []

        self._deal()

    # ------------------------------------------------------------ setup
    def _deal(self) -> None:
        ids = list(self.map.territories)
        self.rng.shuffle(ids)
        # El reparto incluye al neutral como un bando más (14 territorios c/u).
        sides = self.players + ([self.neutral] if self.neutral else [])
        for i, t in enumerate(ids):
            self.owner[t] = sides[i % len(sides)]
            self.armies[t] = 1
        start = STARTING_ARMIES.get(len(self.players), 20)
        for p in self.players:
            self.setup_left[p] = start - len(self.territories_of(p))
        if self.neutral:
            # El neutral recibe el mismo total que un jugador, ya colocado de
            # forma aleatoria entre sus territorios (nadie lo coloca por turnos).
            mine = self.territories_of(self.neutral)
            for _ in range(start - len(mine)):
                self.armies[self.rng.choice(mine)] += 1
        self.deck = build_deck(list(self.map.territories))
        self.rng.shuffle(self.deck)
        if self.simultaneous_setup:
            self.current = self.players[0]
            self._begin_setup_round()
            return
        self.current = self.players[-1]  # so the first placement goes to players[0]
        self._advance_setup()

    # -- preparación simultánea
    def _begin_setup_round(self) -> None:
        """Abre una ronda: cada jugador recibe su cuota, o termina la fase."""
        if not any(v > 0 for v in self.setup_left.values()):
            self._finish_setup()
            return
        self.setup_round += 1
        for p in self.players:
            q = max(0, min(self.setup_batch, self.setup_left[p]))
            self.setup_quota[p] = q
            self.setup_pending[p] = {}
            self.setup_ready[p] = q == 0

    def _finish_setup(self) -> None:
        self.setup_quota = {p: 0 for p in self.players}
        self.setup_pending = {p: {} for p in self.players}
        self.setup_ready = {p: True for p in self.players}
        self.setup_turn_left = 0
        self.phase = "reinforce"
        self.current = self.players[0]
        self.log.append({"event": "setup_done"})
        self._start_turn()

    def _require_setup_open(self, player: str) -> None:
        if self.phase != "setup" or not self.simultaneous_setup:
            raise RuleError("not allowed during the " + self.phase + " phase")
        if player not in self.players:
            raise RuleError("unknown player")
        if self.setup_ready[player]:
            raise RuleError("you already placed this round's armies; wait for the others")

    def undo_setup(self, player: str) -> None:
        """Retira lo colocado esta ronda (solo posible antes de completar la cuota)."""
        self._require_setup_open(player)
        self.setup_pending[player] = {}

    def _complete_setup_round_for(self, player: str) -> None:
        """Se llama solo cuando el jugador colocó toda su cuota: queda en espera.

        No hay botón de confirmar: al colocar el último ejército de la cuota el
        jugador pasa a "listo". Cuando todos están listos se aplican las
        colocaciones y se abre la siguiente ronda.
        """
        self.setup_ready[player] = True
        if all(self.setup_ready.values()):
            for p in self.players:
                for t, n in self.setup_pending[p].items():
                    self.armies[t] += n
                self.setup_left[p] -= self.setup_quota[p]
            self._begin_setup_round()

    def _advance_setup(self) -> None:
        """Move to the next player with armies left to place, or start play."""
        if not any(self.setup_left.values()):
            self.phase = "reinforce"
            self.current = self.players[0]
            self.setup_turn_left = 0
            self._start_turn()
            return
        i = self.players.index(self.current)
        for k in range(1, len(self.players) + 1):
            p = self.players[(i + k) % len(self.players)]
            if self.setup_left[p] > 0:
                self.current = p
                # El turno dura hasta colocar `setup_batch` ejércitos (o los
                # que le queden, si son menos).
                self.setup_turn_left = min(self.setup_batch, self.setup_left[p])
                return

    # ------------------------------------------------------------ queries
    def territories_of(self, player: str) -> list[str]:
        return [t for t, o in self.owner.items() if o == player]

    def alive_players(self) -> list[str]:
        return [p for p in self.players if p not in self.eliminated]

    def reinforcement_income(self, player: str) -> int:
        owned = set(self.territories_of(player))
        n = max(3, len(owned) // 3)
        for c in self.map.continents.values():
            if c.territories <= owned:
                n += c.bonus
        return n

    def _require_turn(self, player: str, *phases: str) -> None:
        if self.phase == "finished":
            raise RuleError("the game is over")
        if player != self.current:
            raise RuleError(f"it is {self.current}'s turn")
        if self.phase not in phases:
            raise RuleError(f"not allowed during the {self.phase} phase")

    def _require_own(self, player: str, territory: str) -> None:
        if territory not in self.owner:
            raise RuleError(f"unknown territory {territory!r}")
        if self.owner[territory] != player:
            raise RuleError(f"{territory} is not yours")

    # ------------------------------------------------------------ placing armies
    def place(self, player: str, territory: str, count: int = 1) -> None:
        """Place armies: initial setup (one at a time) or reinforcement."""
        if self.phase == "setup" and self.simultaneous_setup:
            # Simultánea: no hay turno. Lo colocado queda pendiente (y privado)
            # hasta que todos confirmen la ronda.
            self._require_setup_open(player)
            self._require_own(player, territory)
            if count < 1:
                raise RuleError("must place at least one army")
            left = self.setup_quota[player] - sum(self.setup_pending[player].values())
            if count > left:
                raise RuleError(f"you can place {left} more armies this round")
            pend = self.setup_pending[player]
            pend[territory] = pend.get(territory, 0) + count
            if sum(pend.values()) == self.setup_quota[player]:
                self._complete_setup_round_for(player)
            return
        self._require_turn(player, "setup", "reinforce", "attack")
        self._require_own(player, territory)
        if count < 1:
            raise RuleError("must place at least one army")

        if self.phase == "setup":
            if count > self.setup_turn_left:
                raise RuleError(f"you can place {self.setup_turn_left} more armies this turn")
            self.armies[territory] += count
            self.setup_left[player] -= count
            self.setup_turn_left -= count
            # El turno pasa al siguiente jugador solo al completar la cuota.
            if self.setup_turn_left == 0:
                self._advance_setup()
            return

        if self.phase == "reinforce" and len(self.cards[player]) >= MUST_TRADE_AT:
            raise RuleError("you must trade in cards first")
        if count > self.reinforcements:
            raise RuleError(f"only {self.reinforcements} armies left to place")
        self.armies[territory] += count
        self.reinforcements -= count
        if self.phase == "reinforce" and self.reinforcements == 0:
            self.phase = "attack"

    def trade_cards(self, player: str, indices: list[int]) -> int:
        """Trade three cards (by index into the hand) for armies; returns armies gained."""
        self._require_turn(player, "reinforce", "attack")
        hand = self.cards[player]
        if self.phase == "attack" and len(hand) <= MUST_TRADE_AT:
            raise RuleError("cards can only be traded now when holding more than 5")
        if len(set(indices)) != 3 or any(not 0 <= i < len(hand) for i in indices):
            raise RuleError("choose three different cards from your hand")
        chosen = [hand[i] for i in indices]
        if not is_valid_set(chosen):
            raise RuleError("those cards do not form a set")

        gained = set_value(self.sets_traded)
        self.sets_traded += 1
        for i in sorted(indices, reverse=True):
            self.discard.append(hand.pop(i))
        # official rule: +2 armies on one owned territory shown on a traded card
        for c in chosen:
            if c.territory and self.owner[c.territory] == player:
                self.armies[c.territory] += 2
                break
        self.reinforcements += gained
        self.log.append({"event": "trade", "player": player, "armies": gained})
        return gained

    # ------------------------------------------------------------ attacking
    def attack(self, player: str, source: str, target: str, dice: int) -> dict:
        self._require_turn(player, "attack")
        if self.reinforcements > 0:
            raise RuleError("place your armies from the trade first")
        if len(self.cards[player]) > MUST_TRADE_AT:
            raise RuleError("you must trade in cards first")
        self._require_own(player, source)
        if target not in self.owner:
            raise RuleError(f"unknown territory {target!r}")
        if self.owner[target] == player:
            raise RuleError("you cannot attack your own territory")
        # Regla de adyacencia: solo se ataca a un territorio vecino directo
        # (comparten frontera según el mapa). No hay ataques a distancia.
        if target not in self.map.territories[source].neighbors:
            raise RuleError(f"{source} is not adjacent to {target}")
        # Siempre debe quedar al menos 1 ejército defendiendo el territorio de
        # origen, por eso solo se pueden usar (ejércitos - 1) para atacar, con
        # un máximo de 3 dados.
        max_dice = min(3, self.armies[source] - 1)
        if max_dice < 1:
            raise RuleError("you need at least 2 armies to attack")
        if not 1 <= dice <= max_dice:
            raise RuleError(f"you can attack with 1 to {max_dice} dice")

        defender = self.owner[target]
        d_dice = min(2, self.armies[target])  # el defensor tira 1 o 2 dados
        # Se ordenan de mayor a menor y se comparan por parejas: el mejor dado
        # del atacante contra el mejor del defensor, luego el segundo contra el
        # segundo. zip se detiene en la lista más corta, así que sobran dados
        # sin comparar cuando uno de los dos tiró más.
        a_rolls = sorted((self.rng.randint(1, 6) for _ in range(dice)), reverse=True)
        d_rolls = sorted((self.rng.randint(1, 6) for _ in range(d_dice)), reverse=True)
        a_lost = d_lost = 0
        for a, d in zip(a_rolls, d_rolls):
            if a > d:
                d_lost += 1
            else:  # ties favour the defender
                a_lost += 1
        self.armies[source] -= a_lost
        self.armies[target] -= d_lost

        result = {
            "attacker": player, "defender": defender, "from": source, "to": target,
            "attacker_rolls": a_rolls, "defender_rolls": d_rolls,
            "attacker_lost": a_lost, "defender_lost": d_lost,
            "conquered": False, "eliminated": None,
        }
        if self.armies[target] == 0:
            self._conquer(player, defender, source, target, dice, result)
        self.log.append({"event": "attack", **result})
        return result

    def _conquer(self, player, defender, source, target, dice, result) -> None:
        self.owner[target] = player
        self.conquered_this_turn = True
        result["conquered"] = True
        # Al conquistar hay que ocupar el territorio: mínimo tantos ejércitos
        # como dados usados (o los que haya disponibles) y máximo todos menos
        # uno, porque el territorio de origen nunca puede quedar vacío.
        max_move = self.armies[source] - 1
        self.pending_conquest = {
            "from": source, "to": target, "min": min(dice, max_move), "max": max_move,
        }
        if not self.territories_of(defender):
            # El neutral puede quedarse sin territorios, pero no tiene cartas
            # que heredar ni cuenta como jugador eliminado.
            if defender != self.neutral:
                self.eliminated.append(defender)
                self.cards[player].extend(self.cards[defender])
                self.cards[defender] = []
            result["eliminated"] = defender
        if len(self.territories_of(player)) == len(self.map.territories):
            self.winner = player
            self.phase = "finished"
            # no choice left to make: occupy the last territory with the dice used
            moved = self.pending_conquest["min"]
            self.armies[source] -= moved
            self.armies[target] = moved
            self.pending_conquest = None
            return
        self.phase = "conquer_move"

    def move_in(self, player: str, count: int) -> None:
        """After a conquest, move armies from the attacking territory into the new one."""
        self._require_turn(player, "conquer_move")
        pc = self.pending_conquest
        if not pc["min"] <= count <= pc["max"]:
            raise RuleError(f"move between {pc['min']} and {pc['max']} armies")
        self.armies[pc["from"]] -= count
        self.armies[pc["to"]] = count
        self.pending_conquest = None
        self.phase = "attack"

    def end_attack(self, player: str) -> None:
        self._require_turn(player, "attack")
        if self.reinforcements > 0:
            raise RuleError("place your armies from the trade first")
        if len(self.cards[player]) > MUST_TRADE_AT:
            raise RuleError("you must trade in cards first")
        self.phase = "fortify"

    # ------------------------------------------------------------ fortify / end turn
    def fortify(self, player: str, source: str, target: str, count: int) -> None:
        """Move armies along a path of your own territories, then the turn ends.

        A diferencia del ataque, el destino NO tiene que ser vecino directo del
        origen: basta con que exista un camino de territorios propios que los
        una (ver _connected). Solo se permite un movimiento por turno.
        """
        self._require_turn(player, "fortify")
        self._require_own(player, source)
        self._require_own(player, target)
        if source == target:
            raise RuleError("choose two different territories")
        # Igual que al atacar: en el origen debe quedar al menos 1 ejército.
        if not 1 <= count <= self.armies[source] - 1:
            raise RuleError(f"you can move 1 to {self.armies[source] - 1} armies")
        if not self._connected(player, source, target):
            raise RuleError("no path of your own territories connects them")
        self.armies[source] -= count
        self.armies[target] += count
        self.end_turn(player)

    def _connected(self, player: str, a: str, b: str) -> bool:
        """¿Se puede llegar de `a` a `b` pasando solo por territorios de `player`?

        Búsqueda en anchura (BFS) sobre el grafo del mapa: se parte de `a`, se
        visitan sus vecinos propios, luego los vecinos de esos, y así hasta
        encontrar `b` o quedarse sin territorios por explorar. Un territorio
        enemigo en medio corta el camino porque nunca se mete en la cola.
        """
        seen, queue = {a}, deque([a])
        while queue:
            t = queue.popleft()
            if t == b:
                return True
            for n in self.map.territories[t].neighbors:
                # `seen` evita volver sobre lo ya explorado (y bucles infinitos).
                if n not in seen and self.owner[n] == player:
                    seen.add(n)
                    queue.append(n)
        return False

    def end_turn(self, player: str) -> None:
        self._require_turn(player, "attack", "fortify")
        if self.phase == "attack" and (
            self.reinforcements > 0 or len(self.cards[player]) > MUST_TRADE_AT
        ):
            raise RuleError("finish your trade and placement first")
        if self.conquered_this_turn:
            self._draw_card(player)
        order = self.players
        i = order.index(player)
        for k in range(1, len(order) + 1):
            nxt = order[(i + k) % len(order)]
            if nxt not in self.eliminated:
                self.current = nxt
                break
        self._start_turn()

    def _start_turn(self) -> None:
        self.phase = "reinforce"
        self.conquered_this_turn = False
        self.pending_conquest = None
        self.reinforcements = self.reinforcement_income(self.current)

    def _draw_card(self, player: str) -> None:
        if not self.deck and self.discard:
            self.deck, self.discard = self.discard, []
            self.rng.shuffle(self.deck)
        if self.deck:
            self.cards[player].append(self.deck.pop())

    # ------------------------------------------------------------ serialization
    def to_dict(self, viewer: str | None = None) -> dict:
        """Snapshot for clients. Other players' cards are reduced to a count."""
        hide = self.phase == "setup" and self.simultaneous_setup
        armies = self.armies
        if hide:
            # Cada uno ve el reparto inicial y SU propia colocación; lo que
            # colocan los demás (confirmado o no) queda oculto hasta el final.
            mine = self.setup_pending.get(viewer, {})
            armies = {
                t: (self.armies[t] + mine.get(t, 0)
                    if self.owner[t] in (viewer, self.neutral) else 1)
                for t in self.armies
            }
        return {
            "map": self.map.name,
            "players": self.players,
            "setup_simultaneous": self.simultaneous_setup,
            "setup_round": self.setup_round,
            "setup_quota": self.setup_quota,
            "setup_ready": self.setup_ready,
            "setup_placed": sum(self.setup_pending.get(viewer, {}).values()),
            "neutral": self.neutral,
            "eliminated": self.eliminated,
            "winner": self.winner,
            "phase": self.phase,
            "current": self.current,
            "reinforcements": self.reinforcements,
            "setup_left": self.setup_left,
            "setup_turn_left": self.setup_turn_left,
            "setup_batch": self.setup_batch if self.setup_batch < 10**9 else 0,
            "owner": self.owner,
            "armies": armies,
            "pending_conquest": self.pending_conquest,
            "card_counts": {p: len(c) for p, c in self.cards.items()},
            "cards": [
                {"symbol": c.symbol, "territory": c.territory}
                for c in self.cards.get(viewer, [])
            ],
            "sets_traded": self.sets_traded,
            "next_set_value": set_value(self.sets_traded),
        }
