from __future__ import annotations

from dataclasses import dataclass

from .models import Intent

_KEYWORDS: list[tuple[Intent, tuple[str, ...]]] = [
    ("trade_in_sector", ("trade", "selling", "sell", "buy", "price", "profit", "run", "ware", "goods")),
    ("faction_state", ("war", "faction", "relation", "xenon", "argon", "paranid", "teladi", "split", "losing", "politic", "news")),
    ("ship_status", ("ship", "hull", "shield", "cargo", "credits", "fuel", "status", "damage")),
    ("sector_objects", ("where", "near", "nearby", "station", "gate", "lockbox", "wreck", "object", "target", "around")),
]


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float
    matched: tuple[str, ...]


def classify(text: str) -> IntentResult:
    haystack = f" {text.lower()} "
    scored: list[tuple[int, Intent, list[str]]] = []
    for intent, words in _KEYWORDS:
        matched = [word for word in words if word in haystack]
        if matched:
            scored.append((len(matched), intent, matched))
    if not scored:
        return IntentResult("unknown", 0.0, ())
    scored.sort(key=lambda item: item[0], reverse=True)
    score, intent, matched = scored[0]
    return IntentResult(intent, min(0.95, 0.35 + score * 0.2), tuple(matched))
