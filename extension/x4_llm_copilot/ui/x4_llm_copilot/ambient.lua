local ffi = require("ffi")
local C = ffi.C

ffi.cdef[[
    typedef uint64_t UniverseID;
    UniverseID GetContextByClass(UniverseID componentid, const char* classname, bool includeself);
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

local function is_array_table(value)
    local count = 0
    local max_index = 0
    for key, _ in pairs(value) do
        if type(key) ~= "number" or key < 1 or key % 1 ~= 0 then
            return false
        end
        count = count + 1
        if key > max_index then
            max_index = key
        end
    end
    return count == max_index
end

local function json_value(value, depth)
    depth = depth or 0
    if value == nil then
        return "null"
    end
    local value_type = type(value)
    if value_type == "number" then
        return tostring(value)
    end
    if value_type == "boolean" then
        return tostring(value)
    end
    if value_type == "table" then
        if depth >= 3 then
            return json_string(tostring(value))
        end
        local parts = {}
        if is_array_table(value) then
            for index = 1, #value do
                table.insert(parts, json_value(value[index], depth + 1))
            end
            return "[" .. table.concat(parts, ",") .. "]"
        end
        for key, item in pairs(value) do
            table.insert(parts, json_string(tostring(key)) .. ":" .. json_value(item, depth + 1))
        end
        return "{" .. table.concat(parts, ",") .. "}"
    end
    return json_string(value)
end

local function emit_ambient(trigger)
    trigger = trigger or "unspecified"
    local ok, payload = pcall(function()
        local player_id = C.GetPlayerID()
        local ship_id = C.GetPlayerOccupiedShipID()
        local ship64 = ConvertStringTo64Bit(tostring(ship_id))
        local name, sector, hullpercent, shieldpercent, cargo = GetComponentData(ship64, "name", "sector", "hullpercent", "shieldpercent", "cargo")
        local player_money = GetPlayerMoney()

        return "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"ambient_context",'
            .. '"source":"x4_lua_live",'
            .. '"schema":"ambient_probe_v2",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"player_id":' .. json_string(tostring(player_id)) .. ','
            .. '"ship_id":' .. json_string(tostring(ship_id)) .. ','
            .. '"ship_name":' .. json_raw_string_or_null(name) .. ','
            .. '"sector_raw":' .. json_raw_string_or_null(sector) .. ','
            .. '"player_money":' .. json_number_or_null(player_money) .. ','
            .. '"cargo_raw":' .. json_value(cargo) .. ','
            .. '"hullpercent":' .. json_number_or_null(hullpercent) .. ','
            .. '"shieldpercent":' .. json_number_or_null(shieldpercent)
            .. "}"
    end)

    if not ok then
        payload = "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"ambient_context",'
            .. '"source":"x4_lua_live",'
            .. '"schema":"ambient_probe_v2",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"error":' .. json_string(payload)
            .. "}"
    end

    DebugError("X4 LLM Copilot Lua ambient payload: " .. payload)
    AddUITriggeredEvent("X4LLMCopilot", "AmbientRaw", payload)
end

local function emit_trade(trigger)
    trigger = trigger or "unspecified"
    local ok, payload = pcall(function()
        local player_id = C.GetPlayerID()
        local ship_id = C.GetPlayerOccupiedShipID()
        local ship64 = ConvertStringTo64Bit(tostring(ship_id))
        local ship_name, sector, isdocked = GetComponentData(ship64, "name", "sector", "isdocked")
        local player_money = GetPlayerMoney()
        local container64 = nil
        local container_name = nil
        local container_id = nil
        local offers = {}
        local nontradeoffers = {}

        if ship_id ~= 0 and isdocked then
            container64 = C.GetContextByClass(ship_id, "container", false)
            if container64 ~= 0 then
                container_id = ConvertStringToLuaID(tostring(container64))
                container_name = GetComponentData(container64, "name")
                offers = GetTradeList(container_id, ConvertStringToLuaID(tostring(ship_id))) or {}
                nontradeoffers = GetTradeList(container_id, ConvertStringToLuaID(tostring(ship_id)), false) or {}
            end
        end

        return "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"trade_in_sector",'
            .. '"source":"x4_lua_live",'
            .. '"schema":"trade_offers_probe_v1",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"player_id":' .. json_string(tostring(player_id)) .. ','
            .. '"ship_id":' .. json_string(tostring(ship_id)) .. ','
            .. '"ship_name":' .. json_raw_string_or_null(ship_name) .. ','
            .. '"sector_raw":' .. json_raw_string_or_null(sector) .. ','
            .. '"player_money":' .. json_number_or_null(player_money) .. ','
            .. '"docked":' .. tostring(isdocked == true) .. ','
            .. '"trade_container_id":' .. json_raw_string_or_null(container_id and tostring(container_id) or nil) .. ','
            .. '"trade_container_name":' .. json_raw_string_or_null(container_name) .. ','
            .. '"offers_raw":' .. json_value(offers) .. ','
            .. '"nontrade_offers_raw":' .. json_value(nontradeoffers)
            .. "}"
    end)

    if not ok then
        payload = "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"trade_in_sector",'
            .. '"source":"x4_lua_live",'
            .. '"schema":"trade_offers_probe_v1",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"error":' .. json_string(payload)
            .. "}"
    end

    DebugError("X4 LLM Copilot Lua trade payload: " .. payload)
    AddUITriggeredEvent("X4LLMCopilot", "AmbientRaw", payload)
end

local function request_intent(request_json)
    request_json = tostring(request_json or "")
    local intent = string.match(request_json, '"intent"%s*:%s*"([^"]+)"')
    return intent or "ambient_context"
end

function L.Init()
    DebugError("X4 LLM Copilot Lua ambient module initialized")
    RegisterEvent("x4LLMCopilotFetchAmbient", function(_, request_json)
        local intent = request_intent(request_json)
        if intent == "trade_in_sector" then
            emit_trade("fetch_response")
        else
            emit_ambient("fetch_response")
        end
    end)
end

Register_OnLoad_Init(L.Init, "extensions.x4_llm_copilot.ui.x4_llm_copilot.ambient")
Register_Require_Response("extensions.x4_llm_copilot.ui.x4_llm_copilot.ambient", L)

return L
