"""Map definition: a graph of territories grouped into continents.

The engine only ever sees this graph. Visual shape (SVG) is optional and
purely presentational: each territory may name an element id in `svg`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class MapError(ValueError):
    """The map file is malformed or inconsistent."""


@dataclass(frozen=True)
class Territory:
    id: str
    name: str
    continent: str
    neighbors: frozenset[str]


@dataclass(frozen=True)
class Continent:
    id: str
    name: str
    bonus: int
    territories: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class GameMap:
    name: str
    territories: dict[str, Territory]
    continents: dict[str, Continent]
    svg: str | None = None  # path to SVG, relative to the map file

    @classmethod
    def from_dict(cls, data: dict) -> "GameMap":
        try:
            raw_conts = data["continents"]
            raw_terrs = data["territories"]
        except KeyError as e:
            raise MapError(f"missing key {e}") from None

        members: dict[str, set[str]] = {c: set() for c in raw_conts}
        territories: dict[str, Territory] = {}
        for tid, t in raw_terrs.items():
            cont = t.get("continent")
            if cont not in raw_conts:
                raise MapError(f"{tid}: unknown continent {cont!r}")
            members[cont].add(tid)
            territories[tid] = Territory(
                id=tid,
                name=t.get("name", tid),
                continent=cont,
                neighbors=frozenset(t.get("neighbors", [])),
            )
        continents = {
            cid: Continent(cid, c.get("name", cid), int(c["bonus"]), frozenset(members[cid]))
            for cid, c in raw_conts.items()
        }
        gm = cls(data.get("name", "Unnamed"), territories, continents, data.get("svg"))
        gm.validate()
        return gm

    @classmethod
    def load(cls, path: str | Path) -> "GameMap":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def validate(self) -> None:
        if len(self.territories) < 4:
            raise MapError("a map needs at least 4 territories")
        for t in self.territories.values():
            for n in t.neighbors:
                if n not in self.territories:
                    raise MapError(f"{t.id}: unknown neighbor {n!r}")
                if n == t.id:
                    raise MapError(f"{t.id}: territory is its own neighbor")
                if t.id not in self.territories[n].neighbors:
                    raise MapError(f"adjacency not symmetric: {t.id} -> {n}")
        for c in self.continents.values():
            if not c.territories:
                raise MapError(f"continent {c.id} has no territories")
        # the whole graph must be connected
        start = next(iter(self.territories))
        seen, stack = {start}, [start]
        while stack:
            for n in self.territories[stack.pop()].neighbors:
                if n not in seen:
                    seen.add(n)
                    stack.append(n)
        if len(seen) != len(self.territories):
            raise MapError("map graph is not connected")
