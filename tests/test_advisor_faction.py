from __future__ import annotations

from x4_copilot.advisor import GroundedAdvisor
from x4_copilot.models import AmbientContext, TelemetryPayload


def test_faction_advisor_summarizes_standings_without_dumping_raw_dict() -> None:
    payload = TelemetryPayload(
        intent="faction_state",
        ambient=AmbientContext(sector="Windfall I"),
        data=[
            {
                "kind": "faction_standing",
                "faction_name": "Antigone Republic",
                "standing": 0,
                "relation_name": "Neutral",
                "rank_title": "Citizen of the Republic",
                "raw": {"licences_raw": [{"name": f"licence-{index}"} for index in range(40)]},
            },
            {
                "kind": "faction_standing",
                "faction_name": "Teladi Company",
                "standing": 5,
                "relation_name": "Friend",
            },
        ],
    )

    answer = GroundedAdvisor().answer("faction status", payload)

    assert "Antigone Republic: 0 (Neutral), rank Citizen of the Republic" in answer
    assert "Teladi Company: 5 (Friend)" in answer
    assert "licences_raw" not in answer
    assert len(answer) < 400
