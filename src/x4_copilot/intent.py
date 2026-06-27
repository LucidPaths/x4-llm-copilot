from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Intent

_WORD_RE = re.compile(r"[a-z0-9_]+")
_KEYWORDS: list[tuple[Intent, tuple[str, ...]]] = [
    ("trade_in_sector", ("trade", "trading", "selling", "sell", "buy", "price", "prices", "profit", "run", "ware", "wares", "goods")),
    ("faction_state", ("war", "faction", "factions", "relation", "relations", "xenon", "argon", "paranid", "teladi", "losing", "politics", "political", "news")),
    ("ship_status", ("ship", "hull", "shield", "shields", "cargo", "credits", "fuel", "status", "damage", "damaged")),
    ("sector_objects", ("where", "near", "nearby", "station", "stations", "gate", "gates", "lockbox", "lockboxes", "wreck", "wrecks", "object", "objects", "target", "around")),
]


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float
    matched: tuple[str, ...]


def classify(text: str) -> IntentResult:
    tokens = set(_WORD_RE.findall(text.lower()))
    scored: list[tuple[int, Intent, list[str]]] = []
    for intent, words in _KEYWORDS:
        matched = [word for word in words if word in tokens]
        if matched:
            scored.append((len(matched), intent, matched))
    if not scored:
        return IntentResult("unknown", 0.0, ())
    scored.sort(key=lambda item: item[0], reverse=True)
    score, intent, matched = scored[0]
    return IntentResult(intent, min(0.9, 0.35 + score * 0.18), tuple(matched))
