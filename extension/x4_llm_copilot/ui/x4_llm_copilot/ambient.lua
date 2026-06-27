local ffi = require("ffi")
local C = ffi.C

ffi.cdef[[
    typedef uint64_t UniverseID;
    UniverseID GetPlayerID(void);
    UniverseID GetPlayerOccupiedShipID(void);
]]

local L = {}

local function escape_json_string(value)
    if value == nil then
        return ""
    end
    value = tostring(value)
    value = string.gsub(value, "\\", "\\\\")
    value = string.gsub(value, '"', '\\"')
    value = string.gsub(value, "\n", "\\n")
    value = string.gsub(value, "\r", "\\r")
    value = string.gsub(value, "\t", "\\t")
    return value
end

local function json_string(value)
    return '"' .. escape_json_string(value) .. '"'
end

local function json_number_or_null(value)
    if value == nil then
        return "null"
    end
    local number = tonumber(value)
    if number == nil then
        return "null"
    end
    return tostring(number)
end

local function json_raw_string_or_null(value)
    if value == nil then
        return "null"
    end
    return json_string(value)
end

local function emit_ambient()
    local ok, payload = pcall(function()
        local player_id = C.GetPlayerID()
        local ship_id = C.GetPlayerOccupiedShipID()
        local ship64 = ConvertStringTo64Bit(tostring(ship_id))
        local name, sector, hullpercent, shieldpercent = GetComponentData(ship64, "name", "sector", "hullpercent", "shieldpercent")

        return "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"ambient_context",'
            .. '"source":"x4_lua_live",'
            .. '"schema":"ambient_probe_v1",'
            .. '"player_id":' .. json_string(tostring(player_id)) .. ','
            .. '"ship_id":' .. json_string(tostring(ship_id)) .. ','
            .. '"ship_name":' .. json_raw_string_or_null(name) .. ','
            .. '"sector_raw":' .. json_raw_string_or_null(sector) .. ','
            .. '"hullpercent":' .. json_number_or_null(hullpercent) .. ','
            .. '"shieldpercent":' .. json_number_or_null(shieldpercent)
            .. "}"
    end)

    if not ok then
        payload = "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"ambient_context",'
            .. '"source":"x4_lua_live",'
            .. '"schema":"ambient_probe_v1",'
            .. '"error":' .. json_string(payload)
            .. "}"
    end

    DebugError("X4 LLM Copilot Lua ambient payload: " .. payload)
    AddUITriggeredEvent("X4LLMCopilot", "AmbientRaw", payload)
end

function L.Init()
    DebugError("X4 LLM Copilot Lua ambient module initialized")
    emit_ambient()
end

Register_OnLoad_Init(L.Init, "extensions.x4_llm_copilot.ui.x4_llm_copilot.ambient")
Register_Require_Response("extensions.x4_llm_copilot.ui.x4_llm_copilot.ambient", L)

return L
