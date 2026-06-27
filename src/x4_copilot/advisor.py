from __future__ import annotations

from .models import PayloadError, TelemetryPayload


class GroundedAdvisor:
    """Deterministic fallback advisor used for tests, smoke checks, and provider outages."""

    def answer(self, question: str, payload: TelemetryPayload) -> str:
        if payload.intent == "trade_in_sector":
            return self._trade_answer(payload)
        if payload.intent == "ship_status":
            return self._ship_answer(payload)
        if payload.intent == "faction_state":
            return self._faction_answer(payload)
        if payload.intent == "sector_objects":
            return self._objects_answer(payload)
        return f"I have {payload.ambient.label()}, but no scoped data for that query."

    def _trade_answer(self, payload: TelemetryPayload) -> str:
        try:
            offers = payload.trade_offers()
        except PayloadError as exc:
            return f"Trade telemetry is malformed: {exc}."
        if not offers:
            sector = payload.ambient.sector or "this sector"
            return f"No trade offers visible in {sector}. Scanner data is empty, so I won't invent prices."
        ranked = sorted(
            offers,
            key=lambda offer: (
                offer.spread is not None,
                offer.spread if offer.spread is not None else -10**9,
                -(offer.dist_km if offer.dist_km is not None else 10**9),
            ),
            reverse=True,
        )
        best = ranked[0]
        price_bits = []
        if best.buy is not None:
            price_bits.append(f"buy {best.buy}{best.unit}")
        if best.sell is not None:
            price_bits.append(f"sell {best.sell}{best.unit}")
        if best.spread is not None:
            price_bits.append(f"spread {best.spread}{best.unit}")
        dist = f", {best.dist_km:g} km out" if best.dist_km is not None else ""
        stock = f", stock {best.stock}" if best.stock is not None else ""
        return f"Best visible trade: {best.ware} at {best.station} ({', '.join(price_bits)}{dist}{stock})."

    def _ship_answer(self, payload: TelemetryPayload) -> str:
        if not payload.data:
            return f"{payload.ambient.label()}; no detailed ship telemetry returned."
        item = payload.data[0]
        parts = [payload.ambient.label()]
        for key in ("hull", "shield", "cargo", "fuel"):
            if key in item:
                parts.append(f"{key}={item[key]}")
        return "; ".join(parts) + "."

    def _faction_answer(self, payload: TelemetryPayload) -> str:
        if not payload.data:
            return "No faction deltas/events in the current telemetry window."
        event = payload.data[0]
        summary = event.get("summary") or event.get("event") or str(event)
        return f"Latest political signal: {summary}"

    def _objects_answer(self, payload: TelemetryPayload) -> str:
        if not payload.data:
            return f"No nearby sector objects reported around {payload.ambient.sector or 'current sector'}."
        first = payload.data[0]
        name = first.get("name") or first.get("id") or "unknown object"
        kind = first.get("type", "object")
        dist = f" at {first['dist_km']} km" if "dist_km" in first else ""
        return f"Nearest reported {kind}: {name}{dist}."
