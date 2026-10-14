import random
import unittest
from pathlib import Path

from risk import Game, GameMap, MapError, RuleError
from risk.cards import Card, is_valid_set, set_value

CLASSIC = Path(__file__).parent.parent / "maps" / "classic" / "map.json"


class ScriptedRng(random.Random):
    """Returns queued dice values first, then falls back to real randomness."""
    def __init__(self, seed=1):
        super().__init__(seed)
        self.queue = []

    def randint(self, a, b):
        return self.queue.pop(0) if self.queue else super().randint(a, b)


def new_game(n=3, seed=1):
    return Game(GameMap.load(CLASSIC), [f"p{i}" for i in range(n)], ScriptedRng(seed))


def to_attack_phase(g):
    """Finish setup and reinforcement placement, leaving current player in attack."""
    while g.phase == "setup":
        g.place(g.current, g.territories_of(g.current)[0])
    assert g.phase == "reinforce"
    p = g.current
    g.place(p, g.territories_of(p)[0], g.reinforcements)
    assert g.phase == "attack"
    return p


def arrange(g, attacker, source, target, a_armies, d_armies):
    """Force a specific attack scenario."""
    defender = next(p for p in g.players if p != attacker)
    g.owner[source], g.owner[target] = attacker, defender
    g.armies[source], g.armies[target] = a_armies, d_armies


class MapTests(unittest.TestCase):
    def test_classic_loads(self):
        m = GameMap.load(CLASSIC)
        self.assertEqual(len(m.territories), 42)
        self.assertEqual(len(m.continents), 6)
        self.assertEqual(sum(len(c.territories) for c in m.continents.values()), 42)

    def test_asymmetric_adjacency_rejected(self):
        d = {"continents": {"c": {"bonus": 1}},
             "territories": {t: {"continent": "c", "neighbors": []} for t in "abcd"}}
        d["territories"]["a"]["neighbors"] = ["b"]
        with self.assertRaises(MapError):
            GameMap.from_dict(d)

    def test_disconnected_graph_rejected(self):
        d = {"continents": {"c": {"bonus": 1}},
             "territories": {
                 "a": {"continent": "c", "neighbors": ["b"]},
                 "b": {"continent": "c", "neighbors": ["a"]},
                 "c": {"continent": "c", "neighbors": ["d"]},
                 "d": {"continent": "c", "neighbors": ["c"]}}}
        with self.assertRaises(MapError):
            GameMap.from_dict(d)


class SetupTests(unittest.TestCase):
    def test_starting_armies_by_player_count(self):
        for n, armies in {2: 40, 3: 35, 4: 30, 5: 25, 6: 20}.items():
            g = new_game(n)
            total = {p: 0 for p in g.players}
            for t, o in g.owner.items():
                if o in total:  # ignora al neutral
                    total[o] += g.armies[t]
            for p in g.players:
                self.assertEqual(total[p] + g.setup_left[p], armies)

    def test_all_territories_dealt_evenly(self):
        g = new_game(4)
        counts = sorted(len(g.territories_of(p)) for p in g.players)
        self.assertLessEqual(counts[-1] - counts[0], 1)

    def test_two_players_get_a_neutral_side(self):
        g = new_game(2)
        self.assertEqual(g.neutral, "neutral")
        self.assertNotIn("neutral", g.players)
        for side in ("p0", "p1", "neutral"):
            self.assertEqual(len(g.territories_of(side)), 14)
        # el neutral tiene el mismo total de ejércitos que un jugador
        total = sum(g.armies[t] for t in g.territories_of("neutral"))
        self.assertEqual(total, 40)
        self.assertNotIn("neutral", g.setup_left)
        self.assertIsNone(new_game(3).neutral)

    def test_neutral_never_gets_a_turn(self):
        g = new_game(2)
        for _ in range(6):
            while g.phase == "setup":
                g.place(g.current, g.territories_of(g.current)[0])
            self.assertIn(g.current, ("p0", "p1"))
            g.reinforcements = 0
            g.phase = "fortify"
            g.end_turn(g.current)

    def test_conquering_all_neutral_territories_keeps_game_going(self):
        g = new_game(2)
        to_attack_phase(g)
        me = g.current
        for t in g.territories_of("neutral"):
            g.owner[t] = me
        self.assertEqual(g.eliminated, [])
        self.assertIsNone(g.winner)

    def test_setup_batch_keeps_turn_until_quota_done(self):
        g = Game(GameMap.load(CLASSIC), ["p0", "p1", "p2"], ScriptedRng(1), setup_batch=5)
        first = g.current
        for _ in range(4):
            g.place(first, g.territories_of(first)[0])
            self.assertEqual(g.current, first)
        g.place(first, g.territories_of(first)[0])
        self.assertNotEqual(g.current, first)
        with self.assertRaises(RuleError):  # no puede pasarse de la cuota
            g.place(g.current, g.territories_of(g.current)[0], 6)

    def test_setup_batch_handles_remainder_and_finishes(self):
        for batch in (3, 10, 0):
            g = Game(GameMap.load(CLASSIC), ["p0", "p1", "p2"], ScriptedRng(1), setup_batch=batch)
            while g.phase == "setup":
                n = g.setup_turn_left
                g.place(g.current, g.territories_of(g.current)[0], n)
            self.assertEqual(g.phase, "reinforce")
            self.assertTrue(all(v == 0 for v in g.setup_left.values()))

    # --- preparación simultánea y oculta
    def sim_game(self, n=3, batch=10):
        return Game(GameMap.load(CLASSIC), [f"p{i}" for i in range(n)], ScriptedRng(1),
                    setup_batch=batch, simultaneous_setup=True)

    def test_simultaneous_setup_has_no_turns_and_rounds_unlock(self):
        g = self.sim_game(3, batch=10)
        self.assertEqual(g.setup_round, 1)
        for p in g.players:
            self.assertEqual(g.setup_quota[p], 10)
        # cualquiera puede colocar, en cualquier orden; no hay que confirmar
        for p in reversed(g.players[1:]):
            g.place(p, g.territories_of(p)[0], 10)
            self.assertTrue(g.setup_ready[p])      # queda en espera solo
        self.assertEqual(g.setup_round, 1)         # falta p0
        g.place("p0", g.territories_of("p0")[0], 10)
        self.assertEqual(g.setup_round, 2)         # se desbloquean otros 10
        self.assertEqual(g.setup_quota["p0"], 10)
        self.assertFalse(g.setup_ready["p0"])

    def test_simultaneous_setup_quota_undo_and_wait(self):
        g = self.sim_game(3, batch=10)
        p = "p0"
        t = g.territories_of(p)[0]
        g.place(p, t, 4)
        self.assertFalse(g.setup_ready[p])
        with self.assertRaises(RuleError):
            g.place(p, t, 7)             # se pasa de la cuota
        g.undo_setup(p)
        self.assertEqual(g.to_dict(p)["setup_placed"], 0)
        g.place(p, t, 10)
        self.assertTrue(g.setup_ready[p])
        with self.assertRaises(RuleError):
            g.place(p, t, 1)             # ya completó la ronda: espera
        with self.assertRaises(RuleError):
            g.undo_setup(p)
        self.assertEqual(g.armies[t], 1)  # aún no se aplica: faltan los demás

    def test_simultaneous_setup_hides_other_players_placements(self):
        g = self.sim_game(3, batch=10)
        t0 = g.territories_of("p0")[0]
        g.place("p0", t0, 10)
        mine = g.to_dict("p0")["armies"][t0]
        theirs = g.to_dict("p1")["armies"][t0]
        self.assertEqual(mine, 11)       # el dueño ve su colocación
        self.assertEqual(theirs, 1)      # los demás no
        self.assertEqual(g.to_dict("p1")["setup_placed"], 0)

    def test_simultaneous_setup_finishes_and_reveals(self):
        g = self.sim_game(3, batch=10)
        while g.phase == "setup":
            for p in g.players:
                g.place(p, g.territories_of(p)[0], g.setup_quota[p])
        self.assertEqual(g.phase, "reinforce")
        self.assertEqual(g.current, "p0")
        t0 = g.territories_of("p1")[0]
        self.assertEqual(g.to_dict("p0")["armies"][t0], g.armies[t0])  # ya visible
        for p in g.players:
            total = sum(g.armies[t] for t in g.territories_of(p))
            self.assertEqual(total, 35)

    def test_setup_round_robin_then_play(self):
        g = new_game(3)
        seen = []
        while g.phase == "setup":
            seen.append(g.current)
            g.place(g.current, g.territories_of(g.current)[0])
        self.assertEqual(seen[:3], ["p0", "p1", "p2"])
        self.assertEqual(g.phase, "reinforce")
        self.assertEqual(g.current, "p0")
        self.assertTrue(all(v == 0 for v in g.setup_left.values()))

    def test_cannot_place_out_of_turn_or_on_enemy(self):
        g = new_game(3)
        other = g.players[1]
        with self.assertRaises(RuleError):
            g.place(other, g.territories_of(other)[0])
        enemy = g.territories_of(other)[0]
        with self.assertRaises(RuleError):
            g.place(g.current, enemy)

    def test_player_count_limits(self):
        m = GameMap.load(CLASSIC)
        with self.assertRaises(RuleError):
            Game(m, ["a"])
        with self.assertRaises(RuleError):
            Game(m, list("abcdefg"))
        Game(m, ["a", "b"])  # two players is allowed


class ReinforcementTests(unittest.TestCase):
    def test_minimum_three(self):
        g = new_game(3)
        for t in g.owner:
            g.owner[t] = "p1"
        g.owner["alaska"] = "p0"
        g.owner["kamchatka"] = "p0"
        self.assertEqual(g.reinforcement_income("p0"), 3)

    def test_territories_divided_by_three(self):
        g = new_game(3)
        mine = [t for t in g.owner if g.owner[t] == "p0"]
        for t in g.owner:
            g.owner[t] = "p1"
        for t in list(g.map.territories)[:15]:
            g.owner[t] = "p0"
        # may also complete a continent; compare against computed bonus
        base = 15 // 3
        bonus = sum(c.bonus for c in g.map.continents.values()
                    if c.territories <= set(g.territories_of("p0")))
        self.assertEqual(g.reinforcement_income("p0"), base + bonus)

    def test_continent_bonus(self):
        g = new_game(3)
        for t in g.owner:
            g.owner[t] = "p1"
        for t in g.map.continents["australia"].territories:
            g.owner[t] = "p0"
        self.assertEqual(g.reinforcement_income("p0"), 3 + 2)
        for t in g.map.continents["asia"].territories:
            g.owner[t] = "p0"
        self.assertEqual(g.reinforcement_income("p0"), (16 // 3) + 2 + 7)

    def test_reinforce_must_place_all_before_attack(self):
        g = new_game(3)
        while g.phase == "setup":
            g.place(g.current, g.territories_of(g.current)[0])
        p = g.current
        t = g.territories_of(p)[0]
        n = g.reinforcements
        g.place(p, t, n - 1)
        self.assertEqual(g.phase, "reinforce")
        with self.assertRaises(RuleError):
            g.attack(p, t, "alaska", 1)
        g.place(p, t, 1)
        self.assertEqual(g.phase, "attack")


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.g = new_game(3)
        self.p = to_attack_phase(self.g)
        arrange(self.g, self.p, "alaska", "kamchatka", 5, 3)

    def test_attacker_wins_all(self):
        self.g.rng.queue = [6, 5, 4, 3, 2]  # att 6,5,4  vs def 3,2
        r = self.g.attack(self.p, "alaska", "kamchatka", 3)
        self.assertEqual((r["attacker_lost"], r["defender_lost"]), (0, 2))
        self.assertEqual(self.g.armies["kamchatka"], 1)
        self.assertEqual(self.g.armies["alaska"], 5)

    def test_ties_favour_defender(self):
        self.g.rng.queue = [4, 4, 4]  # att 4 vs def 4,4
        r = self.g.attack(self.p, "alaska", "kamchatka", 1)
        self.assertEqual((r["attacker_lost"], r["defender_lost"]), (1, 0))

    def test_split_result(self):
        self.g.rng.queue = [6, 2, 5, 3]  # att 6,2 vs def 5,3
        r = self.g.attack(self.p, "alaska", "kamchatka", 2)
        self.assertEqual((r["attacker_lost"], r["defender_lost"]), (1, 1))

    def test_defender_with_one_army_rolls_one_die(self):
        self.g.armies["kamchatka"] = 1
        self.g.rng.queue = [3, 6]  # one attacker die, one defender die
        r = self.g.attack(self.p, "alaska", "kamchatka", 1)
        self.assertEqual(len(r["defender_rolls"]), 1)

    def test_dice_limited_by_armies(self):
        self.g.armies["alaska"] = 3
        with self.assertRaises(RuleError):
            self.g.attack(self.p, "alaska", "kamchatka", 3)
        self.g.armies["alaska"] = 1
        with self.assertRaises(RuleError):
            self.g.attack(self.p, "alaska", "kamchatka", 1)

    def test_must_be_adjacent_and_enemy(self):
        with self.assertRaises(RuleError):
            self.g.attack(self.p, "alaska", "brazil", 1)
        self.g.owner["alberta"] = self.p
        with self.assertRaises(RuleError):
            self.g.attack(self.p, "alaska", "alberta", 1)

    def test_conquest_and_move_in(self):
        self.g.armies["kamchatka"] = 1
        self.g.rng.queue = [6, 6, 5, 1]  # att 6,6,5 ... def 1
        r = self.g.attack(self.p, "alaska", "kamchatka", 3)
        self.assertTrue(r["conquered"])
        self.assertEqual(self.g.owner["kamchatka"], self.p)
        self.assertEqual(self.g.phase, "conquer_move")
        with self.assertRaises(RuleError):
            self.g.move_in(self.p, 2)   # below dice count
        with self.assertRaises(RuleError):
            self.g.move_in(self.p, 5)   # would leave source empty
        self.g.move_in(self.p, 3)
        self.assertEqual(self.g.armies["kamchatka"], 3)
        self.assertEqual(self.g.armies["alaska"], 2)
        self.assertEqual(self.g.phase, "attack")

    def test_cannot_attack_during_conquer_move(self):
        self.g.armies["kamchatka"] = 1
        self.g.rng.queue = [6, 1]
        self.g.attack(self.p, "alaska", "kamchatka", 1)
        with self.assertRaises(RuleError):
            self.g.attack(self.p, "alaska", "alberta", 1)

    def test_elimination_transfers_cards_and_winner(self):
        g = self.g
        victim = g.owner["kamchatka"]
        for t in g.owner:
            if g.owner[t] == victim and t != "kamchatka":
                g.owner[t] = "p2" if victim != "p2" else "p1"
        g.armies["kamchatka"] = 1
        g.cards[victim] = [Card("infantry", "japan")]
        g.rng.queue = [6, 1]
        r = g.attack(self.p, "alaska", "kamchatka", 1)
        self.assertEqual(r["eliminated"], victim)
        self.assertIn(victim, g.eliminated)
        self.assertEqual(g.cards[self.p][-1].territory, "japan")

    def test_victory_when_all_owned(self):
        g = self.g
        for t in g.owner:
            g.owner[t] = self.p
        g.owner["kamchatka"] = "p1"
        g.armies["kamchatka"] = 1
        g.rng.queue = [6, 1]
        g.attack(self.p, "alaska", "kamchatka", 1)
        self.assertEqual(g.winner, self.p)
        self.assertEqual(g.phase, "finished")


class FortifyAndTurnTests(unittest.TestCase):
    def test_fortify_requires_connected_own_path(self):
        g = new_game(3)
        p = to_attack_phase(g)
        for t in g.owner:
            g.owner[t] = "p1"
        for t in ("alaska", "alberta", "ontario", "brazil"):
            g.owner[t] = p
            g.armies[t] = 4
        g.end_attack(p)
        with self.assertRaises(RuleError):
            g.fortify(p, "alaska", "brazil", 1)  # not connected
        with self.assertRaises(RuleError):
            g.fortify(p, "alaska", "ontario", 4)  # would leave 0
        g.fortify(p, "alaska", "ontario", 3)      # via alberta
        self.assertEqual(g.armies["ontario"], 7)
        self.assertEqual(g.armies["alaska"], 1)
        self.assertNotEqual(g.current, p)         # turn passed
        self.assertEqual(g.phase, "reinforce")

    def test_card_drawn_only_after_conquest(self):
        g = new_game(3)
        p = to_attack_phase(g)
        g.end_turn(p)
        self.assertEqual(len(g.cards[p]), 0)
        p2 = g.current
        p2 = to_attack_phase_from_reinforce(g)
        g.conquered_this_turn = True
        g.end_turn(p2)
        self.assertEqual(len(g.cards[p2]), 1)

    def test_eliminated_players_skipped(self):
        g = new_game(3)
        p = to_attack_phase(g)
        g.eliminated.append("p1")
        g.end_turn(p)
        self.assertEqual(g.current, "p2")


def to_attack_phase_from_reinforce(g):
    p = g.current
    g.place(p, g.territories_of(p)[0], g.reinforcements)
    return p


class CardTests(unittest.TestCase):
    def test_valid_sets(self):
        i, c, a, w = (Card(s) for s in ("infantry", "cavalry", "artillery", "wild"))
        self.assertTrue(is_valid_set([i, i, i]))
        self.assertTrue(is_valid_set([i, c, a]))
        self.assertTrue(is_valid_set([i, i, w]))
        self.assertTrue(is_valid_set([w, w, a]))
        self.assertFalse(is_valid_set([i, i, c]))

    def test_set_values(self):
        vals = [set_value(n) for n in range(9)]
        self.assertEqual(vals, [4, 6, 8, 10, 12, 15, 20, 25, 30])

    def test_trade_gives_armies_and_territory_bonus(self):
        g = new_game(3)
        while g.phase == "setup":
            g.place(g.current, g.territories_of(g.current)[0])
        p = g.current
        mine = g.territories_of(p)[0]
        g.cards[p] = [Card("infantry", mine), Card("infantry", "x"), Card("infantry", "y")]
        before_armies, before_reinf = g.armies[mine], g.reinforcements
        self.assertEqual(g.trade_cards(p, [0, 1, 2]), 4)
        self.assertEqual(g.reinforcements, before_reinf + 4)
        self.assertEqual(g.armies[mine], before_armies + 2)
        self.assertEqual(g.cards[p], [])
        self.assertEqual(g.sets_traded, 1)

    def test_invalid_trade_rejected(self):
        g = new_game(3)
        while g.phase == "setup":
            g.place(g.current, g.territories_of(g.current)[0])
        p = g.current
        g.cards[p] = [Card("infantry", "a"), Card("infantry", "b"), Card("cavalry", "c")]
        with self.assertRaises(RuleError):
            g.trade_cards(p, [0, 1, 2])

    def test_five_cards_force_trade_before_placing(self):
        g = new_game(3)
        while g.phase == "setup":
            g.place(g.current, g.territories_of(g.current)[0])
        p = g.current
        g.cards[p] = [Card("infantry", t) for t in list(g.map.territories)[:5]]
        with self.assertRaises(RuleError):
            g.place(p, g.territories_of(p)[0], 1)
        g.trade_cards(p, [0, 1, 2])
        g.place(p, g.territories_of(p)[0], 1)  # now allowed


class FullGameSmokeTest(unittest.TestCase):
    def test_random_playthrough_terminates_consistently(self):
        """Random legal play must never crash or break army/ownership invariants."""
        for seed in range(5):
            g = new_game(4, seed)
            rng = random.Random(seed)
            for _ in range(100000):
                if g.phase == "finished":
                    break
                p = g.current
                mine = g.territories_of(p)
                border = [t for t in mine
                          if any(g.owner[n] != p for n in g.map.territories[t].neighbors)]
                strongest = max(border or mine, key=lambda t: g.armies[t])
                if g.phase in ("setup", "reinforce"):
                    if len(g.cards[p]) >= 5:
                        g.trade_cards(p, self._find_set(g.cards[p]))
                    g.place(p, strongest if g.phase == "reinforce" else rng.choice(mine),
                            1 if g.phase == "setup" else g.reinforcements)
                elif g.phase == "attack":
                    if len(g.cards[p]) > 5:
                        g.trade_cards(p, self._find_set(g.cards[p]))
                    elif g.reinforcements:
                        g.place(p, strongest, g.reinforcements)
                    else:
                        opts = [(s, n) for s in mine if g.armies[s] > 1
                                for n in g.map.territories[s].neighbors if g.owner[n] != p]
                        if opts:
                            s, n = max(opts, key=lambda o: g.armies[o[0]] - g.armies[o[1]])
                            g.attack(p, s, n, min(3, g.armies[s] - 1))
                        else:
                            g.end_attack(p)
                elif g.phase == "conquer_move":
                    g.move_in(p, g.pending_conquest["max"])
                elif g.phase == "fortify":
                    g.end_turn(p)
                for t in g.owner:
                    # a just-conquered territory is empty until move_in
                    floor = 0 if g.phase == "conquer_move" else 1
                    self.assertGreaterEqual(g.armies[t], floor)
                    self.assertIn(g.owner[t], g.players)
            self.assertEqual(g.phase, "finished", f"seed {seed} did not finish")
            self.assertEqual(len(g.alive_players()), 1)

    @staticmethod
    def _find_set(hand):
        from itertools import combinations
        for combo in combinations(range(len(hand)), 3):
            if is_valid_set([hand[i] for i in combo]):
                return list(combo)
        raise AssertionError("no set in hand")


if __name__ == "__main__":
    unittest.main()
