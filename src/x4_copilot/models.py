from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, get_args

Intent = Literal["ambient_context", "trade_in_sector", "faction_state", "ship_status", "sector_objects", "unknown"]
VALID_INTENTS = frozenset(get_args(Intent))


class PayloadError(ValueError):
    """Raised when an adapter payload is malformed."""


@dataclass(frozen=True)
class AmbientContext:
    sector: str | None = None
    pos: tuple[float, float, float] | None = None
    credits: int | None = None
    ship: str | None = None
    target: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AmbientContext:
        pos = raw.get("pos")
        parsed_pos: tuple[float, float, float] | None = None
        if pos is not None:
            if not isinstance(pos, list | tuple) or len(pos) != 3:
                raise PayloadError("ambient.pos must be a 3-item list")
            parsed_pos = (float(pos[0]), float(pos[1]), float(pos[2]))
        return cls(
            sector=_optional_str(raw.get("sector")),
            pos=parsed_pos,
            credits=_optional_int(raw.get("credits"), "ambient.credits"),
            ship=_optional_str(raw.get("ship")),
            target=_optional_str(raw.get("target")),
        )

    def label(self) -> str:
        bits = []
        if self.sector:
            bits.append(f"sector={self.sector}")
        if self.ship:
            bits.append(f"ship={self.ship}")
        if self.credits is not None:
            bits.append(f"credits={self.credits}cr")
        if self.target:
            bits.append(f"target={self.target}")
        return ", ".join(bits) if bits else "ambient context unavailable"


@dataclass(frozen=True)
class TradeOffer:
    ware: str
    station: str
    unit: str = "cr/u"
    buy: int | None = None
    sell: int | None = None
    dist_km: float | None = None
    stock: int | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TradeOffer:
        return cls(
            ware=_required_str(raw, "ware"),
            station=_required_str(raw, "station"),
            unit=str(raw.get("unit", "cr/u")),
            buy=_optional_int(raw.get("buy"), "buy"),
            sell=_optional_int(raw.get("sell"), "sell"),
            dist_km=_optional_float(raw.get("dist_km"), "dist_km"),
            stock=_optional_int(raw.get("stock"), "stock"),
        )

    @property
    def spread(self) -> int | None:
        if self.sell is None or self.buy is None:
            return None
        return self.sell - self.buy


@dataclass(frozen=True)
class TelemetryPayload:
    intent: Intent
    ambient: AmbientContext
    data: list[dict[str, Any]] = field(default_factory=list)
    as_of: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any], default_intent: Intent = "unknown") -> TelemetryPayload:
        if not isinstance(raw, dict):
            raise PayloadError("payload must be an object")
        ambient_raw = raw.get("ambient") or {}
        if not isinstance(ambient_raw, dict):
            raise PayloadError("ambient must be an object")
        data = raw.get("data", [])
        if not isinstance(data, list):
            raise PayloadError("data must be a list")
        intent = raw.get("intent", default_intent)
        if intent not in VALID_INTENTS:
            intent = default_intent
        return cls(
            intent=intent,
            ambient=AmbientContext.from_dict(ambient_raw),
            data=[_require_dict(item, "data item") for item in data],
            as_of=_optional_str(raw.get("as_of")),
        )

    def trade_offers(self) -> list[TradeOffer]:
        return [TradeOffer.from_dict(item) for item in self.data]


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PayloadError(f"{label} must be an object")
    return value


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if value is None or str(value).strip() == "":
        raise PayloadError(f"{key} is required")
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PayloadError(f"{label} must be an integer") from exc


def _optional_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise PayloadError(f"{label} must be a number") from exc
