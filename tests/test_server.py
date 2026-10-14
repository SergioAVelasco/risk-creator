import unittest

try:
    from fastapi.testclient import TestClient
    from server.app import app
except ImportError:  # server deps are optional for the engine tests
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class ServerTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_maps_listed(self):
        maps = self.client.get("/api/maps").json()
        self.assertIn("classic", [m["id"] for m in maps])

    def test_full_lobby_and_first_moves(self):
        code = self.client.post("/api/rooms", json={"map": "classic"}).json()["code"]
        with self.client.websocket_connect(f"/ws/{code}?name=Ana") as a, \
             self.client.websocket_connect(f"/ws/{code}?name=Beto") as b, \
             self.client.websocket_connect(f"/ws/{code}?name=Carla") as c:
            welcome = a.receive_json()
            self.assertEqual(welcome["type"], "welcome")
            self.assertEqual(welcome["pid"], "p1")
            # b/c joined: drain until each has its welcome
            self.assertEqual(b.receive_json()["pid"], "p2")
            self.assertEqual(c.receive_json()["pid"], "p3")

            # non-host cannot start
            b.send_json({"type": "start"})
            while True:
                m = b.receive_json()
                if m["type"] == "error":
                    break
            self.assertIn("host", m["message"])

            # non-host cannot change settings; host sets the classic 1-per-turn setup
            b.send_json({"type": "settings", "setup_batch": 10})
            while True:
                m = b.receive_json()
                if m["type"] == "error":
                    break
            self.assertIn("host", m["message"])
            a.send_json({"type": "settings", "setup_batch": 1})

            a.send_json({"type": "start"})
            state = None
            while state is None or state["game"] is None:
                m = a.receive_json()
                if m["type"] == "state":
                    state = m
            g = state["game"]
            self.assertEqual(g["phase"], "setup")
            self.assertEqual(g["current"], "p1")
            self.assertEqual(g["cards"], [])
            mine = [t for t, o in g["owner"].items() if o == "p1"][0]
            a.send_json({"type": "place", "territory": mine})
            while True:
                m = a.receive_json()
                if m["type"] == "state" and m["game"]["current"] == "p2":
                    break
            # wrong player acting gets an error, not a crash
            a.send_json({"type": "place", "territory": mine})
            while True:
                m = a.receive_json()
                if m["type"] == "error":
                    break
            self.assertIn("turn", m["message"])

    def test_creator_can_choose_the_room_code(self):
        r = self.client.post("/api/rooms", json={"map": "classic", "code": "ab12"})
        self.assertEqual(r.json()["code"], "AB12")
        dup = self.client.post("/api/rooms", json={"map": "classic", "code": "AB12"})
        self.assertEqual(dup.status_code, 400)
        bad = self.client.post("/api/rooms", json={"map": "classic", "code": "xy"})
        self.assertEqual(bad.status_code, 400)

    def test_rejoin_by_name_after_disconnect(self):
        code = self.client.post("/api/rooms", json={"map": "classic"}).json()["code"]
        with self.client.websocket_connect(f"/ws/{code}?name=Ana") as a:
            a.receive_json()
            with self.client.websocket_connect(f"/ws/{code}?name=Beto") as b:
                token = b.receive_json()["token"]
                a.send_json({"type": "start"})
            # Beto se desconectó; vuelve sin token (otro navegador) con su nombre
            with self.client.websocket_connect(f"/ws/{code}?name=beto") as b2:
                w = b2.receive_json()
                self.assertEqual(w["type"], "welcome")
                self.assertEqual(w["pid"], "p2")
                self.assertEqual(w["token"], token)
            # un nombre desconocido no puede entrar a una partida ya iniciada
            with self.client.websocket_connect(f"/ws/{code}?name=Intruso") as x:
                self.assertEqual(x.receive_json()["type"], "fatal")

    def test_unknown_room_is_fatal(self):
        with self.client.websocket_connect("/ws/ZZZZ?name=X") as ws:
            self.assertEqual(ws.receive_json()["type"], "fatal")

    def test_reconnect_with_token(self):
        code = self.client.post("/api/rooms", json={"map": "classic"}).json()["code"]
        with self.client.websocket_connect(f"/ws/{code}?name=Ana") as a:
            w = a.receive_json()
        with self.client.websocket_connect(f"/ws/{code}?name=Ana&token={w['token']}") as a2:
            w2 = a2.receive_json()
        self.assertEqual(w2["pid"], w["pid"])


if __name__ == "__main__":
    unittest.main()
