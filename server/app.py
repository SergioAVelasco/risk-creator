"""FastAPI app: REST for rooms/maps, one WebSocket per player, static client.

Run: python3 -m uvicorn server.app:app --reload
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from risk import RuleError

from .rooms import MAPS_DIR, Rooms, list_maps

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Risk Creator")
rooms = Rooms()
sockets: dict[str, dict[str, WebSocket]] = {}  # room code -> pid -> ws


class CreateRoom(BaseModel):
    map: str = "classic"


@app.get("/api/maps")
def api_maps():
    return list_maps()


@app.post("/api/rooms")
def api_create_room(body: CreateRoom):
    try:
        room = rooms.create(body.map)
    except RuleError as e:
        raise HTTPException(400, str(e))
    return {"code": room.code}


@app.get("/api/rooms/{code}")
def api_room(code: str):
    try:
        return rooms.get(code).lobby_view()
    except RuleError as e:
        raise HTTPException(404, str(e))


async def broadcast(code: str) -> None:
    room = rooms.rooms[code]
    for pid, ws in list(sockets.get(code, {}).items()):
        try:
            await ws.send_text(json.dumps({"type": "state", **room.view_for(pid)}))
        except Exception:
            sockets[code].pop(pid, None)
            room.members[pid].connected = False


@app.websocket("/ws/{code}")
async def ws_endpoint(ws: WebSocket, code: str, name: str = "", token: str = ""):
    await ws.accept()
    try:
        room = rooms.get(code)
        member = room.join(name, token or None)
    except RuleError as e:
        await ws.send_text(json.dumps({"type": "fatal", "message": str(e)}))
        await ws.close()
        return
    code = room.code
    sockets.setdefault(code, {})[member.pid] = ws
    member.connected = True
    await ws.send_text(json.dumps({"type": "welcome", "pid": member.pid, "token": member.token}))
    await broadcast(code)
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            try:
                if msg.get("type") == "start":
                    room.start(member.pid)
                elif msg.get("type") == "settings":
                    room.set_setup_batch(member.pid, int(msg.get("setup_batch")))
                else:
                    room.act(member.pid, msg)
            except (RuleError, KeyError, ValueError, TypeError) as e:
                await ws.send_text(json.dumps({"type": "error", "message": str(e) or "invalid action"}))
                continue
            await broadcast(code)
    except WebSocketDisconnect:
        pass
    finally:
        if sockets.get(code, {}).get(member.pid) is ws:
            sockets[code].pop(member.pid, None)
            member.connected = False
            await broadcast(code)


app.mount("/maps", StaticFiles(directory=MAPS_DIR), name="maps")
app.mount("/static", StaticFiles(directory=ROOT / "client"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "client" / "index.html")
