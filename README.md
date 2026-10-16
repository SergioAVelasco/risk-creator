# Risk Creator

Risk clásico con mapas personalizables. El motor de reglas es independiente del mapa.

## Jugar

```bash
python3 -m pip install --user -r requirements.txt   # una vez
python3 -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Abre `http://localhost:8000`, crea una sala y comparte el enlace (o el código de 4
letras). Cada jugador entra desde su navegador; 2 a 6 jugadores. Con 2 jugadores entra un bando
neutral (regla oficial): 14 territorios y 40 ejércitos repartidos al azar; defiende con
dados pero no juega turnos ni roba cartas.

La preparación es **simultánea y oculta**: todos colocan a la vez, por rondas (el
anfitrión elige el tamaño: 5, 10 por defecto, 20 o todo de una vez). Nadie ve lo que
colocan los demás hasta que termina la fase; al colocar tu cuota quedas en espera y la
siguiente ronda se abre cuando todos terminaron. En red local, usa la
IP de la máquina en vez de `localhost`.

## Reglas de rondas simultáneas (por defecto)

Pensadas para eliminar el tiempo muerto: nadie espera su turno. Cada ronda:

1. **Planificación** (temporizador + botón *Terminar turno*): todos colocan refuerzos y
   declaran ataques en secreto (`origen → destino × n`; los `n` ejércitos salen del
   territorio, siempre queda 1 en casa; los refuerzos deben estar colocados antes de
   declarar). Si se acaba el tiempo, el jugador queda listo tal cual (lo no colocado
   se acumula).
2. **Batallas**: se revelan y se tiran todas a la vez, tirada a tirada (cada 5 s o
   cuando todos los implicados pulsan *Continuar*). Antes de cada tirada puedes
   *retirarte* de cualquiera de tus batallas.
   - **Campo abierto**: si A ataca a B y B ataca a A, ambos tiran como atacantes y el
     empate cuesta una baja a cada uno. El ganador sigue después contra el territorio
     del perdedor (**ciudad**, reglas normales: el defensor gana los empates).
   - **Retirada**: cuesta un último tiro en el que quien se retira no elimina nada pero
     sí puede perder unidades; si ambos se retiran no hay tiro. Los supervivientes
     vuelven a su origen; si el origen fue tomado, no hay retirada.
   - Un territorio atacado por varios frentes reparte su guarnición entre ellos
     (dados = su parte, mín. 1, máx. 2). Si cae, lo ocupa el atacante que conserve más
     ejércitos; los demás siguen la batalla contra él (o se unen, si son del mismo dueño).
3. **Cartas**: al terminar *todas* las batallas se reparten las cartas de conquista
   (una por jugador que conquistó) y las de los eliminados pasan a quien los eliminó.
4. **Fortificar**: un movimiento por jugador, simultáneo y con temporizador.

El anfitrión elige reglas (`Clásicas por turnos` sigue disponible) y tiempo de planificación.
Código: `risk/rounds.py`; los plazos los lleva el servidor (`server/rooms.py`).

## Estructura

- `risk/` — motor (Python, sin dependencias)
  - `gamemap.py` carga y valida mapas · `game.py` reglas clásicas · `rounds.py` rondas simultáneas · `cards.py` cartas
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

## Jugar por internet (túnel)

```bash
tools/play_online.sh        # puerto 8000 por defecto
```

Levanta el servidor y abre un túnel público (`cloudflared` si está instalado, si no
`localhost.run` por ssh). Pasa a tu amigo la URL `https://...` que se imprime; la sala
y el enlace para compartir funcionan igual por ese dominio.
