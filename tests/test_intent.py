from x4_copilot.intent import classify


def test_trade_query_routes_to_trade():
    result = classify("what are goods selling for in this system")
    assert result.intent == "trade_in_sector"
    assert result.confidence > 0


def test_war_query_routes_to_faction_state():
    assert classify("where is the war and who is losing").intent == "faction_state"


def test_unknown_query_stays_unknown():
    assert classify("sing me a sea shanty").intent == "unknown"
