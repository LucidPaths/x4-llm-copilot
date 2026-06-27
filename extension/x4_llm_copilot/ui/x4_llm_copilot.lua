-- X4 LLM Copilot UI bootstrap.
-- Keep this tiny until the live telemetry fields are confirmed in-game.

local L = {}

function L.Init()
    DebugError("X4 LLM Copilot UI bootstrap loaded")
end

Register_Require_Response("extensions.x4_llm_copilot.ui.x4_llm_copilot", L)
return L
