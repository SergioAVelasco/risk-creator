from __future__ import annotations

from dataclasses import dataclass

SYMBOLS = ("infantry", "cavalry", "artillery")
WILD = "wild"


@dataclass(frozen=True)
class Card:
    symbol: str
    territory: str | None = None  # None for wild cards


def build_deck(territory_ids: list[str], wilds: int = 2) -> list[Card]:
    deck = [Card(SYMBOLS[i % 3], t) for i, t in enumerate(territory_ids)]
    deck += [Card(WILD) for _ in range(wilds)]
    return deck


def is_valid_set(cards: list[Card]) -> bool:
    if len(cards) != 3:
        return False
    symbols = [c.symbol for c in cards if c.symbol != WILD]
    if len(symbols) < 3:  # any wild completes the set
        return True
    return len(set(symbols)) in (1, 3)


def set_value(sets_traded: int) -> int:
    """Armies for the (sets_traded+1)-th set traded in the game."""
    table = [4, 6, 8, 10, 12, 15]
    if sets_traded < len(table):
        return table[sets_traded]
    return 15 + 5 * (sets_traded - len(table) + 1)
