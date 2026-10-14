# Risk Creator

Risk clásico con mapas personalizables. El motor de reglas es independiente del mapa.

## Jugar

```bash
python3 -m pip install --user -r requirements.txt   # una vez
python3 -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Abre `http://localhost:8000`, crea una sala y comparte el enlace (o el código de 4
letras). Cada jugador entra desde su navegador; 3 a 6 jugadores. En red local, usa la
IP de la máquina en vez de `localhost`.

## Estructura

- `risk/` — motor (Python, sin dependencias)
  - `gamemap.py` carga y valida mapas · `game.py` reglas · `cards.py` cartas
- `server/` — FastAPI: salas (`rooms.py`) y WebSocket (`app.py`)
- `client/` — interfaz web (HTML/JS sin build)
- `tools/build_classic_svg.py` — genera `maps/classic/map.svg` desde `world.svg`
- `maps/<nombre>/map.json` — un mapa
- `tests/` — `python3 -m unittest discover -s tests -t .`

## Formato de mapa (`map.json`)

```json
{
  "name": "Mi mapa",
  "svg": "map.svg",
  "continents": { "norte": { "name": "Norte", "bonus": 3 } },
  "territories": {
    "a": { "name": "Territorio A", "continent": "norte", "neighbors": ["b"] },
    "b": { "name": "Territorio B", "continent": "norte", "neighbors": ["a"] }
  }
}
```

Reglas de validación: adyacencias simétricas, grafo conexo, cada territorio en un
continente existente. `svg` es opcional y solo presentacional: cada territorio es un
`<path>` del SVG con `id` igual al id del territorio (y `data-cx`/`data-cy` para la
posición del contador de ejércitos). Las conexiones que no se tocan en el dibujo
(mar, o Alaska–Kamchatka al dar la vuelta al mapa) van en `<g id="links">` como
`<line data-a data-b>`.

## Uso del motor

```python
from risk import Game, GameMap
g = Game(GameMap.load("maps/classic/map.json"), ["ana", "beto", "carla"])
g.place(g.current, "alaska")          # setup / refuerzo
g.attack("ana", "alaska", "kamchatka", dice=3)
g.move_in("ana", 3)                   # tras conquistar
g.end_attack("ana"); g.fortify("ana", "alaska", "alberta", 2)
g.to_dict(viewer="ana")               # estado para el cliente
```
