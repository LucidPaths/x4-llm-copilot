local ffi = require("ffi")
local C = ffi.C

ffi.cdef[[
    typedef uint64_t UniverseID;
    typedef struct {
        float x;
        float y;
        float z;
    } Coord3D;
    const char* GetComponentClass(UniverseID componentid);
    UniverseID GetContextByClass(UniverseID componentid, const char* classname, bool includeself);
    UniverseID GetPlayerID(void);
    UniverseID GetPlayerOccupiedShipID(void);
    Coord3D GetObjectPositionInSector(UniverseID componentid);
    typedef uint64_t OperationID;
    typedef struct {
        const char* name;
        const char* colorid;
    } RelationRangeInfo;
    typedef struct {
        const char* id;
        const char* name;
        const char* desc;
        const char* shortdesc;
        const char* iconid;
        const char* imageid;
        double duration;
        uint32_t numoptions;
    } DiplomacyEventInfo;
    typedef struct {
        OperationID id;
        OperationID sourceactionoperationid;
        const char* eventid;
        UniverseID agentid;
        const char* agentname;
        const char* agentimageid;
        const char* agentresultstate;
        int32_t agentexp_negotiation;
        int32_t agentexp_espionage;
        const char* faction;
        const char* otherfaction;
        const char* option;
        const char* outcome;
        double starttime;
        bool read;
        int32_t startrelation;
    } DiplomacyEventOperation;
    const char* GetComponentName(UniverseID componentid);
    double GetCurrentGameTime(void);
    RelationRangeInfo GetUIRelationName(const char* fromfactionid, const char* tofactionid);
    uint32_t GetDiplomacyEvents(DiplomacyEventInfo* result, uint32_t resultlen);
    uint32_t GetNumDiplomacyEvents();
    uint32_t GetDiplomacyEventOperations(DiplomacyEventOperation* result, uint32_t resultlen, bool active);
    uint32_t GetNumDiplomacyEventOperations(bool active);
    bool IsComponentClass(UniverseID componentid, const char* classname);
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
        if depth >= 5 then
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

local STATION_CAP = 32
local OFFERS_PER_STATION_CAP = 20
local TOTAL_OFFER_CAP = 200
local SECTOR_OBJECT_TOTAL_CAP = 160
local SECTOR_OBJECT_KIND_CAPS = { station = 64, gate = 16, ship = 40, collectable = 32, wreck = 32 }

local function request_scope(request_json)
    request_json = tostring(request_json or "")
    local scope = string.match(request_json, '"scope"%s*:%s*"([^"]+)"')
    if scope ~= nil then
        return scope
    end
    if string.match(request_json, '"radar_only"%s*:%s*true') then
        return "radar_range"
    end
    return "docked_station"
end

local function component_position(component64)
    local ok, pos = pcall(function()
        return C.GetObjectPositionInSector(component64)
    end)
    if ok and pos ~= nil then
        return { x = tonumber(pos.x), y = tonumber(pos.y), z = tonumber(pos.z), source = "C.GetObjectPositionInSector" }
    end
    ok, pos = pcall(function()
        return GetComponentData(component64, "position")
    end)
    if ok and type(pos) == "table" then
        return { x = tonumber(pos.x), y = tonumber(pos.y), z = tonumber(pos.z), source = "GetComponentData(position)" }
    end
    return nil
end

local function distance_between(a, b)
    if a == nil or b == nil or a.x == nil or a.y == nil or a.z == nil or b.x == nil or b.y == nil or b.z == nil then
        return nil
    end
    local dx = a.x - b.x
    local dy = a.y - b.y
    local dz = a.z - b.z
    return math.sqrt((dx * dx) + (dy * dy) + (dz * dz))
end

local function request_kinds(request_json)
    request_json = tostring(request_json or "")
    local allowed = {}
    local saw = false
    local array_text = string.match(request_json, '"kinds"%s*:%s*%[(.-)%]')
    if array_text ~= nil then
        for kind in string.gmatch(array_text, '"([^"]+)"') do
            kind = string.lower(kind)
            if kind == "loot" or kind == "lockbox" then
                kind = "collectable"
            end
            allowed[kind] = true
            saw = true
        end
    end
    if not saw then
        allowed.station = true
        allowed.gate = true
        allowed.ship = true
        allowed.collectable = true
        allowed.wreck = true
    end
    return allowed
end

local function kind_allowed(allowed, kind)
    return allowed[kind] == true
end

local function is_component_class(component64, classname)
    local ok, result = pcall(function() return C.IsComponentClass(component64, classname) end)
    return ok and result == true
end

local function component_class(component64)
    local ok, value = pcall(function() return C.GetComponentClass(component64) end)
    if ok and value ~= nil then
        return ffi.string(value)
    end
    return nil
end

local function classify_sector_object(object64, classid, realclassid, iswreck)
    classid = tostring(classid or "")
    realclassid = tostring(realclassid or "")
    if iswreck == true then
        return "wreck"
    end
    if classid == "station" or realclassid == "station" or is_component_class(object64, "station") then
        return "station"
    end
    if classid == "gate" or realclassid == "gate" or is_component_class(object64, "gate") then
        return "gate"
    end
    if classid == "lockbox" or classid == "collectablewares" or is_component_class(object64, "lockbox") or is_component_class(object64, "collectablewares") then
        return "collectable"
    end
    if classid == "ship" or realclassid == "ship" or is_component_class(object64, "ship") then
        return "ship"
    end
    return nil
end

local function append_sector_object(objects, seen, counts, allowed, object64, ship_pos, sector64, forced_kind)
    if object64 == nil or object64 == 0 then
        return false
    end
    local object_key = tostring(object64)
    if seen[object_key] then
        return false
    end
    local name, classid, realclassid, owner, factionname, idcode, isradarvisible, isplayerowned, isdocked, ismasstraffic, iswreck = GetComponentData(object64, "name", "classid", "realclassid", "owner", "factionname", "idcode", "isradarvisible", "isplayerowned", "isdocked", "ismasstraffic", "iswreck")
    local kind = forced_kind or classify_sector_object(object64, classid, realclassid, iswreck)
    if kind == nil or not kind_allowed(allowed, kind) then
        return false
    end
    if counts.total >= SECTOR_OBJECT_TOTAL_CAP or (counts[kind] or 0) >= (SECTOR_OBJECT_KIND_CAPS[kind] or 0) then
        return false
    end
    if kind == "ship" and not isplayerowned then
        local notable_name = name ~= nil and tostring(name) ~= "" and not string.match(string.lower(tostring(name)), "^%s*.+%s+%([a-z]+%)%s*$")
        if ismasstraffic == true or isdocked == true or not notable_name then
            return false
        end
    end
    local object_pos = component_position(object64)
    local distance_m = distance_between(ship_pos, object_pos)
    local distance_km = distance_m and (distance_m / 1000) or nil
    if distance_km == nil then
        return false
    end
    seen[object_key] = true
    counts.total = counts.total + 1
    counts[kind] = (counts[kind] or 0) + 1
    table.insert(objects, {
        id = object_key,
        name = name,
        type = kind,
        class = classid,
        realclass = realclassid,
        component_class = component_class(object64),
        idcode = idcode,
        owner = owner,
        faction = factionname,
        sectorid = tostring(sector64),
        distance_m = distance_m,
        distance_km = distance_km,
        distance_source = object_pos and object_pos.source or nil,
        isradarvisible = isradarvisible,
        isplayerowned = isplayerowned,
        iswreck = iswreck,
        raw = {
            id = object_key,
            name = name,
            classid = classid,
            realclassid = realclassid,
            owner = owner,
            factionname = factionname,
            idcode = idcode,
            isradarvisible = isradarvisible,
            isplayerowned = isplayerowned,
            isdocked = isdocked,
            ismasstraffic = ismasstraffic,
            iswreck = iswreck,
            position = object_pos
        }
    })
    return true
end

local function append_component_ids(target, source, values, forced_kind)
    local count = 0
    for _, value in ipairs(values or {}) do
        table.insert(target, { id = value, source = source, forced_kind = forced_kind })
        count = count + 1
    end
    return count
end

local function try_contained_component_source(target, scan_errors, source, forced_kind, func)
    local ok, values = pcall(func)
    if ok and type(values) == "table" then
        return append_component_ids(target, source, values, forced_kind)
    end
    table.insert(scan_errors, { source = source, error = tostring(values) })
    return 0
end

local function contained_objects(sector64, scan_errors)
    local objects = {}
    local source_counts = {}
    source_counts.gates = try_contained_component_source(objects, scan_errors, "GetGates(sector)", "gate", function() return GetGates(sector64) end)
    source_counts.ships = try_contained_component_source(objects, scan_errors, "GetContainedShips(sector,true)", "ship", function() return GetContainedShips(sector64, true) end)
    if source_counts.ships == 0 then
        source_counts.ships = try_contained_component_source(objects, scan_errors, "GetContainedShips(sector)", "ship", function() return GetContainedShips(sector64) end)
    end
    -- No global GetContainedObjects() exists in the live Lua environment. Keep this
    -- source list to verified APIs only; collectable/wreck widening needs a new
    -- observed live read rather than a noisy nonexistent function probe.
    return objects, source_counts
end

local function limited_offers(offers, station64, station_name, sector64, distance_m, distance_km)
    local result = {}
    local count = 0
    for _, offer in ipairs(offers or {}) do
        if count >= OFFERS_PER_STATION_CAP then
            break
        end
        count = count + 1
        if type(offer) == "table" then
            offer.station = offer.station or tostring(station64)
            offer.stationname = offer.stationname or station_name
            offer.stationsectorid = offer.stationsectorid or tostring(sector64)
            offer.distance_m = distance_m
            offer.distance_km = distance_km
        end
        table.insert(result, offer)
    end
    return result, count
end

local function emit_trade_docked(trigger)
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

local function emit_trade_radar(trigger)
    trigger = trigger or "unspecified"
    local ok, payload = pcall(function()
        local player_id = C.GetPlayerID()
        local ship_id = C.GetPlayerOccupiedShipID()
        local ship64 = ConvertStringTo64Bit(tostring(ship_id))
        local ship_name, sector, sector_id, radar_range_m = GetComponentData(ship64, "name", "sector", "sectorid", "maxradarrange")
        local player_money = GetPlayerMoney()
        local sector64 = ConvertStringTo64Bit(tostring(sector_id or 0))
        local ship_pos = component_position(ship64)
        local stationtable = {}
        if sector64 ~= 0 then
            stationtable = GetContainedStations(sector64, true) or {}
        end

        local stations = {}
        local scanned = 0
        local included = 0
        local total_offers = 0
        for _, station in ipairs(stationtable) do
            if included >= STATION_CAP or total_offers >= TOTAL_OFFER_CAP then
                break
            end
            scanned = scanned + 1
            local station64 = ConvertStringTo64Bit(tostring(station))
            local station_name, isdock, canhavetradeoffers, isradarvisible, factionname, owner, idcode = GetComponentData(station64, "name", "isdock", "canhavetradeoffers", "isradarvisible", "factionname", "owner", "idcode")
            if isdock and canhavetradeoffers then
                local station_pos = component_position(station64)
                local distance_m = distance_between(ship_pos, station_pos)
                local distance_km = distance_m and (distance_m / 1000) or nil
                local in_range = (isradarvisible == true) or (distance_m ~= nil and tonumber(radar_range_m) ~= nil and distance_m <= tonumber(radar_range_m))
                if in_range then
                    local offers = GetTradeList(ConvertStringToLuaID(tostring(station64)), ConvertStringToLuaID(tostring(ship_id))) or {}
                    local capped_offers, offer_count = limited_offers(offers, station64, station_name, sector64, distance_m, distance_km)
                    if #capped_offers > 0 and total_offers < TOTAL_OFFER_CAP then
                        local remaining = TOTAL_OFFER_CAP - total_offers
                        if #capped_offers > remaining then
                            local trimmed = {}
                            for i = 1, remaining do
                                table.insert(trimmed, capped_offers[i])
                            end
                            capped_offers = trimmed
                        end
                        total_offers = total_offers + #capped_offers
                        included = included + 1
                        table.insert(stations, {
                            id = tostring(station64),
                            name = station_name,
                            idcode = idcode,
                            factionname = factionname,
                            owner = owner,
                            sectorid = tostring(sector64),
                            distance_m = distance_m,
                            distance_km = distance_km,
                            distance_source = station_pos and station_pos.source or nil,
                            isradarvisible = isradarvisible,
                            offers_raw = capped_offers,
                            offer_count = offer_count,
                            offer_cap = OFFERS_PER_STATION_CAP
                        })
                    end
                end
            end
        end

        return "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"trade_in_sector",'
            .. '"source":"x4_lua_live_pipe",'
            .. '"schema":"trade_offers_radar_v1",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"player_id":' .. json_string(tostring(player_id)) .. ','
            .. '"ship_id":' .. json_string(tostring(ship_id)) .. ','
            .. '"ship_name":' .. json_raw_string_or_null(ship_name) .. ','
            .. '"sector_raw":' .. json_raw_string_or_null(sector) .. ','
            .. '"sector_id":' .. json_raw_string_or_null(sector64 and tostring(sector64) or nil) .. ','
            .. '"player_money":' .. json_number_or_null(player_money) .. ','
            .. '"radar_range_m":' .. json_number_or_null(radar_range_m) .. ','
            .. '"distance_unit":"meters; distance_km derived by /1000",'
            .. '"station_cap":' .. tostring(STATION_CAP) .. ','
            .. '"offer_cap":' .. tostring(TOTAL_OFFER_CAP) .. ','
            .. '"offers_per_station_cap":' .. tostring(OFFERS_PER_STATION_CAP) .. ','
            .. '"station_count":' .. tostring(#stations) .. ','
            .. '"station_scan_count":' .. tostring(scanned) .. ','
            .. '"stations_raw":' .. json_value(stations)
            .. "}"
    end)

    if not ok then
        payload = "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"trade_in_sector",'
            .. '"source":"x4_lua_live_pipe",'
            .. '"schema":"trade_offers_radar_v1",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"error":' .. json_string(payload)
            .. "}"
    end

    DebugError("X4 LLM Copilot Lua radar trade payload: " .. payload)
    AddUITriggeredEvent("X4LLMCopilot", "AmbientRaw", payload)
end


local function emit_sector_objects(trigger, request_json)
    trigger = trigger or "unspecified"
    local ok, payload = pcall(function()
        local allowed = request_kinds(request_json)
        local player_id = C.GetPlayerID()
        local ship_id = C.GetPlayerOccupiedShipID()
        local ship64 = ConvertStringTo64Bit(tostring(ship_id))
        local ship_name, sector, sector_id = GetComponentData(ship64, "name", "sector", "sectorid")
        local player_money = GetPlayerMoney()
        local sector64 = ConvertStringTo64Bit(tostring(sector_id or 0))
        local ship_pos = component_position(ship64)
        local objects = {}
        local seen = {}
        local counts = { total = 0, station = 0, gate = 0, ship = 0, collectable = 0, wreck = 0 }
        local scan_errors = {}
        local source_counts = {}
        local raw_objects = {}
        local station_scan_count = 0
        local object_scan_count = 0

        if sector64 ~= 0 then
            local stationtable = GetContainedStations(sector64, true) or {}
            for _, station in ipairs(stationtable) do
                station_scan_count = station_scan_count + 1
                append_sector_object(objects, seen, counts, allowed, ConvertStringTo64Bit(tostring(station)), ship_pos, sector64, "station")
            end

            raw_objects, source_counts = contained_objects(sector64, scan_errors)
            for _, object in ipairs(raw_objects) do
                if counts.total >= SECTOR_OBJECT_TOTAL_CAP then
                    break
                end
                object_scan_count = object_scan_count + 1
                append_sector_object(objects, seen, counts, allowed, ConvertStringTo64Bit(tostring(object.id)), ship_pos, sector64, object.forced_kind)
            end
        end

        return "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"sector_objects",'
            .. '"source":"x4_lua_live_pipe",'
            .. '"schema":"sector_objects_v1",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"player_id":' .. json_string(tostring(player_id)) .. ','
            .. '"ship_id":' .. json_string(tostring(ship_id)) .. ','
            .. '"ship_name":' .. json_raw_string_or_null(ship_name) .. ','
            .. '"sector_raw":' .. json_raw_string_or_null(sector) .. ','
            .. '"sector_id":' .. json_raw_string_or_null(sector64 and tostring(sector64) or nil) .. ','
            .. '"player_money":' .. json_number_or_null(player_money) .. ','
            .. '"distance_unit":"meters_from_player_ship_position; distance_km derived by /1000",'
            .. '"object_cap":' .. tostring(SECTOR_OBJECT_TOTAL_CAP) .. ','
            .. '"kind_caps":' .. json_value(SECTOR_OBJECT_KIND_CAPS) .. ','
            .. '"requested_kinds":' .. json_value(allowed) .. ','
            .. '"object_count":' .. tostring(#objects) .. ','
            .. '"station_scan_count":' .. tostring(station_scan_count) .. ','
            .. '"object_scan_count":' .. tostring(object_scan_count) .. ','
            .. '"source_counts":' .. json_value(source_counts or {}) .. ','
            .. '"scan_errors":' .. json_value(scan_errors) .. ','
            .. '"objects_raw":' .. json_value(objects)
            .. "}"
    end)

    if not ok then
        payload = "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"sector_objects",'
            .. '"source":"x4_lua_live_pipe",'
            .. '"schema":"sector_objects_v1",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"error":' .. json_string(payload)
            .. "}"
    end

    DebugError("X4 LLM Copilot Lua sector objects payload: " .. payload)
    AddUITriggeredEvent("X4LLMCopilot", "AmbientRaw", payload)
end

local function emit_faction_state(trigger)
    trigger = trigger or "unspecified"
    local ok, payload = pcall(function()
        local factions = GetLibrary("factions") or {}
        local standings = {}
        for _, faction in ipairs(factions) do
            if faction.id and faction.id ~= "player" then
                local relation_ok, standing = pcall(function() return GetUIRelation(faction.id) end)
                local relation_name = nil
                local relation_color = nil
                local relation_raw_ok, relation_raw = pcall(function() return C.GetUIRelationName("player", faction.id) end)
                if relation_raw_ok and relation_raw ~= nil then
                    relation_name = ffi.string(relation_raw.name)
                    relation_color = ffi.string(relation_raw.colorid)
                end
                local shortname, isdiplomacyactive, isrelationlocked, relationlockshortreason = GetFactionData(faction.id, "shortname", "isdiplomacyactive", "isrelationlocked", "relationlockshortreason")
                local licences = {}
                local rank_title = nil
                local own_ok, own_licences = pcall(function() return GetOwnLicences(faction.id) end)
                if own_ok and type(own_licences) == "table" then
                    for _, licence in ipairs(own_licences) do
                        local item = {
                            type = licence.type,
                            name = licence.name,
                            isrank = (licence.type == "ceremonyfriend" or licence.type == "ceremonyally")
                        }
                        table.insert(licences, item)
                        if item.isrank and item.name and item.name ~= "" then
                            rank_title = item.name
                        end
                    end
                end
                table.insert(standings, {
                    faction = faction.id,
                    faction_name = faction.name,
                    faction_shortname = shortname or faction.shortname,
                    standing = relation_ok and standing or nil,
                    relation_name = relation_name,
                    relation_color = relation_color,
                    rank_title = rank_title,
                    licences_raw = licences,
                    isdiplomacyactive = isdiplomacyactive,
                    isrelationlocked = isrelationlocked,
                    relationlockshortreason = relationlockshortreason
                })
            end
        end

        local events = {}
        local event_defs = {}
        local defs_ok, event_count = pcall(function() return C.GetNumDiplomacyEvents() end)
        if defs_ok and event_count and event_count > 0 then
            local event_buf = ffi.new("DiplomacyEventInfo[?]", event_count)
            event_count = C.GetDiplomacyEvents(event_buf, event_count)
            for i = 0, event_count - 1 do
                local event_id = ffi.string(event_buf[i].id)
                event_defs[event_id] = {
                    id = event_id,
                    name = ffi.string(event_buf[i].name),
                    desc = ffi.string(event_buf[i].desc),
                    shortdesc = ffi.string(event_buf[i].shortdesc),
                    duration = event_buf[i].duration
                }
            end
        end
        local now_ok, now = pcall(function() return C.GetCurrentGameTime() end)
        local function append_event_operation(active)
            local count = C.GetNumDiplomacyEventOperations(active)
            if count and count > 0 then
                local op_buf = ffi.new("DiplomacyEventOperation[?]", count)
                count = C.GetDiplomacyEventOperations(op_buf, count, active)
                for i = 0, count - 1 do
                    local event_id = ffi.string(op_buf[i].eventid)
                    local def = event_defs[event_id] or {}
                    table.insert(events, {
                        kind = "diplomacy",
                        eventid = event_id,
                        event_name = def.name,
                        event_desc = def.shortdesc or def.desc,
                        operation_id = tostring(op_buf[i].id),
                        source_operation_id = tostring(op_buf[i].sourceactionoperationid),
                        faction = ffi.string(op_buf[i].faction),
                        otherfaction = ffi.string(op_buf[i].otherfaction),
                        option = ffi.string(op_buf[i].option),
                        outcome = ffi.string(op_buf[i].outcome),
                        agentname = (op_buf[i].agentid ~= 0) and ffi.string(C.GetComponentName(op_buf[i].agentid)) or ffi.string(op_buf[i].agentname),
                        agentresultstate = ffi.string(op_buf[i].agentresultstate),
                        starttime = op_buf[i].starttime,
                        age_s = (now_ok and now) and (now - op_buf[i].starttime) or nil,
                        read = op_buf[i].read,
                        active = active,
                        startrelation = op_buf[i].startrelation
                    })
                end
            end
        end
        local ops_ok, ops_error = pcall(function()
            append_event_operation(true)
            append_event_operation(false)
        end)
        if not ops_ok then
            table.insert(events, { kind = "event_read_error", summary = tostring(ops_error) })
        end

        return "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"faction_state",'
            .. '"source":"x4_lua_live_pipe",'
            .. '"schema":"faction_state_v1",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"standing_scale":"X4 UI relation integer, expected -30..+30",'
            .. '"standings_raw":' .. json_value(standings) .. ','
            .. '"events_raw":' .. json_value(events)
            .. "}"
    end)

    if not ok then
        payload = "{"
            .. '"type":"telemetry_raw",'
            .. '"intent":"faction_state",'
            .. '"source":"x4_lua_live_pipe",'
            .. '"schema":"faction_state_v1",'
            .. '"trigger":' .. json_string(trigger) .. ','
            .. '"error":' .. json_string(payload)
            .. "}"
    end

    DebugError("X4 LLM Copilot Lua faction payload: " .. payload)
    AddUITriggeredEvent("X4LLMCopilot", "AmbientRaw", payload)
end

local function emit_trade(trigger, scope)
    if scope == "radar_range" then
        emit_trade_radar(trigger)
    else
        emit_trade_docked(trigger)
    end
end

local function request_intent(request_json)
    request_json = tostring(request_json or "")
    local intent = string.match(request_json, '"intent"%s*:%s*"([^"]+)"')
    return intent or "ambient_context"
end

local chat_sequence = 0
local pending_chat = {}

local function json_field(message, field)
    message = tostring(message or "")
    return string.match(message, '"' .. field .. '"%s*:%s*"(([^"\\]|\\.)*)"')
end

local function unescape_json_string(value)
    value = tostring(value or "")
    value = string.gsub(value, '\\n', '\n')
    value = string.gsub(value, '\
', '
')
    value = string.gsub(value, '\\t', '\t')
    value = string.gsub(value, '\\"', '"')
    value = string.gsub(value, '\\\\', '\\')
    return value
end

local function emit_chat_print(text)
    AddUITriggeredEvent("X4LLMCopilot", "ChatPrint", tostring(text or ""))
end

local function emit_chat_request(text)
    text = tostring(text or "")
    if text == "" then
        emit_chat_print("Hermes: empty question ignored.")
        return
    end
    chat_sequence = chat_sequence + 1
    local id = "x4chat-" .. tostring(chat_sequence)
    pending_chat[id] = true
    emit_chat_print("You [" .. id .. "]: " .. text)
    emit_chat_print("Hermes [" .. id .. "]: thinking...")
    AddUITriggeredEvent("X4LLMCopilot", "ChatPending", id)
    AddUITriggeredEvent("X4LLMCopilot", "ChatRequest", json_value({ type = "chat_request", id = id, text = text }))
end

local function handle_chat_response(message)
    local id = unescape_json_string(json_field(message, "id") or "")
    local text = unescape_json_string(json_field(message, "text") or "")
    local error_text = unescape_json_string(json_field(message, "error") or "")
    if id == "" then
        emit_chat_print("Hermes: malformed chat_response without id.")
        return
    end
    pending_chat[id] = nil
    if error_text ~= "" then
        emit_chat_print("Hermes [" .. id .. "] error: " .. error_text)
    elseif text ~= "" then
        emit_chat_print("Hermes [" .. id .. "]: " .. text)
    else
        emit_chat_print("Hermes [" .. id .. "] error: empty response.")
    end
end

local function handle_chat_timeout(id)
    id = tostring(id or "")
    if id ~= "" and pending_chat[id] then
        pending_chat[id] = nil
        emit_chat_print("Hermes [" .. id .. "] error: timed out waiting for Python/Hermes response.")
    end
end

local function chat_text_from_terms(terms)
    if type(terms) ~= "table" then
        return tostring(terms or "")
    end
    local parts = {}
    for index = 2, #terms do
        table.insert(parts, tostring(terms[index] or ""))
    end
    return table.concat(parts, " ")
end

function L.Init()
    DebugError("X4 LLM Copilot Lua ambient module initialized")
    RegisterEvent("x4LLMCopilotFetchAmbient", function(_, request_json)
        local msg_type = json_field(request_json, "type")
        if msg_type == "chat_response" then
            handle_chat_response(request_json)
            return
        end
        local intent = request_intent(request_json)
        if intent == "trade_in_sector" then
            emit_trade("fetch_response", request_scope(request_json))
        elseif intent == "faction_state" then
            emit_faction_state("fetch_response")
        elseif intent == "sector_objects" then
            emit_sector_objects("fetch_response", request_json)
        else
            emit_ambient("fetch_response")
        end
    end)
    RegisterEvent("x4LLMCopilotChatCommand", function(_, terms)
        emit_chat_request(chat_text_from_terms(terms))
    end)
    RegisterEvent("x4LLMCopilotChatTimeout", function(_, id)
        handle_chat_timeout(id)
    end)
end

Register_OnLoad_Init(L.Init, "extensions.x4_llm_copilot.ui.x4_llm_copilot.ambient")
Register_Require_Response("extensions.x4_llm_copilot.ui.x4_llm_copilot.ambient", L)

return L
