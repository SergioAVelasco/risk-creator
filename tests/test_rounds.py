import unittest
from pathlib import Path

from risk import Game, GameMap, RuleError
from tests.test_rules import ScriptedRng

CLASSIC = Path(__file__).parent.parent / "maps" / "classic" / "map.json"


def new_round(n=3, seed=1):
    """Partida simultánea ya en la fase de planificación de la ronda 1."""
    g = Game(GameMap.load(CLASSIC), [f"p{i}" for i in range(n)], ScriptedRng(seed),
             setup_batch=0, simultaneous_setup=True, simultaneous_play=True)
    while g.phase == "setup":
        for p in g.players:
            if not g.setup_ready[p]:
                g.place(p, g.territories_of(p)[0], g.setup_quota[p])
    assert g.phase == "plan"
    return g


def arrange(g, **terr):
    """terr: id=(owner, armies). Fuerza el estado de esos territorios."""
    for t, (o, a) in terr.items():
        g.owner[t], g.armies[t] = o, a


def drop_reinforcements(g):
    for p in g.players:
        g.reinf[p] = 0


def start_battle(g):
    for p in g.players:
        g.set_ready(p)
    assert g.phase == "battle"


class PlanTests(unittest.TestCase):
    def test_setup_leads_to_simultaneous_plan(self):
        g = new_round()
        self.assertEqual(g.round, 1)
        self.assertTrue(all(g.reinf[p] >= 3 for p in g.players))
        self.assertFalse(any(g.ready.values()))

    def test_reinforcements_are_placed_in_parallel_and_hidden(self):
        g = new_round()
        t = g.territories_of("p0")[0]
        before = g.to_dict("p1")["armies"][t]
        g.place("p0", t, g.reinf["p0"])
        self.assertEqual(g.reinf["p0"], 0)
        self.assertEqual(g.to_dict("p1")["armies"][t], before)       # otros no lo ven
        self.assertEqual(g.to_dict("p0")["armies"][t], before + g.armies[t] - before)
        g.place("p1", g.territories_of("p1")[0], 1)                    # sin esperar turno

    def test_declare_needs_reinforcements_placed_and_keeps_one_home(self):
        g = new_round()
        arrange(g, alaska=("p0", 5), kamchatka=("p1", 3))
        with self.assertRaises(RuleError):
            g.declare_attack("p0", "alaska", "kamchatka", 2)           # refuerzos sin colocar
        g.reinf["p0"] = 0
        with self.assertRaises(RuleError):
            g.declare_attack("p0", "alaska", "kamchatka", 5)           # debe quedar 1
        g.declare_attack("p0", "alaska", "kamchatka", 4)
        g.declare_attack("p0", "alaska", "kamchatka", 2)               # reemplaza
        self.assertEqual(g.declared["p0"], [{"from": "alaska", "to": "kamchatka", "n": 2}])
        with self.assertRaises(RuleError):
            g.declare_attack("p0", "alaska", "alberta", 4)             # 2 ya comprometidos
        g.cancel_attack("p0", "alaska", "kamchatka")
        self.assertEqual(g.declared["p0"], [])

    def test_declarations_are_private_and_ready_locks_the_plan(self):
        g = new_round()
        arrange(g, alaska=("p0", 5), kamchatka=("p1", 3))
        drop_reinforcements(g)
        g.declare_attack("p0", "alaska", "kamchatka", 3)
        self.assertEqual(g.to_dict("p1")["declared"], [])
        g.set_ready("p0")
        with self.assertRaises(RuleError):
            g.declare_attack("p0", "alaska", "kamchatka", 1)
        g.set_ready("p0", False)
        g.declare_attack("p0", "alaska", "kamchatka", 1)

    def test_timeout_forces_the_phase_forward(self):
        g = new_round()
        g.force_advance()
        self.assertEqual(g.phase, "fortify")                           # nadie atacó
        g.force_advance()
        self.assertEqual((g.phase, g.round), ("plan", 2))
        self.assertTrue(all(g.reinf[p] >= 3 for p in g.players))


class BattleTests(unittest.TestCase):
    def setUp(self):
        self.g = g = new_round()
        # alaska(p0) <-> kamchatka(p1); alberta(p2) es vecina de alaska
        arrange(g, alaska=("p0", 6), kamchatka=("p1", 6), alberta=("p2", 6))
        drop_reinforcements(g)

    def test_city_battle_defender_wins_ties_and_army_leaves_source(self):
        g = self.g
        g.declare_attack("p0", "alaska", "kamchatka", 2)
        start_battle(g)
        self.assertEqual(g.armies["alaska"], 4)                  # 2 salieron a combatir
        g.rng.queue = [4, 3, 4, 3]                               # atac 4,3 vs def 4,3
        g.force_advance()
        self.assertEqual(g.last_tick[0]["lost"], [2, 0])
        self.assertEqual(g.armies["kamchatka"], 6)
        self.assertEqual(g.phase, "fortify")                     # ejército aniquilado

    def test_field_battle_tie_costs_both_then_winner_attacks_the_city(self):
        g = self.g
        g.declare_attack("p0", "alaska", "kamchatka", 2)
        g.declare_attack("p1", "kamchatka", "alaska", 1)
        start_battle(g)
        (b,) = g.battles
        self.assertEqual(b["kind"], "field")
        g.rng.queue = [5, 4, 5]                                  # p0: 5,4 vs p1: 5 -> empate
        g.force_advance()
        self.assertEqual(g.last_tick[0]["lost"], [1, 1])
        (b,) = g.battles                                         # p1 perdió: p0 sigue a la ciudad
        self.assertEqual((b["kind"], b["sides"][0]["owner"], b["sides"][0]["n"]), ("city", "p0", 1))
        self.assertEqual(b["sides"][0]["dst"], "kamchatka")

    def test_retreat_roll_cannot_kill_but_can_be_killed(self):
        g = self.g
        g.declare_attack("p0", "alaska", "kamchatka", 2)
        g.declare_attack("p1", "kamchatka", "alaska", 2)
        start_battle(g)
        g.set_retreat("p0", g.battles[0]["id"])
        g.rng.queue = [3, 2, 6, 5]                               # p1 gana ambas parejas
        g.force_advance()
        self.assertEqual(g.last_tick[0]["lost"], [2, 0])         # p0 pierde todo al retirarse
        self.assertEqual(g.battles[0]["sides"][0]["owner"], "p1")  # p1 sigue contra alaska

    def test_retreat_returns_survivors_home_when_rival_rolls_low(self):
        g = self.g
        g.declare_attack("p0", "alaska", "kamchatka", 3)
        g.declare_attack("p1", "kamchatka", "alaska", 1)
        start_battle(g)
        g.set_retreat("p0", g.battles[0]["id"])
        g.rng.queue = [6, 5, 4, 1]                               # p0 3 dados, p1 1 dado (1)
        g.force_advance()
        self.assertEqual(g.last_tick[0]["lost"], [0, 0])         # p0 no elimina, p1 no gana
        self.assertEqual(g.armies["alaska"], 6)                  # los 3 volvieron
        (b,) = g.battles
        self.assertEqual(b["sides"][0]["owner"], "p1")

    def test_both_retreat_means_no_roll(self):
        g = self.g
        g.declare_attack("p0", "alaska", "kamchatka", 2)
        g.declare_attack("p1", "kamchatka", "alaska", 2)
        start_battle(g)
        for p in ("p0", "p1"):
            g.set_retreat(p, g.battles[0]["id"])
        g.force_advance()
        self.assertEqual(g.last_tick[0]["rolls"], [])
        self.assertEqual((g.armies["alaska"], g.armies["kamchatka"]), (6, 6))
        self.assertEqual(g.phase, "fortify")

    def test_cannot_retreat_if_home_was_taken(self):
        g = self.g
        g.declare_attack("p0", "alaska", "kamchatka", 2)
        g.declare_attack("p1", "kamchatka", "alaska", 2)
        start_battle(g)
        g.owner["kamchatka"] = "p2"                              # el origen de p1 cae
        with self.assertRaises(RuleError):
            g.set_retreat("p1", g.battles[0]["id"])

    def test_two_fronts_split_the_garrison_dice(self):
        g = self.g
        arrange(g, alaska=("p1", 3), alberta=("p2", 6), northwest_territory=("p0", 6))
        g.declare_attack("p2", "alberta", "alaska", 1)
        g.declare_attack("p0", "northwest_territory", "alaska", 1)
        start_battle(g)
        g.rng.queue = [1, 1, 1, 1, 1]
        g.force_advance()
        defender_dice = sorted(len(r["rolls"][1]) for r in g.last_tick)
        self.assertEqual(defender_dice, [1, 2])                  # 3 ejércitos -> 2 y 1 dados

    def test_conquest_when_garrison_falls_and_card_at_end(self):
        g = self.g
        arrange(g, kamchatka=("p1", 1))
        g.declare_attack("p0", "alaska", "kamchatka", 3)
        start_battle(g)
        g.rng.queue = [6, 5, 4, 1]
        g.force_advance()
        self.assertEqual(g.owner["kamchatka"], "p0")
        self.assertEqual(g.armies["kamchatka"], 3)               # todo el ejército ocupa
        self.assertEqual(len(g.cards["p0"]), 1)                  # carta al terminar las batallas
        self.assertEqual(g.phase, "fortify")

    def test_second_attacker_fights_the_conqueror(self):
        g = self.g
        arrange(g, alberta=("p1", 1), alaska=("p0", 5), northwest_territory=("p2", 5))
        g.declare_attack("p0", "alaska", "alberta", 2)
        g.declare_attack("p2", "northwest_territory", "alberta", 1)
        start_battle(g)
        g.rng.queue = [6, 5, 1, 6, 1]    # p0 (2 dados) vs 1 ; p2 (1 dado) vs 1
        g.force_advance()
        self.assertEqual(g.owner["alberta"], "p0")               # conserva más ejército
        (b,) = g.battles
        self.assertEqual((b["sides"][0]["owner"], b["sides"][0]["dst"]), ("p2", "alberta"))
        self.assertEqual(g.armies["alberta"], 2)

    def test_elimination_gives_cards_after_battles_and_win(self):
        g = Game(GameMap.load(CLASSIC), ["p0", "p1"], ScriptedRng(1), setup_batch=0,
                 simultaneous_setup=True, simultaneous_play=True)
        while g.phase == "setup":
            for p in g.players:
                if not g.setup_ready[p]:
                    g.place(p, g.territories_of(p)[0], g.setup_quota[p])
        for t in g.owner:
            g.owner[t], g.armies[t] = "p0", 3
        arrange(g, kamchatka=("p1", 1))
        g.cards["p1"] = [object()]
        drop_reinforcements(g)
        g.declare_attack("p0", "alaska", "kamchatka", 2)
        start_battle(g)
        g.rng.queue = [6, 5, 1]
        g.force_advance()
        self.assertIn("p1", g.eliminated)
        self.assertEqual(len(g.cards["p0"]), 2)                  # su mano + la de conquista
        self.assertEqual((g.phase, g.winner), ("finished", "p0"))

    def test_continue_votes_trigger_the_tick_early(self):
        g = self.g
        g.declare_attack("p0", "alaska", "kamchatka", 1)
        g.declare_attack("p2", "alberta", "alaska", 1)
        start_battle(g)
        g.battle_continue("p0")
        self.assertEqual(g.last_tick, [])
        g.battle_continue("p2")
        self.assertEqual(len(g.last_tick), 2)

    def test_fortify_is_simultaneous_and_next_round_starts_when_all_ready(self):
        g = self.g
        g.force_advance()
        self.assertEqual(g.phase, "fortify")
        mine = g.territories_of("p0")
        a = next(t for t in mine if g.armies[t] > 1)
        b = next((t for t in mine if t != a and g._connected("p0", a, t)), None)
        if b:
            g.fortify("p0", a, b, 1)
        else:
            g.set_ready("p0")
        self.assertEqual(g.phase, "fortify")                     # faltan los demás
        g.set_ready("p1"); g.set_ready("p2")
        self.assertEqual((g.phase, g.round), ("plan", 2))


class ClassicStillWorks(unittest.TestCase):
    def test_simultaneous_play_requires_simultaneous_setup(self):
        with self.assertRaises(RuleError):
            Game(GameMap.load(CLASSIC), ["a", "b"], simultaneous_play=True)


if __name__ == "__main__":
    unittest.main()


class RoomTimerTests(unittest.TestCase):
    def test_deadline_advances_the_phase_and_exposes_the_timer(self):
        from server.rooms import Room
        r = Room("TEST", "classic")
        for n in ("Ana", "Beto"):
            r.join(n, None)
        r.set_option("p1", {"turn_time": 60, "setup_batch": 0})
        r.start("p1")
        g = r.game
        for p in g.players:
            g.place(p, g.territories_of(p)[0], g.setup_quota[p])
        self.assertEqual(g.phase, "plan")
        r.sync_timer(1000.0)
        self.assertEqual(r.deadline, 1060.0)
        self.assertFalse(r.poll(1059.0))
        self.assertTrue(r.poll(1060.0))
        self.assertEqual(g.phase, "fortify")            # nadie declaró ataques
        self.assertEqual(r.deadline, 1060.0 + 20)       # fortificar: max(15, 60//3)

    def test_classic_rules_option(self):
        from server.rooms import Room
        r = Room("TEST", "classic")
        r.join("Ana", None); r.join("Beto", None)
        r.set_option("p1", {"rules": "classic", "setup_batch": 0})
        r.start("p1")
        self.assertFalse(r.game.simultaneous_play)
